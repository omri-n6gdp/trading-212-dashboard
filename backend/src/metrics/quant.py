"""
Quantitative analytics: Monte Carlo simulation, benchmark-relative metrics,
distribution diagnostics and return attribution.

All inputs are plain Python lists of daily simple returns; numpy is used for
the simulation. Everything is pure/deterministic given a seed so the numbers
are reproducible between refreshes.
"""
from __future__ import annotations

import math

import numpy as np

TRADING_DAYS = 252


def monte_carlo(daily_returns: list[float], current_value: float,
                horizon: int = 252, n_sims: int = 4000, seed: int = 7,
                drift_shrink: float = 0.45, drift_baseline: float = 0.05,
                drift_clip_lo: float = -0.35, drift_clip_hi: float = 0.40,
                gain_threshold_pct: float = 20.0, loss_threshold_pct: float = 20.0) -> dict:
    """Bootstrap (historical resampling) Monte Carlo of value, with a tamed drift.

    Resamples observed daily returns with replacement to build `n_sims` forward
    paths over `horizon` trading days, preserving the empirical distribution
    (fat tails, skew) rather than assuming normality.

    Raw recent returns of high-momentum names imply absurd compounding (a stock
    that tripled would be projected to triple again). So we recenter the drift:
    the per-path expected daily return is shifted to a target annual drift that
    SHRINKS the historical drift toward a modest baseline and is clipped to a
    sane band. Volatility and tail shape are preserved; only the mean is tamed.
    The assumption is reported back as `assumed_drift_pct` for transparency.
    """
    r = np.asarray([x for x in daily_returns if x is not None], dtype=float)
    if r.size < 20 or current_value <= 0:
        return {"insufficient": True}

    rng = np.random.default_rng(seed)
    mu_hist_annual = float(r.mean()) * TRADING_DAYS
    target_annual = drift_shrink * mu_hist_annual + (1.0 - drift_shrink) * drift_baseline
    target_annual = float(min(max(target_annual, drift_clip_lo), drift_clip_hi))
    adj = target_annual / TRADING_DAYS - float(r.mean())   # daily drift shift
    draws = rng.choice(r, size=(n_sims, horizon), replace=True) + adj
    paths = np.cumprod(1.0 + draws, axis=1)          # growth factor per day
    values = current_value * paths

    # Fan chart: weekly percentile bands (as % return from today)
    step = max(1, horizon // 52)
    idx = list(range(step - 1, horizon, step))
    if idx[-1] != horizon - 1:
        idx.append(horizon - 1)
    pct_levels = [5, 25, 50, 75, 95]
    fan = []
    for di in idx:
        col = values[:, di]
        row = {"d": di + 1}
        for p in pct_levels:
            row[f"p{p}"] = round((np.percentile(col, p) / current_value - 1) * 100, 2)
        fan.append(row)

    terminal = values[:, -1]
    tret = terminal / current_value - 1.0
    def vpc(p):  # value at percentile
        return round(float(np.percentile(terminal, p)), 2)
    def rpc(p):  # return % at percentile
        return round(float(np.percentile(tret, p)) * 100, 2)

    return {
        "horizon_days": horizon,
        "n_sims": n_sims,
        "fan": fan,
        "terminal": {
            "p5": vpc(5), "p25": vpc(25), "p50": vpc(50), "p75": vpc(75), "p95": vpc(95),
            "mean": round(float(terminal.mean()), 2),
        },
        "terminal_ret": {
            "p5": rpc(5), "p25": rpc(25), "p50": rpc(50), "p75": rpc(75), "p95": rpc(95),
            "mean": round(float(tret.mean()) * 100, 2),
        },
        "prob_profit": round(float((terminal > current_value).mean()) * 100, 1),
        "prob_gain_hi": round(float((tret > gain_threshold_pct / 100.0).mean()) * 100, 1),
        "prob_loss_hi": round(float((tret < -loss_threshold_pct / 100.0).mean()) * 100, 1),
        "gain_threshold_pct": gain_threshold_pct,
        "loss_threshold_pct": loss_threshold_pct,
        "prob_gain_20": round(float((tret > 0.20).mean()) * 100, 1),
        "prob_loss_20": round(float((tret < -0.20).mean()) * 100, 1),
        "var_95_1y": round((current_value - np.percentile(terminal, 5)), 2),
        "cvar_95_1y": round(current_value - float(terminal[terminal <= np.percentile(terminal, 5)].mean()), 2),
        "assumed_drift_pct": round(target_annual * 100, 1),
        "hist_drift_pct": round(mu_hist_annual * 100, 1),
        "current_value": round(current_value, 2),
    }


def mc_from_cfg(daily_returns: list[float], current_value: float,
                cfg: dict | None = None, n_sims: int | None = None) -> dict:
    """Run `monte_carlo` with settings pulled from the user config block."""
    cfg = cfg or {}
    return monte_carlo(
        daily_returns, current_value,
        horizon=int(cfg.get("horizon_days", 252)),
        n_sims=int(n_sims if n_sims is not None else cfg.get("n_sims", 4000)),
        seed=int(cfg.get("seed", 7)),
        drift_shrink=float(cfg.get("drift_shrink", 0.45)),
        drift_baseline=float(cfg.get("drift_baseline", 0.05)),
        drift_clip_lo=float(cfg.get("drift_clip_lo", -0.35)),
        drift_clip_hi=float(cfg.get("drift_clip_hi", 0.40)),
        gain_threshold_pct=float(cfg.get("gain_threshold_pct", 20)),
        loss_threshold_pct=float(cfg.get("loss_threshold_pct", 20)),
    )


def _align(a: list[float], b: list[float]) -> tuple[np.ndarray, np.ndarray]:
    m = min(len(a), len(b))
    return np.asarray(a[-m:], dtype=float), np.asarray(b[-m:], dtype=float)


def advanced_metrics(port_returns: list[float], bench_returns: list[float],
                     weights: list[float] | None = None) -> dict:
    """Benchmark-relative + distribution metrics from daily simple returns."""
    out: dict = {}
    rp = np.asarray([x for x in port_returns if x is not None], dtype=float)
    if rp.size < 5:
        return {"insufficient": True}

    # ---- distribution / standalone ----
    downside = rp[rp < 0]
    gains = rp[rp > 0]
    out["omega_ratio"] = round(float(gains.sum() / -downside.sum()), 2) if downside.sum() < 0 else None
    out["gain_to_pain"] = round(float(rp.sum() / -downside.sum()), 2) if downside.sum() < 0 else None
    p95, p5 = np.percentile(rp, 95), np.percentile(rp, 5)
    out["tail_ratio"] = round(float(abs(p95) / abs(p5)), 2) if p5 != 0 else None

    # Ulcer index (RMS drawdown of the equity curve implied by returns)
    eq = np.cumprod(1.0 + rp)
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1.0) * 100
    out["ulcer_index"] = round(float(math.sqrt(np.mean(dd ** 2))), 2)
    # Martin (UPI) ratio = annualised return / ulcer index
    ann_ret = (eq[-1] ** (TRADING_DAYS / len(rp)) - 1) * 100 if eq[-1] > 0 else 0.0
    out["martin_ratio"] = round(ann_ret / out["ulcer_index"], 2) if out["ulcer_index"] else None

    out["avg_win_pct"] = round(float(gains.mean()) * 100, 2) if gains.size else 0.0
    out["avg_loss_pct"] = round(float(downside.mean()) * 100, 2) if downside.size else 0.0
    out["win_loss_ratio"] = round(float(gains.mean() / -downside.mean()), 2) if downside.size and downside.mean() != 0 else None
    out["best_streak"], out["worst_streak"] = _streaks(rp)

    # ---- vs benchmark ----
    if bench_returns:
        rp2, rb = _align(port_returns, bench_returns)
        if rp2.size >= 5:
            active = rp2 - rb
            te = float(active.std(ddof=1)) * math.sqrt(TRADING_DAYS)
            out["tracking_error_pct"] = round(te * 100, 2)
            out["information_ratio"] = round(float(active.mean()) * TRADING_DAYS / te, 2) if te else None
            out["active_return_pct"] = round(float(active.mean()) * TRADING_DAYS * 100, 2)
            up = rb > 0
            dn = rb < 0
            out["up_capture_pct"] = round(float(rp2[up].sum() / rb[up].sum()) * 100, 1) if rb[up].sum() != 0 else None
            out["down_capture_pct"] = round(float(rp2[dn].sum() / rb[dn].sum()) * 100, 1) if rb[dn].sum() != 0 else None
            out["batting_avg_pct"] = round(float((active > 0).mean()) * 100, 1)

    # ---- concentration ----
    if weights:
        w = np.asarray([abs(x) for x in weights if x], dtype=float)
        if w.sum() > 0:
            w = w / w.sum()
            hhi = float((w ** 2).sum())
            out["effective_holdings"] = round(1.0 / hhi, 1) if hhi else None
            out["diversification_pct"] = round((1 - hhi) * 100, 1)
    return out


def _streaks(r: np.ndarray) -> tuple[int, int]:
    best = worst = cur = 0
    last = 0
    for x in r:
        s = 1 if x > 0 else (-1 if x < 0 else 0)
        if s == last and s != 0:
            cur += s
        else:
            cur = s
        last = s
        best = max(best, cur)
        worst = min(worst, cur)
    return int(best), int(abs(worst))


def attribution(holdings: list[dict]) -> list[dict]:
    """Per-holding P&L contribution, from the broker's realised+unrealised P&L
    (exact, currency-correct) rather than reconstructed returns."""
    out = []
    for h in holdings:
        ppl = h.get("ppl")
        if ppl is None:
            continue
        out.append({
            "ticker": h.get("ticker"),
            "name": h.get("name"),
            "ppl": round(ppl, 2),
            "return_pct": h.get("return_pct"),
            "nav_pct": h.get("nav_pct"),
        })
    out.sort(key=lambda x: x["ppl"], reverse=True)
    return out


__all__ = ["monte_carlo", "mc_from_cfg", "advanced_metrics", "attribution"]
