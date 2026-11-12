"""
Hyperliquid adapter.

WebSocket subscription for live mark prices (`allMids`), REST poll every
30s for funding rates, open interest and 24h change (`metaAndAssetCtxs`).

Pushes:
- `crypto_perps.update` deltas with mark / day_pct / funding_8h_pct / open_interest
- `crypto_perps.spark` with the 30-tick ring buffer per symbol
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque

import httpx
import websockets

from src.hub import Hub

log = logging.getLogger("jarvis.hyperliquid")

WS_URL = "wss://api.hyperliquid.xyz/ws"
INFO_URL = "https://api.hyperliquid.xyz/info"

# Display symbols (with -PERP suffix) → Hyperliquid coin name
SYMBOLS = {
    "BTC-PERP": "BTC",
    "ETH-PERP": "ETH",
    "SOL-PERP": "SOL",
    "HYPE-PERP": "HYPE",
    "DOGE-PERP": "DOGE",
    "BNB-PERP": "BNB",
}

# Reverse for fast lookup on incoming WS messages
COIN_TO_SYM = {v: k for k, v in SYMBOLS.items()}


def fmt_si(n: float) -> str:
    """Format a number as 2.41B / 482M / 184K."""
    if n is None:
        return "—"
    n = float(n)
    for suffix, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= scale:
            return f"{n / scale:.2f}{suffix}"
    return f"{n:.0f}"


class SparkBuffer:
    """Per-symbol ring buffer of recent marks for sparklines."""

    def __init__(self, length: int = 30) -> None:
        self.length = length
        self.buf: dict[str, deque[float]] = {s: deque(maxlen=length) for s in SYMBOLS}

    def push(self, sym: str, value: float) -> list[float]:
        d = self.buf.setdefault(sym, deque(maxlen=self.length))
        d.append(value)
        return list(d)


async def poll_meta(hub: Hub) -> None:
    """Poll metaAndAssetCtxs every 30s for funding/OI/24h px."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                r = await client.post(INFO_URL, json={"type": "metaAndAssetCtxs"})
                r.raise_for_status()
                data = r.json()
                universe = data[0]["universe"]
                ctxs = data[1]
                updates: dict = {}
                for asset, ctx in zip(universe, ctxs, strict=False):
                    coin = asset["name"]
                    sym = COIN_TO_SYM.get(coin)
                    if not sym:
                        continue
                    mark = float(ctx.get("markPx") or ctx.get("midPx") or 0)
                    prev = float(ctx.get("prevDayPx") or 0)
                    day_pct = ((mark / prev) - 1) * 100 if prev else 0.0
                    funding = float(ctx.get("funding") or 0)
                    open_interest = float(ctx.get("openInterest") or 0)
                    # Hyperliquid funding is hourly; UI shows 8h convention.
                    funding_8h_pct = funding * 8 * 100
                    # OI is in contract count; multiply by mark for $ notional.
                    oi_notional = open_interest * mark
                    updates[sym] = {
                        "day_pct": round(day_pct, 2),
                        "funding_8h_pct": round(funding_8h_pct, 4),
                        "open_interest": fmt_si(oi_notional),
                    }
                if updates:
                    _merge_perps(hub, updates)
            except Exception:
                log.exception("hyperliquid · meta poll failed")
            await asyncio.sleep(30)


def _merge_perps(hub: Hub, updates: dict) -> None:
    """Per-symbol merge so concurrent writers (allMids vs meta) don't clobber each other."""
    slice_ = hub.state.setdefault("crypto_perps", {})
    if not isinstance(slice_, dict):
        slice_ = {}
        hub.state["crypto_perps"] = slice_
    for sym, data in updates.items():
        cur = slice_.setdefault(sym, {})
        cur.update(data)
    hub.broadcast("crypto_perps.update", updates)


async def stream_mids(hub: Hub, spark: SparkBuffer) -> None:
    """WS loop. Subscribes to allMids; pushes mark updates + sparklines."""
    backoff = 1
    tick_counter = 0
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))
                log.info("hyperliquid · WS connected, subscribed to allMids")
                hub.update("system", {"venues_connected": _bump_venues(hub, +1)})
                backoff = 1
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("channel") != "allMids":
                        continue
                    mids = msg["data"]["mids"]
                    updates: dict = {}
                    for coin, price_str in mids.items():
                        sym = COIN_TO_SYM.get(coin)
                        if not sym:
                            continue
                        mark = float(price_str)
                        updates[sym] = {"mark": mark}
                        spark.push(sym, mark)
                    if updates:
                        _merge_perps(hub, updates)
                        tick_counter += 1
                        # Push sparklines every ~30 ticks (~7s at 250ms cadence)
                        if tick_counter % 30 == 0:
                            spark_payload = {
                                sym: list(spark.buf[sym]) for sym in SYMBOLS if spark.buf[sym]
                            }
                            hub.broadcast("crypto_perps.spark", spark_payload)
        except Exception as e:
            log.warning("hyperliquid · WS dropped: %s · retrying in %ds", e, backoff)
            hub.update("system", {"venues_connected": _bump_venues(hub, -1)})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def _bump_venues(hub: Hub, delta: int) -> int:
    """Clamp venue counter to [0, venues_total]."""
    cur = hub.state.get("system", {}).get("venues_connected", 0)
    total = hub.state.get("system", {}).get("venues_total", 4)
    return max(0, min(total, cur + delta))


async def run(hub: Hub) -> None:
    """Top-level adapter task."""
    log.info("hyperliquid · starting")
    spark = SparkBuffer()
    # Seed initial sparkline payload so the frontend can render before WS data arrives
    hub.state["crypto_perps"] = {sym: {"spark": []} for sym in SYMBOLS}
    await asyncio.gather(
        stream_mids(hub, spark),
        poll_meta(hub),
    )
