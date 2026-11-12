"""
Correlation & diversification.

From a {ticker: daily-returns} map (already aligned to a common axis) and the
portfolio weights, builds the holdings correlation matrix, the most-correlated
and most-diversifying pairs, each holding's average correlation to the rest,
the effective number of holdings, and a marginal-diversification figure (how
much portfolio volatility changes if a holding is removed). Pure; uses numpy.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

TRADING_DAYS = 252


def _aligned_matrix(returns_by_ticker: dict[str, list[float]]) -> tuple[list[str], np.ndarray]:
    items = [(t, r) for t, r in returns_by_ticker.items() if r and len(r) >= 20]
    if len(items) < 2:
        return [t for t, _ in items], np.zeros((len(items), len(items)))
    m = min(len(r) for _, r in items)
    tickers = [t for t, _ in items]
    mat = np.array([r[-m:] for _, r in items], dtype=float)  # (n_tickers, m)
    return tickers, mat


def matrices(returns_by_ticker: dict[str, list[float]],
             weights_by_ticker: dict[str, float] | None = None,
             max_cells: int = 60) -> dict[str, Any]:
    tickers, mat = _aligned_matrix(returns_by_ticker)
    n = len(tickers)
    if n < 2:
        return {"tickers": [], "matrix": [], "pairs_corr": [], "pairs_div": [],
                "corr_to_portfolio": [], "effective_holdings": None, "marginal": []}

    corr = np.corrcoef(mat)
    corr = np.nan_to_num(corr, nan=0.0)

    # full matrix can be large; cap the heatmap to the top-weighted holdings
    order = list(range(n))
    if weights_by_ticker:
        order = sorted(order, key=lambda i: weights_by_ticker.get(tickers[i], 0.0), reverse=True)
    keep = order[:max_cells]
    keep_set = set(keep)
    htickers = [tickers[i] for i in keep]
    hmatrix = [[round(float(corr[i, j]), 2) for j in keep] for i in keep]

    # most correlated / most diversifying pairs (across all holdings)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((tickers[i], tickers[j], float(corr[i, j])))
    pairs_corr = sorted(pairs, key=lambda x: x[2], reverse=True)[:10]
    pairs_div = sorted(pairs, key=lambda x: x[2])[:10]
    fmt = lambda p: {"a": p[0], "b": p[1], "corr": round(p[2], 2)}

    # average correlation to the rest, per holding
    corr_to_port = []
    for i in range(n):
        others = [corr[i, j] for j in range(n) if j != i]
        corr_to_port.append({"ticker": tickers[i], "avg_corr": round(float(np.mean(others)), 2)})
    corr_to_port.sort(key=lambda x: x["avg_corr"], reverse=True)

    # effective number of holdings from weights (1 / sum w^2)
    eff = None
    w = None
    if weights_by_ticker:
        wv = np.array([max(0.0, weights_by_ticker.get(t, 0.0)) for t in tickers], dtype=float)
        if wv.sum() > 0:
            w = wv / wv.sum()
            hhi = float((w ** 2).sum())
            eff = round(1.0 / hhi, 1) if hhi else None

    # marginal diversification: portfolio vol with vs without each holding
    marginal = []
    if w is not None:
        port = w @ mat                                   # weighted daily portfolio returns
        base_vol = float(port.std(ddof=1)) * math.sqrt(TRADING_DAYS) * 100
        for i in range(n):
            if w[i] <= 0:
                continue
            w2 = w.copy()
            w2[i] = 0.0
            s = w2.sum()
            if s <= 0:
                continue
            w2 = w2 / s
            v = float((w2 @ mat).std(ddof=1)) * math.sqrt(TRADING_DAYS) * 100
            marginal.append({"ticker": tickers[i], "delta_vol_pct": round(v - base_vol, 3)})
        # negative delta => removing it lowers vol => it ADDS risk; positive => it diversifies
        marginal.sort(key=lambda x: x["delta_vol_pct"])

    return {
        "tickers": htickers,
        "matrix": hmatrix,
        "n_total": n,
        "pairs_corr": [fmt(p) for p in pairs_corr],
        "pairs_div": [fmt(p) for p in pairs_div],
        "corr_to_portfolio": corr_to_port,
        "effective_holdings": eff,
        "marginal": marginal[:15],
        "portfolio_vol_pct": round(float((w @ mat).std(ddof=1)) * math.sqrt(TRADING_DAYS) * 100, 2) if w is not None else None,
    }


__all__ = ["matrices"]
