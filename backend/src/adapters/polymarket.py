"""
Polymarket adapter.

Polls the gamma-api markets endpoint every 30s, filters to a curated
watchlist of slugs/keywords, and pushes `polymarket.replace` with the
shape the UI expects.

Operator wallet positions: if `POLYMARKET_WALLET` is configured, also
fetches `https://data-api.polymarket.com/positions?user=…` and merges.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import httpx

from src.config import settings
from src.hub import Hub

log = logging.getLogger("jarvis.polymarket")

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
POSITIONS_URL = "https://data-api.polymarket.com/positions"

# Watchlist by keyword — the operator cares about space, Fed policy,
# crypto and Premier League football. Markets matching any keyword
# (case-insensitive) get displayed. Cap to 8 entries by 24h volume.
KEYWORDS = (
    "starship", "spacex", "artemis", "moon",
    "fed", "rate cut", "fomc",
    "btc", "bitcoin", "ethereum",
    "premier league", "manchester city", "arsenal", "liverpool",
)
MAX_MARKETS = 8


def fmt_money(v: float | None) -> str:
    if not v:
        return "$0"
    v = float(v)
    for suffix, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= scale:
            return f"${v / scale:.1f}{suffix}"
    return f"${v:.0f}"


def fmt_resolve_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d")
    except Exception:
        return iso[:10]


def matches_watchlist(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in KEYWORDS)


def parse_json_str(v):
    """Polymarket returns several fields as JSON-encoded strings."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


async def fetch_markets(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(
        GAMMA_MARKETS,
        params={"closed": "false", "active": "true", "limit": 100, "order": "volume24hr", "ascending": "false"},
    )
    r.raise_for_status()
    raw = r.json()
    # API may wrap in {"markets": [...]} or return a list directly
    return raw.get("markets", raw) if isinstance(raw, dict) else raw


async def fetch_positions(client: httpx.AsyncClient, wallet: str) -> dict[str, dict]:
    """Returns map of condition_id → {side, entry_price}."""
    try:
        r = await client.get(POSITIONS_URL, params={"user": wallet})
        r.raise_for_status()
        out: dict[str, dict] = {}
        for p in r.json():
            cid = p.get("conditionId") or p.get("condition_id")
            if not cid:
                continue
            out[cid] = {
                "side": "YES" if (p.get("outcome") or "").lower() == "yes" else "NO",
                "entry": float(p.get("avgPrice", 0) or 0),
                "size": float(p.get("size", 0) or 0),
            }
        return out
    except Exception:
        log.exception("polymarket · positions fetch failed")
        return {}


def shape(market: dict, positions: dict[str, dict]) -> dict:
    outcomes = parse_json_str(market.get("outcomes")) or []
    prices = parse_json_str(market.get("outcomePrices")) or []
    # YES probability: index of "Yes" in outcomes, default 0
    yes_idx = 0
    for i, o in enumerate(outcomes):
        if str(o).lower() == "yes":
            yes_idx = i
            break
    try:
        yes_prob = float(prices[yes_idx]) if prices else 0.0
    except (ValueError, IndexError):
        yes_prob = 0.0

    cid = market.get("conditionId") or market.get("id")
    pos = positions.get(cid)
    pos_obj = None
    if pos:
        cur = yes_prob if pos["side"] == "YES" else (1 - yes_prob)
        entry = pos["entry"] or cur
        pnl_pct = ((cur - entry) / entry) * 100 if entry else 0.0
        pos_obj = {"side": pos["side"], "entry": round(entry, 2), "pnl_pct": round(pnl_pct, 1)}

    return {
        "id": cid,
        "question": market.get("question", "")[:120],
        "implied_pct": round(yes_prob * 100),
        "volume_24h": fmt_money(market.get("volume24hr")),
        "resolves": fmt_resolve_date(market.get("endDate")),
        "position": pos_obj,
    }


async def run(hub: Hub) -> None:
    log.info("polymarket · starting")
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "claude-jarvis/0.1"}) as client:
        connected = False
        while True:
            try:
                markets = await fetch_markets(client)
                positions = await fetch_positions(client, settings.polymarket_wallet) if settings.polymarket_wallet else {}
                filtered = [m for m in markets if matches_watchlist(m.get("question", ""))][:MAX_MARKETS]
                # Fallback: if no watchlist matches, show top-volume markets
                if not filtered:
                    filtered = markets[:MAX_MARKETS]
                payload = [shape(m, positions) for m in filtered]
                hub.replace("polymarket", payload)
                if not connected:
                    hub.update("system", {"venues_connected": _bump_venues(hub, +1)})
                    connected = True
            except Exception:
                log.exception("polymarket · fetch failed")
                if connected:
                    hub.update("system", {"venues_connected": _bump_venues(hub, -1)})
                    connected = False
            await asyncio.sleep(30)


def _bump_venues(hub: Hub, delta: int) -> int:
    cur = hub.state.get("system", {}).get("venues_connected", 0)
    total = hub.state.get("system", {}).get("venues_total", 4)
    return max(0, min(total, cur + delta))
