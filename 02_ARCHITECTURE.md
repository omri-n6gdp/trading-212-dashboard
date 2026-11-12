# 02 · Architecture

## Decisions made (don't re-litigate)

- **Backend:** Python 3.11+, FastAPI, uvicorn, asyncio throughout.
- **Frontend:** the existing single-file HTML in `reference/`. No framework. No build step. Modify in place.
- **Transport:** WebSocket from backend → frontend for live data. REST for snapshot reads on page load.
- **State:** in-memory in the backend. SQLite for the NAV time series (so metrics survive restarts). No Postgres, no Redis.
- **Bind to localhost only.** No deployment, no auth.
- **One process.** uvicorn runs FastAPI; FastAPI runs background tasks for each adapter. No Celery, no separate workers.
- **Packaging:** uv (preferred) or pip. `pyproject.toml` is the source of truth.

## Topology

```
┌────────────────────────────────────────────────────────────────┐
│ Browser: claude-jarvis-mock.html (single-page)                 │
│                                                                │
│   on load → GET /api/snapshot      (full state)                │
│   then    → WS  /ws/stream         (live deltas)               │
└────────────────────────────────────────────────────────────────┘
                            ▲
                            │  JSON over WS
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ FastAPI app (uvicorn, single process)                          │
│                                                                │
│  HTTP routes:                                                  │
│    GET  /api/snapshot     → full current state                 │
│    GET  /api/health                                            │
│    GET  /                 → serves the HTML (static)           │
│                                                                │
│  WebSocket:                                                    │
│    /ws/stream             → broadcasts deltas to all clients   │
│                                                                │
│  Background tasks (asyncio.create_task on startup):            │
│    - hyperliquid_adapter   (WS in, broadcast out)              │
│    - polymarket_adapter    (poll, broadcast out)               │
│    - alpaca_adapter        (WS in, broadcast out)              │
│    - equities_adapter      (poll yfinance, broadcast out)      │
│    - metrics_recompute     (every 30s, broadcast out)          │
│    - engine_log_tail       (tail JSON-lines file from algo     │
│                             engine, broadcast out)             │
└────────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
   Hyperliquid WS      Polymarket REST     Alpaca WS+REST
   (free, no auth)     (free, no auth)     (paper key)
                            │
                            ▼
                       yfinance / 
                       Yahoo unofficial
                       (free, rate-limited)
                            │
                            ▼
                       ~/.jarvis/portfolio.json
                       (manual entries for LSE)
                            │
                            ▼
                       ~/.jarvis/engine.jsonl
                       (operator's algo engine
                        writes fills here)
```

## The data hub

Single in-memory dict that holds the current state of every panel. All adapters write to it. A pub/sub pattern broadcasts changes over WebSocket.

```python
# pseudo-structure
state = {
    "crypto_perps":  {...},   # written by hyperliquid_adapter
    "polymarket":    {...},   # written by polymarket_adapter
    "equities":      {...},   # written by equities_adapter
    "alpaca":        {...},   # written by alpaca_adapter
    "options":       {...},   # written by alpaca_adapter (positions only)
    "strategies":    {...},   # written by engine_log_tail
    "tape":          [...],   # written by engine_log_tail (ring buffer)
    "signals":       [...],   # written by engine_log_tail (ring buffer)
    "metrics":       {...},   # written by metrics_recompute
    "exposure":      {...},   # written by metrics_recompute
    "correlation":   {...},   # written by metrics_recompute
    "system":        {...},   # uptime, latency, fill rate (self-measured)
}
```

When an adapter updates a slice of state, it pushes a delta:

```json
{ "type": "crypto_perps.update", "payload": { "BTC-PERP": { "mark": 68420.4, ... } } }
{ "type": "tape.append", "payload": { ...fill object... } }
{ "type": "metrics.update", "payload": { "sharpe": 2.31, "max_dd": -0.042, ... } }
```

The frontend already has handlers for each panel; they just need to be re-pointed from "random walk on a timer" to "listen for this `type` over WS." See `04_UI_CONTRACT.md` for the exact payload shapes per type.

## Background-task pattern

Each adapter is an `async def` that runs in a `while True:` loop. On exception, log and back off (exponential, capped at 30s). Never let an adapter take down the app — wrap the outer loop in try/except.

```python
async def hyperliquid_adapter(hub: Hub):
    backoff = 1
    while True:
        try:
            async with websockets.connect(URL) as ws:
                await ws.send(subscribe_msg)
                backoff = 1
                async for raw in ws:
                    msg = parse(raw)
                    hub.update("crypto_perps", msg)
        except Exception as e:
            log.exception("hyperliquid adapter dropped")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
```

## SQLite schema

One file, `~/.jarvis/state.db`. Two tables to start:

```sql
CREATE TABLE nav_snapshot (
    ts        TIMESTAMP PRIMARY KEY,  -- UTC, end of minute
    nav_pct   REAL NOT NULL,           -- cumulative return, e.g. 0.8942 = +89.42%
    source    TEXT NOT NULL            -- 'alpaca', 'manual', 'combined'
);

CREATE TABLE position_snapshot (
    ts        TIMESTAMP NOT NULL,
    symbol    TEXT NOT NULL,
    weight    REAL NOT NULL,           -- fraction of NAV
    side      TEXT,                    -- 'LONG' | 'SHORT' | NULL
    source    TEXT NOT NULL,
    PRIMARY KEY (ts, symbol)
);
```

NAV snapshots feed the equity curve and all return-based metrics. Position snapshots feed the correlation and exposure panels.

## Frontend wiring strategy

The mock has ~30 `setInterval` calls that each simulate one panel's updates. Each one becomes:

1. Delete the `setInterval` body (keep the function definition).
2. Register a handler for the corresponding WS message type.
3. Have the handler call the existing render function with real data.

Concrete example for the crypto panel:

```javascript
// BEFORE (in mock):
setInterval(() => {
    cryptos.forEach((c, i) => {
        c.mark = c.mark * (1 + rng(0.001));  // fake
        setText(`cr-mk-${i}`, fmtPrice(c.mark));
    });
}, 1100);

// AFTER:
ws.on('crypto_perps.update', (payload) => {
    Object.entries(payload).forEach(([sym, data]) => {
        const i = cryptos.findIndex(c => c.sym === sym);
        if (i < 0) return;
        cryptos[i] = { ...cryptos[i], ...data };
        setText(`cr-mk-${i}`, fmtPrice(cryptos[i].mark));
        // existing flash-up/flash-dn animations still fire
    });
});
```

Helpers `setText`, `flash`, `fmtPrice`, `fmtPct`, `fmtBps` are already in the mock — reuse them.

## What to do when an adapter has no data yet

Two options, pick one and be consistent:

1. **Hide the panel** behind a placeholder ("Awaiting feed · phase N").
2. **Keep the simulation** in place until the real adapter is wired.

I recommend (2) for visual continuity — Omri likes seeing motion. Add a small badge to the panel header (`SIM`) so it's obvious which panels are live vs simulated.

---

## Rebuild architecture (2026-05)

The backend grows from "publish current state" into a **portfolio analytics
engine**. Same single-process FastAPI + Hub model; new analytics modules write
their own state slices, and new REST endpoints expose them.

### New / changed modules

```
backend/src/
  config_user.py              # NEW: loads ~/.jarvis/jarvis.config.json (file-watched), merges over defaults
  adapters/
    fundamentals.py           # NEW: yfinance .info per ticker -> sector, industry, mktcap, P/E, yield, analyst target (cached)
  metrics/
    security.py               # NEW: per-stock quant + per-stock Monte Carlo + predicted profit
    ranking.py                # NEW: predicted-profit leaderboard, composite 0-100 score, flags, master table
    correlation.py            # NEW: holdings + pie correlation matrices, effective holdings, marginal diversification
    sectors.py                # NEW: rolls fundamentals into sector/thematic exposure + attribution + forward contribution
    performance.py            # EXTENDED: per-pie full stats block + per-pie Monte Carlo
    quant.py                  # EXTENDED: parametric (GBM) MC engine, target-prob, horizon param, path max-drawdown
```

### New Hub state slices

`fundamentals`, `security_analytics`, `sector_exposure`, `rankings`,
`correlation_full`, and a richer `performance_pies`. Plus a `config` slice that
echoes the (non-secret) effective personalization so the frontend can render
labels, benchmarks, theme, and notes.

### New REST endpoints (read-only)

```
GET /api/snapshot                  # unchanged: full current state (now includes new slices)
GET /api/config                    # effective personalization (no secrets)
GET /api/stock/{ticker}            # one holding: full analytics + MC + fundamentals + price series
GET /api/pie/{id}                  # one pie: full analytics + MC + slices + sector/geo breakdown
GET /api/sectors                   # sector + thematic exposure, attribution, forward contribution
GET /api/rankings?by=...           # ranking table (default by predicted_profit)
GET /api/correlation               # correlation matrix payloads
GET /api/export/{table}.csv        # CSV export of any table
```

Drill-down pages (`/api/stock/{ticker}`, `/api/pie/{id}`) compute on demand from
the cached price matrix so we are not holding 99 stocks' worth of analytics in
the snapshot at all times — the snapshot carries summaries; the detail endpoint
carries depth.

### Generalization

No ticker, pie name, currency, or amount is hardcoded. The account comes from
the API key; everything else personal comes from `config_user.py`. See
`08_PERSONALIZATION.md`. The `data/jarvis.config.example.json` template ships
with generic placeholders only.

### Frontend

Multi-page. Three interchangeable designs live under `backend/static/`
(`design-a/`, `design-b/`, `design-c/`), each a small set of HTML/CSS/JS files
reading the same `/api`. A launcher page at `/` lets Omri switch; the choice is
remembered via the `theme` field in the config. Designs differ in look and
interaction only — never in the data they show.
