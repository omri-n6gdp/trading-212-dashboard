"""
yfinance adapter for LSE equities + benchmark.

Yahoo's unofficial API is rate-limited and flaky, so we poll every 30s
and tolerate the occasional miss. Pulls last price + day_pct for any
tickers in the merged equities slice tagged `LSE` plus the ACWI
benchmark (used by the equity curve).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.adapters.trading212 import merge_equities
from src.hub import Hub

log = logging.getLogger("jarvis.yfinance")

# LSE tickers seeded from the operator's stated holdings; extended by
# anything tagged `LSE` in the merged equities slice or manual portfolio.
DEFAULT_LSE = ["VWRL.L", "SMH.L", "SGLP.L", "VHYG.L", "VFEG.L"]
BENCHMARK = "ACWI"


def to_yahoo(ticker: str, exchange: str) -> str:
    if "." in ticker:
        return ticker
    if exchange == "LSE":
        return f"{ticker}.L"
    return ticker


async def fetch_prices(symbols: list[str]) -> dict[str, dict]:
    """Run yfinance in a thread (it's synchronous)."""
    import yfinance as yf

    def _fetch() -> dict[str, dict]:
        out: dict[str, dict] = {}
        try:
            data = yf.Tickers(" ".join(symbols))
            for sym in symbols:
                try:
                    info = data.tickers[sym].fast_info
                    last = float(info.last_price)
                    prev = float(info.previous_close)
                    day_pct = ((last - prev) / prev) * 100 if prev else 0
                    out[sym] = {"price": last, "day_pct": day_pct}
                except Exception:
                    continue
        except Exception:
            log.exception("yfinance · batch fetch failed")
        return out

    return await asyncio.to_thread(_fetch)


def gather_lse_tickers(hub: Hub) -> list[tuple[str, str]]:
    """Returns list of (display_ticker, yahoo_symbol) to fetch."""
    out: list[tuple[str, str]] = [(t.replace(".L", ""), t) for t in DEFAULT_LSE]
    sources = hub.state.get("_equities_sources", {})
    for src_rows in sources.values():
        for r in src_rows:
            if r.get("exchange") == "LSE":
                t = r["ticker"]
                ysym = to_yahoo(t, "LSE")
                if (t, ysym) not in out:
                    out.append((t, ysym))
    return out


async def run(hub: Hub) -> None:
    log.info("yfinance · starting")
    connected = False
    while True:
        try:
            pairs = gather_lse_tickers(hub)
            ysymbols = [y for _, y in pairs] + [BENCHMARK]
            prices = await fetch_prices(ysymbols)

            # Build/refresh LSE rows
            rows: list[dict] = []
            for disp, ysym in pairs:
                p = prices.get(ysym)
                if not p:
                    continue
                rows.append({
                    "ticker": disp,
                    "name": disp,
                    "exchange": "LSE",
                    "nav_pct": 0.0,  # owned weight comes from t212/manual sources
                    "day_pct": round(p["day_pct"], 2),
                    "pnl_bps": int(round(p["day_pct"] * 100)),
                    "spark": [],
                    "_price": p["price"],
                })
            merge_equities(hub, "yfinance", rows)

            # Benchmark for equity curve
            b = prices.get(BENCHMARK)
            if b:
                iso = datetime.now(timezone.utc).isoformat()
                hub.state.setdefault("equity_curve", {}).setdefault("benchmark", []).append(
                    {"ts": iso, "value_pct": round(b["day_pct"], 4)}
                )
                # Trim to last 240 points (matches the mock's window)
                bench = hub.state["equity_curve"]["benchmark"]
                del bench[:-240]

            if not connected:
                hub.update("system", {"venues_connected": _bump_venues(hub, +1)})
                connected = True
        except Exception:
            log.exception("yfinance · cycle failed")
            if connected:
                hub.update("system", {"venues_connected": _bump_venues(hub, -1)})
                connected = False
        await asyncio.sleep(30)


def _bump_venues(hub: Hub, delta: int) -> int:
    cur = hub.state.get("system", {}).get("venues_connected", 0)
    total = hub.state.get("system", {}).get("venues_total", 4)
    return max(0, min(total, cur + delta))
