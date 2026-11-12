"""
Analytics orchestrator (rebuild R2-R6).

Once per cycle it loads the cached price matrix (populated by the performance
task, so no extra yfinance calls) and computes + publishes:
  - `security_analytics`  per-holding full quant + predicted 1y profit
  - `sector_exposure`     sector / industry / thematic exposure + attribution
  - `rankings`            predicted-profit leaderboard, composite score, flags
  - `correlation_full`    correlation matrix + diversification
  - `config`              non-secret effective personalization (labels / theme)

It also keeps a module-level context (price matrix, dates, benchmark) so the
drill-down endpoints can compute one stock's or one pie's full detail on demand
without reloading anything.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src import config_user
from src.hub import Hub
from src.metrics import correlation, ranking, sectors, security
from src.metrics.performance import (
    FX_PAIRS,
    _align_dates,
    _series_on_dates,
    benchmark_curve,
    build_fx,
    get_price_matrix,
    load_instruments,
    needed_symbols,
    to_yahoo,
)

log = logging.getLogger("jarvis.analytics")

REFRESH_SECONDS = 20 * 60
ETF_SECTOR = "Funds / ETF"

_CTX: dict[str, Any] = {}


def _holdings_value(account: dict, positions: list[dict]) -> float:
    """Reliable holdings value: total - free cash (T212 per-position MV over-sums)."""
    tv = float(account.get("total_value", 0) or 0)
    fc = float(account.get("free_cash", 0) or 0)
    if tv > 0:
        return max(tv - fc, 0.0)
    return sum(float(p.get("market_value", 0) or 0) for p in positions)


def _sector_for(fund: dict, ptype: str) -> str:
    s = (fund or {}).get("sector")
    if s:
        return s
    if (ptype or "").upper() == "ETF" or (fund or {}).get("quote_type") == "ETF":
        return ETF_SECTOR
    return "Unknown"


async def _build_context(hub: Hub) -> dict | None:
    instr = load_instruments()
    positions = hub.state.get("t212_positions", [])
    if not positions:
        return None
    matrix = await get_price_matrix(needed_symbols(hub, instr))
    fx_syms = set(FX_PAIRS.values())
    if not (fx_syms & set(matrix.keys())):
        matrix.update(await get_price_matrix(fx_syms))
    dates = _align_dates({k: v for k, v in matrix.items() if k not in FX_PAIRS.values()})
    if not dates:
        return None
    fx = build_fx(matrix, dates)
    return {
        "instr": instr, "matrix": matrix, "dates": dates, "fx": fx,
        "bench": benchmark_curve(matrix, dates, fx),
        "positions": positions, "account": hub.state.get("t212_account", {}),
        "fundamentals": hub.state.get("fundamentals", {}), "built": time.time(),
    }


def _series_for(ctx: dict, raw_ticker: str):
    sym = to_yahoo(raw_ticker, ctx["instr"].get(raw_ticker, {}))
    series = ctx["matrix"].get(sym) if sym else None
    if not series:
        return sym, None
    return sym, _series_on_dates(series, ctx["dates"])


def _scale(ctx: dict) -> float:
    sum_mv = sum(float(p.get("market_value", 0) or 0) for p in ctx["positions"]) or 1.0
    return _holdings_value(ctx["account"], ctx["positions"]) / sum_mv if sum_mv else 1.0


def _compute_securities(ctx: dict) -> list[dict]:
    funds = ctx["fundamentals"]
    mc_cfg = config_user.monte_carlo()
    notes = config_user.notes()
    scale = _scale(ctx)
    out: list[dict] = []
    for p in ctx["positions"]:
        raw = p.get("raw_ticker", "")
        short = p.get("ticker")
        sym, px = _series_for(ctx, raw)
        mv = float(p.get("market_value", 0) or 0) * scale
        fund = funds.get(short, {})
        row = {
            "ticker": short, "name": p.get("name"), "raw_ticker": raw, "yahoo": sym,
            "exchange": p.get("exchange"), "geo": p.get("geo"), "type": p.get("type"),
            "currency": p.get("currency"), "nav_pct": p.get("nav_pct"),
            "return_pct": p.get("return_pct"), "ppl": p.get("ppl"),
            "market_value": round(mv, 2), "qty": p.get("qty"),
            "avg_price": p.get("avg_price"), "current_price": p.get("current_price"),
            "sector": _sector_for(fund, p.get("type")),
            "industry": (fund or {}).get("industry"),
            "trailing_pe": (fund or {}).get("trailing_pe"),
            "dividend_yield": (fund or {}).get("dividend_yield"),
            "market_cap": (fund or {}).get("market_cap"),
            "note": notes.get(short) or notes.get((short or "").upper()),
        }
        if px is not None:
            summ = security.analyze(px, ctx["dates"], ctx["bench"], mv, mc_cfg)
            if not summ.get("insufficient"):
                row.update(summ)
                up = security.analyst_upside(fund, summ.get("last_price"))
                row.update({"upside_pct": up["upside_pct"], "target_mean": up["target_mean"],
                            "num_analysts": up["num_analysts"], "recommendation": up["recommendation"]})
        out.append(row)
    out.sort(key=lambda r: r.get("nav_pct", 0) or 0, reverse=True)
    return out


def _returns_map(ctx: dict) -> dict[str, list[float]]:
    rm: dict[str, list[float]] = {}
    for p in ctx["positions"]:
        _, px = _series_for(ctx, p.get("raw_ticker", ""))
        if px is None:
            continue
        px = [v for v in px if v and v == v]  # drop NaN/0
        if len(px) < 20:
            continue
        rm[p.get("ticker")] = [px[i] / px[i - 1] - 1 for i in range(1, len(px)) if px[i - 1]]
    return rm


async def run(hub: Hub) -> None:
    log.info("analytics · starting")
    for _ in range(90):
        if hub.state.get("t212_positions"):
            break
        await asyncio.sleep(2)

    while True:
        try:
            ctx = await _build_context(hub)
            if not ctx:
                await asyncio.sleep(60)
                continue
            _CTX.clear()
            _CTX.update(ctx)

            securities = _compute_securities(ctx)
            hub.replace("security_analytics", securities)

            hub.replace("sector_exposure", sectors.aggregate(securities, config_user.thematic_tags()))

            rk = ranking.build(securities, config_user.score_weights())
            rk["portfolio_predicted_profit_1y"] = round(
                sum((s.get("predicted_profit_1y") or 0) for s in securities), 2)
            rk["holdings_value"] = round(_holdings_value(ctx["account"], ctx["positions"]), 2)
            hub.replace("rankings", rk)

            hub.replace("correlation_full", correlation.matrices(
                _returns_map(ctx), {s["ticker"]: (s.get("nav_pct") or 0) for s in securities}))

            cfg = config_user.public_config()
            cfg["account_currency"] = ctx["account"].get("currency", "GBP")
            cfg["resolved_currency"] = config_user.base_currency(ctx["account"].get("currency", "GBP"))
            hub.replace("config", cfg)

            fund_ready = sum(1 for v in ctx["fundamentals"].values() if v.get("sector"))
            log.info("analytics · published %d securities · sectors=%d · pred_profit=%.0f · fund_ready=%d",
                     len(securities), len(hub.state["sector_exposure"].get("by_sector", [])),
                     rk["portfolio_predicted_profit_1y"], fund_ready)
        except Exception:
            log.exception("analytics · cycle failed")
            await asyncio.sleep(60)
            continue

        # Adaptive wait: while sector/fundamentals data is still streaming in,
        # recompute within ~30s of new data arriving; otherwise settle to REFRESH.
        waited = 0
        while waited < REFRESH_SECONDS:
            await asyncio.sleep(30)
            waited += 30
            now_ready = sum(1 for v in hub.state.get("fundamentals", {}).values() if v.get("sector"))
            if now_ready != fund_ready:
                break


# ---------------------------------------------------------------------------
# On-demand drill-downs for the REST endpoints
# ---------------------------------------------------------------------------

def stock_detail(hub: Hub, ticker: str) -> dict:
    ctx = _CTX
    if not ctx:
        return {"error": "analytics warming up — retry shortly"}
    positions = ctx.get("positions", [])
    p = next((x for x in positions if (x.get("ticker") or "").upper() == ticker.upper()), None)
    if not p:
        return {"error": f"{ticker} not found in current holdings"}
    _, px = _series_for(ctx, p.get("raw_ticker", ""))
    if px is None:
        return {"error": f"no price history mapped for {ticker}"}
    mv = float(p.get("market_value", 0) or 0) * _scale(ctx)
    fund = ctx.get("fundamentals", {}).get(p.get("ticker"), {})
    det = security.detail(px, ctx["dates"], ctx["bench"], mv, config_user.monte_carlo())
    det["position"] = p
    det["fundamentals"] = fund
    det["analyst"] = security.analyst_upside(fund, (det.get("summary") or {}).get("last_price"))
    det["note"] = config_user.notes().get(p.get("ticker"))
    return det


def pie_detail(hub: Hub, pid: str) -> dict:
    pies = hub.state.get("t212_pies", [])
    perf = {str(p.get("id")): p for p in hub.state.get("performance_pies", [])}
    pie = next((x for x in pies if str(x.get("id")) == str(pid)), None)
    if not pie:
        return {"error": "pie not found"}
    funds = hub.state.get("fundamentals", {})
    slices = []
    for s in pie.get("slices", []):
        fund = funds.get(s.get("ticker"), {})
        slices.append({**s, "sector": fund.get("sector") or "Unknown",
                       "industry": fund.get("industry"), "full_name": fund.get("name") or s.get("ticker")})
    sect = sectors.aggregate(
        [{"ticker": s["ticker"], "market_value": s.get("value", 0), "ppl": s.get("result_abs", 0),
          "sector": s["sector"], "industry": s.get("industry"), "geo": "?", "predicted_profit_1y": None}
         for s in slices],
        config_user.thematic_tags(),
    )
    pf = perf.get(str(pie.get("id")), {})
    return {
        "pie": {k: pie.get(k) for k in ("id", "name", "current_value", "invested", "result_pct",
                                        "result_abs", "cash", "div_gained", "status", "progress")},
        "slices": slices,
        "curve": pf.get("curve", []),
        "stats": pf.get("stats", {}),
        "monte_carlo": pf.get("monte_carlo", {}),
        "sectors": sect,
    }


__all__ = ["run", "stock_detail", "pie_detail"]
