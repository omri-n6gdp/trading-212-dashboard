"""
Manual portfolio loader.

Reads ~/.jarvis/portfolio.json on startup and re-reads on file change.
Combines with yfinance / Alpaca live prices to produce equity rows.
Also publishes any `polymarket_positions` entries the operator declares.

Since T212 now has a real API (see adapters/trading212.py), the manual
file is reserved for things no API covers: positions on venues we don't
integrate, or explicit Polymarket positions for wallets not configured.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from watchfiles import awatch

from src.adapters.trading212 import merge_equities
from src.config import settings
from src.hub import Hub

log = logging.getLogger("jarvis.manual")


def load_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        log.exception("manual · failed to parse %s", path)
        return {}


def shape_rows(data: dict) -> list[dict]:
    """Convert manual entries to equity row shape with placeholder prices.

    Without a price feed wired (Phase 4 yfinance work), we display the
    cost basis as the current price so the row at least appears. Real
    P&L gets filled in once yfinance is plugged in.
    """
    rows: list[dict] = []
    for p in data.get("positions", []):
        shares = float(p.get("shares", 0) or 0)
        cost = float(p.get("cost_basis", 0) or 0)
        if shares == 0:
            continue
        rows.append({
            "ticker": p.get("ticker", "?"),
            "name": p.get("name", p.get("ticker", "")),
            "exchange": p.get("exchange", ""),
            "nav_pct": 0.0,  # filled in by metrics loop once total NAV known
            "day_pct": 0.0,
            "pnl_bps": 0,
            "spark": [],
            "_shares": shares,
            "_cost_basis": cost,
            "_currency": p.get("currency", "USD"),
        })
    return rows


def shape_polymarket(data: dict) -> list[dict]:
    rows: list[dict] = []
    for m in data.get("polymarket_positions", []):
        rows.append({
            "id": m.get("id"),
            "question": m.get("question", ""),
            "implied_pct": int(float(m.get("entry_price", 0) or 0) * 100),
            "volume_24h": "—",
            "resolves": "—",
            "position": {
                "side": m.get("side", "YES"),
                "entry": float(m.get("entry_price", 0) or 0),
                "pnl_pct": 0.0,
            },
        })
    return rows


def apply(hub: Hub, data: dict) -> None:
    rows = shape_rows(data)
    merge_equities(hub, "manual", rows)
    # If the operator declared explicit polymarket positions, merge them
    pm_manual = shape_polymarket(data)
    if pm_manual:
        existing = hub.state.get("polymarket", [])
        existing_ids = {m.get("id") for m in existing}
        combined = list(existing) + [m for m in pm_manual if m["id"] not in existing_ids]
        hub.replace("polymarket", combined)


async def run(hub: Hub) -> None:
    path = settings.portfolio_path
    if not path.exists():
        # Seed an empty file so the operator has something to edit
        example = Path(__file__).resolve().parent.parent.parent / "data" / "portfolio.example.json"
        if example.exists():
            path.write_text(example.read_text())
            log.info("manual · seeded %s from example", path)
        else:
            path.write_text('{"positions": [], "polymarket_positions": []}')

    log.info("manual · watching %s", path)
    apply(hub, load_file(path))

    try:
        async for _ in awatch(str(path.parent)):
            apply(hub, load_file(path))
            log.info("manual · reloaded %s", path.name)
    except Exception:
        log.exception("manual · watcher crashed")
