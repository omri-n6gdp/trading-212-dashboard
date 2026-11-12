"""
Hub: single source of truth for current state, plus pub/sub to WS clients.

Adapters write into the state dict and call .broadcast(type, payload).
WS clients subscribe by getting their own asyncio.Queue.

Thread safety: not needed. Everything runs on the asyncio loop.
"""
from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("jarvis.hub")


# Starting state. Mirrors the UI contract in 04_UI_CONTRACT.md.
# Until adapters populate real values, these are placeholders that match
# the mock's visual defaults so the page doesn't render empty.
DEFAULT_STATE: dict[str, Any] = {
    "system": {
        "status": "ARMED",
        "latency_ms": 14,
        "fill_rate_pct": 99.4,
        "venues_connected": 0,
        "venues_total": 4,
        "uptime_seconds": 0,
        "strategies_active": 9,
        "lse_open": False,
    },
    "hero": {
        "mtd_pct": 0.0, "ytd_pct": 0.0, "itd_pct": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
        "win_rate_pct": 0.0, "profit_factor": 0.0,
        "trade_count": 0, "avg_trade_bps": 0,
        "max_dd_pct": 0.0, "current_dd_pct": 0.0,
        "var_95_pct": 0.0, "cvar_95_pct": 0.0, "vol_ann_pct": 0.0,
        "gross_exposure_pct": 0, "net_exposure_pct": 0,
        "leverage": 0.0, "cash_pct": 100,
    },
    "equity_curve": {"points": [], "benchmark": [], "stats": {}},
    "risk": {},
    "exposure": {"by_class": {}},
    "correlation": {"tickers": [], "matrix": []},
    "performance_overall": {"curve": [], "sp_equiv": [], "benchmark": [], "stats": {}},
    "performance_pies": [],
    "performance_holdings": [],
    "performance_quant": {"monte_carlo": {}, "metrics": {}, "attribution": []},
    # --- Rebuild (2026-05) analytics slices ---
    "fundamentals": {},
    "security_analytics": [],
    "sector_exposure": {},
    "rankings": {},
    "correlation_full": {},
    "config": {},
    "crypto_perps": {},
    "options": [],
    "polymarket": [],
    "equities": [],
    "strategies": [],
    "execution": {},
    "tape": [],
    "signals": [],
    "footer": {
        "node_id": "ldn-1-a3",
        "data_sources": [],
        "engine": "vectorbt-pro · fastapi",
        "mem_gb": 0.0, "cpu_pct": 0,
        "ws_connected": 0, "ws_total": 7,
        "mode": "PAPER",
    },
}


class Hub:
    def __init__(self) -> None:
        self.state: dict[str, Any] = deepcopy(DEFAULT_STATE)
        self.subscribers: set[asyncio.Queue] = set()

    # ------ Subscriber management ------

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    # ------ State mutation + broadcast ------

    def snapshot(self) -> dict[str, Any]:
        """Full state copy for /api/snapshot endpoint."""
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            **self.state,
        }

    def update(self, slice_name: str, payload: dict[str, Any]) -> None:
        """Merge payload into state[slice_name] (top-level only) and broadcast."""
        cur = self.state.setdefault(slice_name, {})
        if isinstance(cur, dict):
            cur.update(payload)
        else:
            self.state[slice_name] = payload
        self.broadcast(f"{slice_name}.update", payload)

    def replace(self, slice_name: str, payload: Any) -> None:
        """Full slice replacement + broadcast."""
        self.state[slice_name] = payload
        self.broadcast(f"{slice_name}.replace", payload)

    def append(self, slice_name: str, item: Any, max_len: int = 100) -> None:
        """Append to a list slice (ring buffer) + broadcast just the new item."""
        lst = self.state.setdefault(slice_name, [])
        if not isinstance(lst, list):
            raise TypeError(f"{slice_name} is not a list slice")
        lst.insert(0, item)
        del lst[max_len:]
        self.broadcast(f"{slice_name}.append", {"item": item})

    def broadcast(self, msg_type: str, payload: Any) -> None:
        """Fire-and-forget broadcast to all subscribers. Drops on full queue."""
        msg = {"type": msg_type, "payload": payload}
        dropped = 0
        for q in self.subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            log.warning("broadcast · dropped on %d slow subscribers · type=%s", dropped, msg_type)
