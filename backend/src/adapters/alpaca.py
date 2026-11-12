"""
Alpaca paper adapter (READ-ONLY).

Pulls positions and account snapshot from the paper account every 15s,
plus portfolio history once at boot to seed the equity curve. Never
places orders; the project is observe-only.

Uses the official alpaca-py SDK if present; falls back to raw HTTP
otherwise. Streaming WS is left for a later phase — periodic REST is
plenty for a dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import httpx

from src.adapters.trading212 import merge_equities
from src.config import settings
from src.hub import Hub

log = logging.getLogger("jarvis.alpaca")


HEADERS = lambda: {
    "APCA-API-KEY-ID": settings.alpaca_api_key,
    "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
}


def init_db() -> None:
    settings.jarvis_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.db_path)) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS nav_snapshot (
                ts        TEXT PRIMARY KEY,
                nav_pct   REAL NOT NULL,
                source    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position_snapshot (
                ts        TEXT NOT NULL,
                symbol    TEXT NOT NULL,
                weight    REAL NOT NULL,
                side      TEXT,
                source    TEXT NOT NULL,
                PRIMARY KEY (ts, symbol)
            );
            """
        )
        con.commit()


def write_nav(ts: str, nav_pct: float, source: str = "alpaca") -> None:
    with closing(sqlite3.connect(settings.db_path)) as con:
        con.execute(
            "INSERT OR REPLACE INTO nav_snapshot (ts, nav_pct, source) VALUES (?, ?, ?)",
            (ts, nav_pct, source),
        )
        con.commit()


def write_positions(ts: str, positions: list[tuple[str, float, str]]) -> None:
    with closing(sqlite3.connect(settings.db_path)) as con:
        con.executemany(
            "INSERT OR REPLACE INTO position_snapshot (ts, symbol, weight, side, source) VALUES (?, ?, ?, ?, 'alpaca')",
            [(ts, sym, w, side) for sym, w, side in positions],
        )
        con.commit()


def shape_position(p: dict, total_equity: float) -> dict:
    qty = float(p.get("qty", 0) or 0)
    avg_entry = float(p.get("avg_entry_price", 0) or 0)
    current = float(p.get("current_price", 0) or 0)
    market_value = float(p.get("market_value", 0) or 0)
    change_today_pct = float(p.get("change_today", 0) or 0) * 100
    nav_pct = (market_value / total_equity * 100) if total_equity else 0
    pnl_pct = float(p.get("unrealized_plpc", 0) or 0) * 100
    pnl_bps = int(round(pnl_pct * 100))
    symbol = p.get("symbol", "")
    asset_class = p.get("asset_class", "us_equity")
    return {
        "ticker": symbol,
        "name": symbol,  # Alpaca doesn't return long name; could enrich later
        "exchange": p.get("exchange", "NASDAQ"),
        "asset_class": asset_class,
        "nav_pct": round(nav_pct, 2),
        "day_pct": round(change_today_pct, 2),
        "pnl_bps": pnl_bps,
        "spark": [],
        "_market_value": market_value,
        "_avg_entry": avg_entry,
        "_current": current,
        "_qty": qty,
    }


def shape_option(p: dict) -> dict:
    """Minimal option position shape; greeks/IV come from snapshot endpoint."""
    return {
        "occ_symbol": p.get("symbol", ""),
        "contract": _pretty_occ(p.get("symbol", "")),
        "quantity": int(float(p.get("qty", 0) or 0)),
        "side": "long" if float(p.get("qty", 0) or 0) >= 0 else "short",
        "label": None,
        "iv_pct": None,
        "iv_rank": None,
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "pnl_bps": int(round(float(p.get("unrealized_plpc", 0) or 0) * 10000)),
    }


def _pretty_occ(occ: str) -> str:
    """SPY250528C00528000 → 'SPY 28MAY 528C'."""
    if len(occ) < 15:
        return occ
    try:
        underlying = occ[:-15].strip()
        ymd = occ[-15:-9]  # YYMMDD
        cp = occ[-9]
        strike = int(occ[-8:]) / 1000
        from datetime import datetime as _dt
        d = _dt.strptime(ymd, "%y%m%d")
        month_lbl = d.strftime("%d%b").upper()
        return f"{underlying} {month_lbl} {strike:g}{cp}"
    except Exception:
        return occ


async def fetch_positions(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get("/v2/positions")
    r.raise_for_status()
    return r.json()


async def fetch_account(client: httpx.AsyncClient) -> dict:
    r = await client.get("/v2/account")
    r.raise_for_status()
    return r.json()


async def fetch_portfolio_history(client: httpx.AsyncClient) -> dict:
    r = await client.get(
        "/v2/account/portfolio/history",
        params={"period": "1M", "timeframe": "1H"},
    )
    r.raise_for_status()
    return r.json()


async def backfill_equity_curve(hub: Hub, client: httpx.AsyncClient) -> None:
    try:
        hist = await fetch_portfolio_history(client)
        timestamps = hist.get("timestamp", [])
        equity = hist.get("equity", [])
        base_value = hist.get("base_value") or (equity[0] if equity else 0)
        if not base_value:
            return
        points = []
        for ts, eq in zip(timestamps, equity, strict=False):
            if eq is None:
                continue
            pct = (eq / base_value) - 1
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            points.append({"ts": iso, "nav_pct": round(pct * 100, 4)})
            write_nav(iso, pct, source="alpaca")
        hub.update("equity_curve", {"points": points, "benchmark": []})
        log.info("alpaca · backfilled %d equity points", len(points))
    except Exception:
        log.exception("alpaca · portfolio history fetch failed")


async def run(hub: Hub) -> None:
    if not settings.alpaca_api_key or settings.alpaca_api_key.startswith("PK_REPLACE"):
        log.info("alpaca · API key not set, adapter idle")
        return

    init_db()
    log.info("alpaca · starting")

    base = settings.alpaca_base_url
    connected = False
    async with httpx.AsyncClient(base_url=base, headers=HEADERS(), timeout=15.0) as client:
        await backfill_equity_curve(hub, client)

        backoff = 5
        while True:
            try:
                account = await fetch_account(client)
                positions = await fetch_positions(client)

                total_equity = float(account.get("equity", 0) or 0)
                last_equity = float(account.get("last_equity", total_equity) or total_equity)
                session_pct = ((total_equity / last_equity) - 1) * 100 if last_equity else 0

                # Split equity vs option positions
                equity_rows: list[dict] = []
                options_rows: list[dict] = []
                pos_snap: list[tuple[str, float, str]] = []
                for p in positions:
                    asset_class = p.get("asset_class", "us_equity")
                    qty = float(p.get("qty", 0) or 0)
                    mv = float(p.get("market_value", 0) or 0)
                    weight = (mv / total_equity) if total_equity else 0
                    side = "LONG" if qty >= 0 else "SHORT"
                    pos_snap.append((p.get("symbol", ""), weight, side))
                    if asset_class == "us_option":
                        options_rows.append(shape_option(p))
                    else:
                        equity_rows.append(shape_position(p, total_equity))

                # Persist NAV snapshot every poll cycle (~1/min)
                now_iso = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
                # NAV % vs base = use last_equity → today's session change as a rough proxy
                # The full historical series is provided by backfill; here we just keep it live.
                write_nav(now_iso, session_pct / 100, source="alpaca")
                write_positions(now_iso, pos_snap)

                merge_equities(hub, "alpaca", equity_rows)
                hub.replace("options", options_rows)
                hub.update("alpaca_account", {
                    "equity": total_equity,
                    "last_equity": last_equity,
                    "session_pct": round(session_pct, 4),
                    "cash": float(account.get("cash", 0) or 0),
                })
                hub.update("equity_curve", {"stats": {"session_pct": round(session_pct, 2)}})

                if not connected:
                    hub.update("system", {"venues_connected": _bump_venues(hub, +1)})
                    connected = True
                backoff = 5
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code in (401, 403):
                    log.error("alpaca · %s — bad key, adapter halting", code)
                    return
                log.warning("alpaca · HTTP %s: %s", code, e.response.text[:200])
            except Exception:
                log.exception("alpaca · poll failed")
                if connected:
                    hub.update("system", {"venues_connected": _bump_venues(hub, -1)})
                    connected = False
            await asyncio.sleep(15)


def _bump_venues(hub: Hub, delta: int) -> int:
    cur = hub.state.get("system", {}).get("venues_connected", 0)
    total = hub.state.get("system", {}).get("venues_total", 4)
    return max(0, min(total, cur + delta))
