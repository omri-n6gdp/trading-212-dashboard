"""
Per-security analytics.

Full quant for a single holding from its price series, plus a per-stock Monte
Carlo and a predicted 1-year profit (in account currency, scaled to the held
market value). Pure functions over a price series — no I/O — so the same code
serves the all-holdings summary loop and the on-demand drill-down endpoint.

We reuse the portfolio stat engine in `performance.compute_stats` (Sharpe,
Sortino, drawdowns, beta/alpha vs benchmark, momentum, period returns, ...) by
wrapping the price series as a value curve, then add security-specific extras:
moving averages, 52-week range position, golden/death cross, analyst upside,
and the Monte Carlo predicted profit.
"""
from __future__ import annotations

import math
from typing import Any

from src.metrics import quant
from src.metrics.performance import compute_stats

SUMMARY_KEYS = (
    "total_return_pct", "cagr_pct", "vol_ann_pct", "downside_dev_pct",
    "sharpe", "sortino", "calmar", "max_dd_pct", "current_dd_pct", "max_dd_days",
    "var_95_pct", "var_99_pct", "cvar_95_pct", "skew", "kurtosis",
    "beta", "alpha_ann_pct", "correlation", "r_squared", "win_rate_pct",
    "ret_1w_pct", "ret_1m_pct", "ret_3m_pct", "ret_6m_pct", "ret_1y_pct",
    "momentum_12_1_pct", "best_day_pct", "worst_day_pct", "n_days",
)


def _clean(prices: list[float]) -> list[float]:
    return [p for p in prices if p is not None and not (isinstance(p, float) and math.isnan(p))]


def _ma(vals: list[float], n: int) -> float | None:
    return sum(vals[-n:]) / n if len(vals) >= n else None


def analyze(
    prices: list[float],
    dates: list[str],
    bench: list[dict],
    market_value: float,
    mc_cfg: dict | None = None,
    summary_sims: int = 1200,
) -> dict:
    """Summary analytics for one holding. `prices` aligned to `dates`."""
    px = _clean(prices)
    if len(px) < 20:
        return {"insufficient": True}

    dd = dates[-len(px):] if dates and len(dates) >= len(px) else [str(i) for i in range(len(px))]
    curve = [{"t": dd[i], "v": px[i]} for i in range(len(px))]
    stats = compute_stats(curve, bench)
    if stats.get("insufficient"):
        return {"insufficient": True}

    rets = [px[i] / px[i - 1] - 1 for i in range(1, len(px)) if px[i - 1]]
    mc_cfg = mc_cfg or {}

    pred_value = pred_profit = prob_profit = None
    if market_value and market_value > 0:
        mc = quant.mc_from_cfg(rets, market_value, mc_cfg, n_sims=summary_sims)
        if mc and not mc.get("insufficient"):
            pred_value = mc["terminal"]["mean"]
            pred_profit = round(pred_value - market_value, 2)
            prob_profit = mc["prob_profit"]

    ma50, ma200 = _ma(px, 50), _ma(px, 200)
    last = px[-1]
    window = px[-252:] if len(px) >= 2 else px
    hi, lo = max(window), min(window)
    range_pos = ((last - lo) / (hi - lo) * 100) if hi > lo else 50.0
    cross = ("golden" if ma50 > ma200 else "death") if (ma50 and ma200) else None

    out = {k: stats.get(k) for k in SUMMARY_KEYS}
    out.update({
        "last_price": round(last, 4),
        "ma50": round(ma50, 4) if ma50 else None,
        "ma200": round(ma200, 4) if ma200 else None,
        "ma_cross": cross,
        "above_ma50": bool(ma50 and last > ma50),
        "above_ma200": bool(ma200 and last > ma200),
        "range_52w_pct": round(range_pos, 1),
        "high_52w": round(hi, 4),
        "low_52w": round(lo, 4),
        "market_value": round(market_value, 2) if market_value else 0.0,
        "predicted_value_1y": round(pred_value, 2) if pred_value is not None else None,
        "predicted_profit_1y": pred_profit,
        "prob_profit": prob_profit,
    })
    return out


def analyst_upside(fund: dict, last_price: float | None) -> dict:
    """Implied upside to the mean analyst target (reference data, not advice)."""
    tgt = (fund or {}).get("target_mean")
    cur = (fund or {}).get("current_price") or last_price
    out = {"target_mean": tgt, "target_high": (fund or {}).get("target_high"),
           "target_low": (fund or {}).get("target_low"),
           "num_analysts": (fund or {}).get("num_analysts"),
           "recommendation": (fund or {}).get("recommendation"), "upside_pct": None}
    if tgt and cur and cur > 0:
        out["upside_pct"] = round((tgt / cur - 1) * 100, 1)
    return out


def histogram(rets: list[float], bins: int = 31) -> dict:
    """Daily-return histogram with a normal-curve overlay, for the distribution chart."""
    r = [x for x in rets if x is not None]
    if len(r) < 10:
        return {"bins": [], "normal": []}
    lo, hi = min(r), max(r)
    if hi <= lo:
        hi = lo + 1e-6
    width = (hi - lo) / bins
    counts = [0] * bins
    for x in r:
        i = min(bins - 1, int((x - lo) / width))
        counts[i] += 1
    n = len(r)
    mean = sum(r) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in r) / max(n - 1, 1)) or 1e-9
    out_bins, out_norm = [], []
    for i in range(bins):
        c = lo + (i + 0.5) * width
        out_bins.append({"x": round(c * 100, 3), "count": counts[i]})
        z = (c - mean) / std
        dens = math.exp(-0.5 * z * z) / (std * math.sqrt(2 * math.pi))
        out_norm.append({"x": round(c * 100, 3), "y": round(dens * width * n, 3)})
    return {"bins": out_bins, "normal": out_norm, "mean_pct": round(mean * 100, 4),
            "std_pct": round(std * 100, 4)}


def detail(
    prices: list[float],
    dates: list[str],
    bench: list[dict],
    market_value: float,
    mc_cfg: dict | None = None,
) -> dict:
    """Full drill-down payload: summary + rebased price curve + MA lines +
    return histogram + full Monte Carlo fan."""
    px = _clean(prices)
    if len(px) < 20:
        return {"insufficient": True}
    dd = dates[-len(px):] if dates and len(dates) >= len(px) else [str(i) for i in range(len(px))]

    summ = analyze(prices, dates, bench, market_value, mc_cfg, summary_sims=int((mc_cfg or {}).get("n_sims", 4000)))
    rets = [px[i] / px[i - 1] - 1 for i in range(1, len(px)) if px[i - 1]]
    base = px[0] or 1.0
    price_curve = [{"t": dd[i], "px": round(px[i], 4), "pct": round((px[i] / base - 1) * 100, 3)}
                   for i in range(len(px))]

    # rolling MA lines on the same axis
    def ma_line(n: int) -> list[dict]:
        out = []
        for i in range(len(px)):
            if i + 1 >= n:
                out.append({"t": dd[i], "v": round(sum(px[i + 1 - n:i + 1]) / n, 4)})
        return out

    mc = quant.mc_from_cfg(rets, market_value or px[-1], mc_cfg or {})
    return {
        "summary": summ,
        "price_curve": price_curve,
        "ma50_line": ma_line(50),
        "ma200_line": ma_line(200),
        "histogram": histogram(rets),
        "monte_carlo": mc,
    }


__all__ = ["analyze", "detail", "histogram", "analyst_upside", "SUMMARY_KEYS"]
