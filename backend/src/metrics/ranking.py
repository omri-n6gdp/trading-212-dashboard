"""
Ranking & scoring engine.

Given the per-holding analytics summaries, produces:
  - the headline leaderboard by predicted 1-year profit (account currency),
  - a composite 0-100 score per holding from transparent sub-scores
    (momentum, risk-adjusted, trend, low-volatility, value), blended with the
    weights from the user config,
  - risk and opportunity flags,
  - the master ranking table (every metric, frontend sorts it).

Sub-scores are percentile ranks across the current universe, so they are
relative ("how does this holding compare to the rest of my book") and bounded
0-100. Pure functions — no I/O.
"""
from __future__ import annotations

from typing import Any


def _num(v) -> float | None:
    """Coerce a possibly-stringy/None/NaN external value to float, else None.
    yfinance fundamentals occasionally return strings (e.g. 'Infinity') — never
    let that crash a comparison."""
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _percentile_ranks(values: list[float | None]) -> list[float | None]:
    """Map each value to its percentile (0-100) among the non-null values.
    Higher value -> higher percentile. Ties share the average rank."""
    idx = [i for i, v in enumerate(values) if v is not None]
    out: list[float | None] = [None] * len(values)
    if len(idx) < 2:
        for i in idx:
            out[i] = 50.0
        return out
    ordered = sorted(idx, key=lambda i: values[i])
    n = len(ordered)
    # average-rank for ties
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[ordered[j + 1]] == values[ordered[i]]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            out[ordered[k]] = round(avg_rank / (n - 1) * 100, 1)
        i = j + 1
    return out


def _inv(values: list[float | None]) -> list[float | None]:
    return [(-v if v is not None else None) for v in values]


def _trend_score(s: dict) -> float | None:
    parts = []
    if s.get("above_ma50") is not None:
        parts.append(100.0 if s.get("above_ma50") else 0.0)
    if s.get("above_ma200") is not None:
        parts.append(100.0 if s.get("above_ma200") else 0.0)
    if s.get("range_52w_pct") is not None:
        parts.append(float(s["range_52w_pct"]))
    if s.get("ma_cross"):
        parts.append(100.0 if s["ma_cross"] == "golden" else 0.0)
    return round(sum(parts) / len(parts), 1) if parts else None


def build(securities: list[dict], weights: dict | None = None) -> dict[str, Any]:
    """`securities`: enriched per-holding summaries. Returns scores + leaderboards + flags."""
    weights = weights or {"momentum": 0.25, "risk_adjusted": 0.25, "trend": 0.20,
                          "low_volatility": 0.15, "value": 0.15}
    n = len(securities)
    if n == 0:
        return {"scored": [], "by_predicted_profit": [], "by_score": [], "flags": {"risk": [], "opportunity": []}}

    mom = [_num(s.get("momentum_12_1_pct")) if s.get("momentum_12_1_pct") is not None
           else _num(s.get("ret_3m_pct")) for s in securities]
    sharpe = [_num(s.get("sharpe")) for s in securities]
    vol = [_num(s.get("vol_ann_pct")) for s in securities]
    upside = [_num(s.get("upside_pct")) for s in securities]

    mom_s = _percentile_ranks(mom)
    radj_s = _percentile_ranks(sharpe)
    lowvol_s = _percentile_ranks(_inv(vol))      # lower vol -> higher score
    value_s = _percentile_ranks(upside)          # more analyst upside -> higher score
    trend_s = [_trend_score(s) for s in securities]

    scored = []
    for i, s in enumerate(securities):
        subs = {
            "momentum": mom_s[i],
            "risk_adjusted": radj_s[i],
            "trend": trend_s[i],
            "low_volatility": lowvol_s[i],
            "value": value_s[i],
        }
        num = den = 0.0
        for k, w in weights.items():
            v = subs.get(k)
            if v is not None:
                num += w * v
                den += w
        score = round(num / den, 1) if den else None
        row = dict(s)
        row["subscores"] = {k: v for k, v in subs.items()}
        row["score"] = score
        scored.append(row)

    by_pp = sorted([r for r in scored if r.get("predicted_profit_1y") is not None],
                   key=lambda r: r["predicted_profit_1y"], reverse=True)
    by_score = sorted([r for r in scored if r.get("score") is not None],
                      key=lambda r: r["score"], reverse=True)

    flags = _flags(scored)
    return {
        "scored": scored,
        "by_predicted_profit": by_pp,
        "by_score": by_score,
        "flags": flags,
        "weights": weights,
    }


def _flags(scored: list[dict]) -> dict[str, list[dict]]:
    navs = sorted([_num(r.get("nav_pct")) or 0.0 for r in scored])
    median_nav = navs[len(navs) // 2] if navs else 0.0
    risk, opp = [], []
    for r in scored:
        t, name = r.get("ticker"), r.get("name")
        vol = _num(r.get("vol_ann_pct"))
        dd = _num(r.get("max_dd_pct"))
        m = _num(r.get("momentum_12_1_pct"))
        pe = _num(r.get("trailing_pe"))
        pp = _num(r.get("prob_profit"))
        up = _num(r.get("upside_pct"))
        sc = _num(r.get("score"))
        nav = _num(r.get("nav_pct")) or 0.0
        if vol is not None and vol > 60:
            risk.append({"ticker": t, "name": name, "type": "high_vol", "label": f"High volatility {vol:.0f}%"})
        if dd is not None and dd < -40:
            risk.append({"ticker": t, "name": name, "type": "deep_dd", "label": f"Deep drawdown {dd:.0f}%"})
        if m is not None and m < -10:
            risk.append({"ticker": t, "name": name, "type": "neg_mom", "label": f"Negative momentum {m:.0f}%"})
        if pe is not None and pe > 60:
            risk.append({"ticker": t, "name": name, "type": "expensive", "label": f"Rich valuation P/E {pe:.0f}"})
        if pp is not None and pp < 40:
            risk.append({"ticker": t, "name": name, "type": "low_winprob", "label": f"MC win prob {pp:.0f}%"})
        if sc is not None and sc >= 70 and nav < median_nav:
            opp.append({"ticker": t, "name": name, "type": "underweight", "label": f"High score {sc:.0f}, low weight {nav:.1f}%"})
        if m is not None and vol is not None and m > 20 and vol < 40:
            opp.append({"ticker": t, "name": name, "type": "momentum", "label": f"Momentum {m:.0f}% at {vol:.0f}% vol"})
        if up is not None and up > 25:
            opp.append({"ticker": t, "name": name, "type": "analyst_upside", "label": f"Analyst upside {up:.0f}%"})
    return {"risk": risk, "opportunity": opp}


__all__ = ["build"]
