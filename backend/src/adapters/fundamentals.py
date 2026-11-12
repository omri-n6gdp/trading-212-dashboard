"""
Fundamentals + sector reference adapter.

Per-ticker reference data from yfinance `.info`: sector, industry, market cap,
P/E, dividend yield, beta, analyst mean/high/low target, 52-week range,
recommendation. yfinance's `.info` is slow and occasionally flaky, so we:
  - run it in a worker thread,
  - fetch only symbols we don't already have fresh,
  - persist to ~/.jarvis/fundamentals.json with per-symbol timestamps,
  - publish progressively as data arrives.

Publishes hub slice `fundamentals`, keyed by the short ticker shown in the UI:
  { "RKLB": { "yahoo": "RKLB", "sector": "Industrials", "target_mean": 28.5, ... } }

This feeds per-stock analytics, sector/thematic exposure, and analyst upside.
It is reference data only and never used to place orders.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from src.config import settings
from src.hub import Hub
from src.metrics.performance import load_instruments, to_yahoo

log = logging.getLogger("jarvis.fundamentals")

CACHE = settings.jarvis_dir / "fundamentals.json"
TTL = 7 * 24 * 3600          # refresh reference data weekly
REFRESH_SECONDS = 6 * 3600   # re-scan for new/stale tickers every 6h


def _load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            return {}
    return {}


def _save_cache(data: dict) -> None:
    try:
        CACHE.write_text(json.dumps(data))
    except Exception:
        log.exception("fundamentals · cache write failed")


def _fetch_one_sync(sym: str) -> dict | None:
    """Pull one ticker's reference data. Runs in a worker thread."""
    import yfinance as yf

    try:
        t = yf.Ticker(sym)
        try:
            info = t.get_info()
        except Exception:
            info = getattr(t, "info", {}) or {}
        if not info:
            return None
        dy = info.get("dividendYield")
        # yfinance is inconsistent: sometimes a fraction (0.012), sometimes a percent (1.2)
        if dy is not None and dy > 1.5:
            dy = dy / 100.0
        return {
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "quote_type": info.get("quoteType"),
            "currency": info.get("currency"),
            "market_cap": info.get("marketCap"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "dividend_yield": dy,
            "beta": info.get("beta"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "target_mean": info.get("targetMeanPrice"),
            "target_high": info.get("targetHighPrice"),
            "target_low": info.get("targetLowPrice"),
            "num_analysts": info.get("numberOfAnalystOpinions"),
            "recommendation": info.get("recommendationKey"),
            "fifty_two_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_low": info.get("fiftyTwoWeekLow"),
        }
    except Exception:
        return None


def _discover(hub: Hub, instr: dict) -> dict[str, str]:
    """Map short ticker -> Yahoo symbol for everything held or in a pie."""
    out: dict[str, str] = {}
    for p in hub.state.get("t212_positions", []):
        raw = p.get("raw_ticker", "")
        sym = to_yahoo(raw, instr.get(raw, {}))
        if sym and p.get("ticker"):
            out[p["ticker"]] = sym
    for pie in hub.state.get("t212_pies", []):
        for s in pie.get("slices", []):
            raw = s.get("raw_ticker", "")
            sym = to_yahoo(raw, instr.get(raw, {}))
            if sym and s.get("ticker"):
                out.setdefault(s["ticker"], sym)
    return out


def _publish(hub: Hub, mapping: dict[str, str], cache: dict) -> None:
    out: dict[str, dict] = {}
    for short, yahoo in mapping.items():
        entry = cache.get(yahoo, {})
        data = entry.get("data") or {}
        out[short] = {"yahoo": yahoo, **data}
    hub.replace("fundamentals", out)


async def run(hub: Hub) -> None:
    log.info("fundamentals · starting")
    instr = load_instruments()
    cache = _load_cache()  # { yahoo: {"fetched": ts, "data": {...}} }

    # wait for the t212 adapter to populate positions
    for _ in range(60):
        if hub.state.get("t212_positions"):
            break
        await asyncio.sleep(2)
    if not instr:
        instr = load_instruments()

    while True:
        still_missing = False
        try:
            mapping = _discover(hub, instr)  # short -> yahoo
            _publish(hub, mapping, cache)    # publish whatever is already cached
            now = time.time()

            def resolved(y: str) -> bool:
                e = cache.get(y)
                if not e:
                    return False
                d = e.get("data") or {}
                if d.get("sector") or d.get("quote_type") == "ETF":
                    return (now - e.get("fetched", 0)) < TTL
                # no sector yet (likely a throttled/empty read): retry a few times
                return e.get("attempts", 0) >= 4

            todo = [y for y in sorted(set(mapping.values())) if not resolved(y)]
            if todo:
                log.info("fundamentals · fetching %d symbols", len(todo))
                for i, sym in enumerate(todo):
                    data = await asyncio.to_thread(_fetch_one_sync, sym)
                    prev = cache.get(sym, {})
                    cache[sym] = {
                        "fetched": time.time(),
                        "data": data or (prev.get("data") or {}),
                        "attempts": prev.get("attempts", 0) + 1,
                    }
                    if i % 10 == 0:
                        _save_cache(cache)
                        _publish(hub, mapping, cache)
                    await asyncio.sleep(0.8)  # gentle on Yahoo to avoid throttling
                _save_cache(cache)
                _publish(hub, mapping, cache)
                done = sum(1 for y in set(mapping.values()) if resolved(y))
                log.info("fundamentals · published %d tickers (resolved %d)", len(mapping), done)
            still_missing = any(not resolved(y) for y in set(mapping.values()))
        except Exception:
            log.exception("fundamentals · cycle failed")
            still_missing = True
        # retry soon while data is still missing; otherwise settle to the slow TTL
        await asyncio.sleep(300 if still_missing else REFRESH_SECONDS)


__all__ = ["run"]
