"""
Full Trading 212 order-history backfill + holdings timeline.

The /equity/portfolio endpoint only gives *current* holdings, so to draw a
performance curve that reflects what was actually held at each point in
time we reconstruct quantities from the complete order history.

Method: a backward walk. We start from today's exact positions (ground
truth) and undo each fill going back in time:
    qty_i(date d) = qty_i(today) - net_signed_flow_i(orders after d)
A BUY adds shares (so undoing it subtracts), a SELL removes shares (undoing
adds them back). Fully-sold tickers reappear historically; the end of the
curve always reconciles to the real account because we anchor on today's
positions. If the history is incomplete the early period simply degrades to
"held constant", which is the most sensible fallback.

The whole order history is small (a few hundred orders) so we fetch it all,
cache to disk, and refresh on a slow TTL.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from src.adapters.trading212 import T212Client
from src.config import settings

log = logging.getLogger("jarvis.orders")

ORDERS_CACHE = settings.jarvis_dir / "t212_orders_full.json"
ORDERS_TTL = 12 * 3600


def _strip(path: str) -> str:
    return path.replace("/api/v0", "", 1) if path and path.startswith("/api/v0") else path


async def fetch_all_orders(client: T212Client, max_pages: int = 60) -> list[dict]:
    """Paginate the full order history (most-recent first)."""
    out: list[dict] = []
    path = "/equity/history/orders"
    params: dict | None = {"limit": 50}
    pages = 0
    while path and pages < max_pages:
        r = await client.client.get(path, params=params)
        if r.status_code == 429:
            await asyncio.sleep(15)
            continue
        r.raise_for_status()
        j = r.json()
        items = j.get("items", [])
        out += items
        pages += 1
        nxt = j.get("nextPagePath")
        if not nxt or not items:
            break
        path = _strip(nxt)
        params = None
        await asyncio.sleep(11)  # endpoint allows ~6/min
    log.info("orders · fetched %d orders across %d pages", len(out), pages)
    return out


def _load_cache() -> list[dict] | None:
    if not ORDERS_CACHE.exists():
        return None
    if time.time() - ORDERS_CACHE.stat().st_mtime > ORDERS_TTL:
        return None
    try:
        return json.loads(ORDERS_CACHE.read_text()).get("orders")
    except Exception:
        return None


def _save_cache(shaped: list[dict]) -> None:
    try:
        ORDERS_CACHE.write_text(json.dumps({"fetched": time.time(), "orders": shaped}))
    except Exception:
        log.exception("orders · cache write failed")


async def get_orders() -> list[dict]:
    """Return shaped orders (cached). Each: raw_ticker, side, filled_qty, fill_price, filled_at."""
    cached = _load_cache()
    if cached is not None:
        return cached
    if not settings.trading212_api_key:
        return []
    client = T212Client(settings.trading212_api_key, settings.trading212_api_secret)
    try:
        raw = await fetch_all_orders(client)
    except Exception:
        log.exception("orders · fetch failed")
        # fall back to a stale cache if one exists
        if ORDERS_CACHE.exists():
            try:
                return json.loads(ORDERS_CACHE.read_text()).get("orders", [])
            except Exception:
                return []
        return []
    finally:
        await client.close()
    shaped = [s for s in (_shape(o) for o in raw) if s.get("raw_ticker") and s.get("filled_qty")]
    _save_cache(shaped)
    return shaped


def _shape(item: dict) -> dict:
    """Flatten a raw order-history item to the fields we need, incl. GBP cash impact."""
    order = item.get("order") or {}
    fill = item.get("fill") or {}
    wallet = fill.get("walletImpact") or {}
    return {
        "raw_ticker": order.get("ticker") or "",
        "side": order.get("side") or "",
        "filled_qty": float(fill.get("quantity") or 0),
        "fill_price": float(fill.get("price") or 0),
        "filled_at": fill.get("filledAt") or order.get("createdAt") or "",
        "currency": order.get("currency") or "GBP",
        # walletImpact.netValue is already in the account currency (GBP)
        "gbp_value": abs(float(wallet.get("netValue") or order.get("filledValue") or 0)),
    }


def daily_net_cashflow(orders: list[dict], dates: list[str]) -> list[float]:
    """Net GBP invested per axis date: +cost on BUY, -proceeds on SELL.

    Used to (a) neutralise cashflows when computing time-weighted returns and
    (b) build the 'S&P 500 with the same cashflows' comparison line.
    """
    by_day: dict[str, float] = defaultdict(float)
    for o in orders:
        g = float(o.get("gbp_value") or 0)
        if g == 0:
            continue
        day = (o.get("filled_at") or "")[:10]
        if not day:
            continue
        axis = _map_to_axis(day, dates)
        if axis is None:
            axis = dates[0]
        by_day[axis] += g if o.get("side") == "BUY" else -g
    return [round(by_day.get(d, 0.0), 4) for d in dates]


def build_quantity_timeline(
    current_positions: list[dict],
    orders: list[dict],
    dates: list[str],
) -> dict[str, list[float]]:
    """Return {raw_ticker: [qty at end of each date in `dates`]} via backward walk."""
    if not dates:
        return {}

    universe: set[str] = {p.get("raw_ticker") for p in current_positions if p.get("raw_ticker")}
    universe |= {o.get("raw_ticker") for o in orders if o.get("raw_ticker")}

    qty_today: dict[str, float] = defaultdict(float)
    for p in current_positions:
        rt = p.get("raw_ticker")
        if rt:
            qty_today[rt] = float(p.get("qty", 0) or 0)

    # net signed flow per axis-date per ticker (BUY +, SELL -)
    date_set = dates  # ascending
    flow: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for o in orders:
        rt = o.get("raw_ticker")
        q = float(o.get("filled_qty", 0) or 0)
        if not rt or q == 0:
            continue
        signed = q if (o.get("side") == "BUY") else -q
        day = (o.get("filled_at") or o.get("created_at") or "")[:10]
        if not day:
            continue
        # map order day to the latest axis date <= order day
        axis_day = _map_to_axis(day, date_set)
        if axis_day is None:
            axis_day = date_set[0]
        flow[axis_day][rt] += signed

    n = len(dates)
    out: dict[str, list[float]] = {t: [0.0] * n for t in universe}
    running: dict[str, float] = {t: qty_today.get(t, 0.0) for t in universe}
    for t in universe:
        out[t][n - 1] = max(0.0, running[t])
    for i in range(n - 1, 0, -1):
        day_flow = flow.get(dates[i], {})
        for t, f in day_flow.items():
            running[t] = running.get(t, 0.0) - f
        for t in universe:
            out[t][i - 1] = max(0.0, running.get(t, 0.0))
    return out


def _map_to_axis(day: str, axis: list[str]) -> str | None:
    """Largest axis date <= day (binary search)."""
    lo, hi = 0, len(axis) - 1
    res = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if axis[mid] <= day:
            res = axis[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return res


__all__ = ["get_orders", "build_quantity_timeline", "daily_net_cashflow"]
