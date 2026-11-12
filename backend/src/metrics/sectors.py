"""
Sector, industry and thematic exposure.

Pure aggregation over an enriched holdings list (each holding already carries
market_value, ppl, predicted_profit_1y, geo, sector, industry). Produces total
exposure breakdowns, P&L and forward (predicted-profit) attribution, a
geography x sector matrix, and sector concentration.

ETFs/funds often have no `sector` from yfinance; the orchestrator labels those
"Funds / ETF" before calling in, so nothing is silently dropped.
"""
from __future__ import annotations

from typing import Any


def _group(holdings: list[dict], key: str) -> list[dict]:
    agg: dict[str, dict] = {}
    for h in holdings:
        k = h.get(key) or "Unknown"
        a = agg.setdefault(k, {"label": k, "value": 0.0, "ppl": 0.0, "predicted_profit": 0.0, "count": 0})
        a["value"] += float(h.get("market_value", 0) or 0)
        a["ppl"] += float(h.get("ppl", 0) or 0)
        pp = h.get("predicted_profit_1y")
        if pp is not None:
            a["predicted_profit"] += float(pp)
        a["count"] += 1
    total = sum(a["value"] for a in agg.values()) or 1.0
    out = []
    for a in agg.values():
        a["pct"] = round(a["value"] / total * 100, 2)
        a["value"] = round(a["value"], 2)
        a["ppl"] = round(a["ppl"], 2)
        a["predicted_profit"] = round(a["predicted_profit"], 2)
        out.append(a)
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


def aggregate(holdings: list[dict], themes_map: dict[str, list[str]] | None = None) -> dict[str, Any]:
    themes_map = themes_map or {}
    by_sector = _group(holdings, "sector")
    by_industry = _group(holdings, "industry")
    by_geo = _group(holdings, "geo")
    total = sum(float(h.get("market_value", 0) or 0) for h in holdings) or 1.0

    # thematic exposure
    themes: list[dict] = []
    for theme, tickers in themes_map.items():
        tset = {str(t).upper() for t in (tickers or [])}
        members = [h for h in holdings if str(h.get("ticker", "")).upper() in tset]
        val = sum(float(h.get("market_value", 0) or 0) for h in members)
        if val <= 0:
            continue
        themes.append({
            "label": theme,
            "value": round(val, 2),
            "pct": round(val / total * 100, 2),
            "ppl": round(sum(float(h.get("ppl", 0) or 0) for h in members), 2),
            "predicted_profit": round(sum(float(h.get("predicted_profit_1y") or 0) for h in members), 2),
            "tickers": [h.get("ticker") for h in members],
        })
    themes.sort(key=lambda x: x["value"], reverse=True)

    # geography x sector matrix
    geos = [g["label"] for g in by_geo]
    secs = [s["label"] for s in by_sector]
    cell: dict[tuple, float] = {}
    for h in holdings:
        g = h.get("geo") or "Unknown"
        s = h.get("sector") or "Unknown"
        cell[(g, s)] = cell.get((g, s), 0.0) + float(h.get("market_value", 0) or 0)
    matrix = [[round(cell.get((g, s), 0.0), 2) for s in secs] for g in geos]

    # concentration (HHI over sectors, 0-10,000)
    hhi = sum((s["value"] / total) ** 2 for s in by_sector) * 10000 if total else 0.0

    return {
        "by_sector": by_sector,
        "by_industry": by_industry[:20],
        "by_geography": by_geo,
        "by_theme": themes,
        "geo_sector": {"geos": geos, "sectors": secs, "matrix": matrix},
        "sector_hhi": round(hhi, 1),
        "sector_count": len(by_sector),
        "total_value": round(total, 2),
    }


__all__ = ["aggregate"]
