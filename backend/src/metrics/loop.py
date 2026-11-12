"""
Metrics recompute loop.

Every 30s:
- Pulls NAV series from SQLite, computes hero/risk metrics.
- Computes exposure by asset class from the current `equities`,
  `crypto_perps`, `polymarket` and `options` slices.
- Publishes footer system stats (mem, cpu, uptime).

Until 30 days of NAV history accumulate, percentile-based metrics
(VaR, CVaR) fall back to last-N-points if shorter.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
import time
from contextlib import closing

import psutil

from src.config import settings
from src.hub import Hub

log = logging.getLogger("jarvis.metrics")

_START_TS = time.monotonic()


def read_nav_series() -> list[tuple[str, float]]:
    if not settings.db_path.exists():
        return []
    with closing(sqlite3.connect(settings.db_path)) as con:
        cur = con.execute("SELECT ts, nav_pct FROM nav_snapshot ORDER BY ts ASC")
        return list(cur.fetchall())


def daily_returns(series: list[tuple[str, float]]) -> list[float]:
    """Simple successive-tick returns; not strictly daily but close enough for the dashboard."""
    if len(series) < 2:
        return []
    out = []
    for i in range(1, len(series)):
        prev = series[i - 1][1]
        cur = series[i][1]
        if prev == 0:
            continue
        out.append(cur - prev)
    return out


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] * (hi - k) + s[hi] * (k - lo)


def compute_hero_and_risk(series: list[tuple[str, float]]) -> tuple[dict, dict]:
    rets = daily_returns(series)
    if not rets:
        return {}, {}

    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / max(n - 1, 1)
    std = math.sqrt(var)
    ann = math.sqrt(252)
    vol_ann = std * ann * 100

    # Sharpe / Sortino
    sharpe = (mean / std) * ann if std else 0.0
    neg = [r for r in rets if r < 0]
    downside = math.sqrt(sum(r * r for r in neg) / max(len(neg), 1)) if neg else 0
    sortino = (mean / downside) * ann if downside else 0.0

    # Max DD from cumulative nav
    cum = [s[1] for s in series]
    peak = cum[0]
    max_dd = 0.0
    cur_dd = 0.0
    for v in cum:
        if v > peak:
            peak = v
        dd = (v - peak)
        max_dd = min(max_dd, dd)
        cur_dd = dd
    calmar = (mean * 252) / abs(max_dd) if max_dd else 0.0

    var_95 = -percentile(rets, 5) * 100
    var_99 = -percentile(rets, 1) * 100
    tail = [r for r in rets if r <= percentile(rets, 5)]
    cvar_95 = -(sum(tail) / len(tail)) * 100 if tail else 0.0
    tail99 = [r for r in rets if r <= percentile(rets, 1)]
    es_99 = -(sum(tail99) / len(tail99)) * 100 if tail99 else 0.0

    skew = 0.0
    kurt = 3.0
    if std > 0:
        m3 = sum((r - mean) ** 3 for r in rets) / n
        m4 = sum((r - mean) ** 4 for r in rets) / n
        skew = m3 / (std ** 3)
        kurt = m4 / (std ** 4)

    # Period returns
    first = cum[0]
    last = cum[-1]
    itd = (last - first) * 100
    # MTD/YTD — approximate by slicing the recent history. Since `series`
    # is small at first, fall back to itd.
    mtd_idx = max(0, n - 30)
    ytd_idx = max(0, n - 252)
    mtd = (cum[-1] - cum[mtd_idx]) * 100
    ytd = (cum[-1] - cum[ytd_idx]) * 100

    hero = {
        "mtd_pct": round(mtd, 2),
        "ytd_pct": round(ytd, 2),
        "itd_pct": round(itd, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "current_dd_pct": round(cur_dd * 100, 2),
        "var_95_pct": round(var_95, 2),
        "cvar_95_pct": round(cvar_95, 2),
        "vol_ann_pct": round(vol_ann, 1),
    }
    risk = {
        "vol_ann_pct": round(vol_ann, 1),
        "skew": round(skew, 2),
        "kurtosis": round(kurt, 2),
        "var_99_pct": round(var_99, 2),
        "expected_shortfall_pct": round(es_99, 2),
    }
    return hero, risk


def compute_exposure(hub: Hub) -> dict:
    equities = hub.state.get("equities", [])
    crypto = hub.state.get("crypto_perps", {})
    pm = hub.state.get("polymarket", [])
    options = hub.state.get("options", [])

    eq_pct = sum(float(r.get("nav_pct", 0) or 0) for r in equities)
    # Crypto positions come from operator data; here approximate via the
    # `position` field on each perp
    cr_pct = 0.0
    for sym, data in crypto.items() if isinstance(crypto, dict) else []:
        pos = data.get("position") if isinstance(data, dict) else None
        if pos:
            cr_pct += abs(float(pos.get("nav_pct", 0) or 0))
    pm_pct = sum(abs(float(((p or {}).get("position") or {}).get("size_pct_nav", 0) or 0)) for p in pm)
    # Options: use abs(qty) * 1 as a placeholder until greeks arrive
    opt_pct = sum(abs(int(o.get("quantity", 0) or 0)) for o in options) * 0.1

    cash = max(0.0, 100 - eq_pct - cr_pct - pm_pct - opt_pct)
    return {
        "by_class": {
            "equities_etf": round(eq_pct, 1),
            "crypto_perps": round(cr_pct, 1),
            "options": round(opt_pct, 1),
            "polymarket": round(pm_pct, 1),
            "cash": round(cash, 1),
        }
    }


def system_telemetry(hub: Hub) -> None:
    proc = psutil.Process()
    mem_gb = proc.memory_info().rss / 1e9
    cpu = psutil.cpu_percent(interval=None)
    uptime = int(time.monotonic() - _START_TS)
    hub.update("system", {"uptime_seconds": uptime})
    hub.update("footer", {
        "mem_gb": round(mem_gb, 2),
        "cpu_pct": int(cpu),
        "ws_connected": len(hub.subscribers),
    })


async def run(hub: Hub) -> None:
    log.info("metrics · starting")
    while True:
        try:
            series = read_nav_series()
            hero, risk = compute_hero_and_risk(series)
            if hero:
                hub.update("hero", hero)
            if risk:
                hub.update("risk", risk)
            hub.update("exposure", compute_exposure(hub))
            system_telemetry(hub)
        except Exception:
            log.exception("metrics · cycle failed")
        await asyncio.sleep(30)
