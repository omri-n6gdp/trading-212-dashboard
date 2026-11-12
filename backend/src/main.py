"""
Claude Jarvis — FastAPI entry point.

Phase 0 scaffold. Serves the static dashboard, accepts WS connections,
exposes /api/snapshot. Adapters are wired in via the lifespan handler
as they get implemented (see roadmap).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.hub import Hub

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-5s %(name)s · %(message)s",
)
log = logging.getLogger("jarvis")

# Global hub. Single instance per process.
hub = Hub()


# ----------------------------------------------------------------------
# Lifespan: start/stop background tasks
# ----------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Start adapters on app startup. Each adapter runs as a long-lived
    asyncio task and is responsible for its own reconnection/backoff.

    Add adapter imports + task creates here as you implement them.
    """
    log.info("Jarvis booting · mode=%s", settings.mode)

    tasks: list[asyncio.Task] = []

    # --- Phase 1: free public data ---
    from src.adapters.hyperliquid import run as run_hyperliquid
    tasks.append(asyncio.create_task(run_hyperliquid(hub), name="hyperliquid"))

    from src.adapters.polymarket import run as run_polymarket
    tasks.append(asyncio.create_task(run_polymarket(hub), name="polymarket"))

    # --- Phase 2: Alpaca + Trading 212 (read-only) ---
    from src.adapters.alpaca import run as run_alpaca
    tasks.append(asyncio.create_task(run_alpaca(hub), name="alpaca"))

    from src.adapters.trading212 import run as run_t212
    tasks.append(asyncio.create_task(run_t212(hub), name="trading212"))

    # --- Phase 4: LSE prices + manual portfolio entries ---
    from src.adapters.yfinance_adapter import run as run_yf
    tasks.append(asyncio.create_task(run_yf(hub), name="yfinance"))

    from src.adapters.manual_portfolio import run as run_manual
    tasks.append(asyncio.create_task(run_manual(hub), name="manual"))

    # --- Phase 3: metrics recompute loop ---
    from src.metrics.loop import run as run_metrics
    tasks.append(asyncio.create_task(run_metrics(hub), name="metrics"))

    # --- Performance: reconstruct portfolio + pie curves from yfinance ---
    from src.metrics.performance import run as run_performance
    tasks.append(asyncio.create_task(run_performance(hub), name="performance"))

    # --- Rebuild R1: sector + fundamentals reference data (yfinance .info) ---
    from src.adapters.fundamentals import run as run_fundamentals
    tasks.append(asyncio.create_task(run_fundamentals(hub), name="fundamentals"))

    # --- Rebuild R2-R6: analytics engine (per-stock, sectors, rankings, correlation) ---
    from src.metrics.analytics import run as run_analytics
    tasks.append(asyncio.create_task(run_analytics(hub), name="analytics"))

    # --- Phase 6: algo engine tail --- (TODO: wire when engine writes JSONL)
    # from src.adapters.engine_tail import run as run_engine
    # tasks.append(asyncio.create_task(run_engine(hub), name="engine"))

    log.info("Started %d background tasks", len(tasks))
    try:
        yield
    finally:
        log.info("Shutting down · cancelling %d tasks", len(tasks))
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Claude Jarvis", lifespan=lifespan)


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent.parent / "static"


_NO_CACHE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def _serve(name: str):
    p = STATIC_DIR / name
    if not p.exists():
        fallback = STATIC_DIR / "launcher.html"
        if fallback.exists():
            return FileResponse(fallback, headers=_NO_CACHE)
        return JSONResponse({"error": f"{name} missing"}, status_code=404)
    return FileResponse(p, headers=_NO_CACHE)


@app.get("/")
async def index():
    """Design launcher — choose one of the three frontends."""
    return _serve("launcher.html")


@app.get("/a")
async def design_a():
    return _serve("jarvis-a.html")


@app.get("/b")
async def design_b():
    return _serve("jarvis-b.html")


@app.get("/c")
async def design_c():
    return _serve("jarvis-c.html")


@app.get("/legacy")
async def legacy():
    """The original single-page mock, kept for reference."""
    return _serve("claude-jarvis-mock.html")


@app.get("/api/snapshot")
async def snapshot():
    """Full current state. Frontend hits this on page load."""
    return hub.snapshot()


@app.get("/api/health")
async def health():
    return {"status": "ok", "subscribers": len(hub.subscribers)}


# ----------------------------------------------------------------------
# Rebuild (2026-05) analytics API
# ----------------------------------------------------------------------
@app.get("/api/config")
async def api_config():
    """Non-secret effective personalization (labels, benchmarks, theme, notes)."""
    from src import config_user
    return hub.state.get("config") or config_user.public_config()


@app.get("/api/securities")
async def api_securities():
    """Per-holding analytics summaries (the master ranking-table source)."""
    return hub.state.get("security_analytics", [])


@app.get("/api/rankings")
async def api_rankings():
    """Predicted-profit leaderboard, composite scores, and flags."""
    return hub.state.get("rankings", {})


@app.get("/api/sectors")
async def api_sectors():
    """Sector / industry / thematic exposure, attribution, geo x sector matrix."""
    return hub.state.get("sector_exposure", {})


@app.get("/api/correlation")
async def api_correlation():
    """Holdings correlation matrix + diversification stats."""
    return hub.state.get("correlation_full", {})


@app.get("/api/stock/{ticker}")
async def api_stock(ticker: str):
    """One holding: full analytics + Monte Carlo + price series + fundamentals."""
    from src.metrics import analytics
    return analytics.stock_detail(hub, ticker)


@app.get("/api/pie/{pid}")
async def api_pie(pid: str):
    """One pie: full analytics + Monte Carlo + slices + sector breakdown."""
    from src.metrics import analytics
    return analytics.pie_detail(hub, pid)


@app.post("/api/theme/{theme_id}")
async def api_theme(theme_id: str):
    """Persist the chosen frontend design id (the only UI state saved server-side)."""
    from src import config_user
    config_user.set_theme(theme_id)
    return {"ok": True, "theme": theme_id}


@app.get("/api/export/{table}.csv")
async def api_export(table: str):
    """CSV export of any table view."""
    import csv
    import io

    sources = {
        "securities": hub.state.get("security_analytics", []),
        "rankings": hub.state.get("rankings", {}).get("scored", []),
        "holdings": hub.state.get("performance_holdings", []),
        "positions": hub.state.get("t212_positions", []),
        "pies": hub.state.get("performance_pies", []),
        "dividends": hub.state.get("t212_dividends", []),
        "orders": hub.state.get("t212_orders", []),
    }
    rows = sources.get(table, [])
    if not rows:
        return PlainTextResponse("", media_type="text/csv")
    flat = [{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in rows]
    cols = sorted({k for r in flat for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerows(flat)
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )


@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    """Bidirectional WS. Server pushes deltas; client doesn't send anything yet."""
    await websocket.accept()
    queue = await hub.subscribe()
    log.info("ws · client connected · subscribers=%d", len(hub.subscribers))
    try:
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws · unexpected error")
    finally:
        hub.unsubscribe(queue)
        log.info("ws · client disconnected · subscribers=%d", len(hub.subscribers))


# Static asset directory if you ever add JS/CSS files separately
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
