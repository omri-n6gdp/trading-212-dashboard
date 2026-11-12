# 05 · Roadmap

> **2026-05 Rebuild roadmap (current).** The phases below the divider are the
> original Claude-Jarvis plan and are now mostly done/superseded. The rebuild
> plan is here. See `07_FEATURES.md` for the 100-feature target and
> `08_PERSONALIZATION.md` for the generalization contract.
>
> **Phase R0 · Personalization spine.** `src/config_user.py` loads
> `~/.jarvis/jarvis.config.json` (seeded from `data/jarvis.config.example.json`,
> file-watched). Everything personal/display routes through it. No hardcoded
> values anywhere.
>
> **Phase R1 · Sector & fundamentals adapter.** `src/adapters/fundamentals.py`
> fetches yfinance `.info` per held/known ticker (sector, industry, market cap,
> P/E, dividend yield, analyst target). Disk-cached, slow TTL. Publishes
> `fundamentals` + drives sector/thematic exposure.
>
> **Phase R2 · Per-stock analytics + Monte Carlo.** `src/metrics/security.py`
> computes, for every holding, full quant (vol, Sharpe, beta, drawdown,
> momentum, MAs, 52w range) + a per-stock Monte Carlo forward cone + predicted
> 1Y profit on the held quantity. Publishes `security_analytics`.
>
> **Phase R3 · Per-pie full quant + Monte Carlo.** Extend
> `src/metrics/performance.py` so each pie gets the same deep stats block as the
> whole portfolio plus its own Monte Carlo. Publishes richer `performance_pies`.
>
> **Phase R4 · Ranking / predicted-profit engine.** `src/metrics/ranking.py`
> builds the predicted-profit leaderboard (headline), the composite 0-100 score,
> risk/opportunity flags, and the master ranking table. Publishes `rankings`.
>
> **Phase R5 · Correlation & diversification.** `src/metrics/correlation.py`
> builds the holdings + pie correlation matrices, effective-holdings, marginal
> diversification. Publishes `correlation_full`.
>
> **Phase R6 · API surface + sector exposure.** New REST endpoints aggregate the
> above; `src/metrics/sectors.py` rolls fundamentals into total sector/thematic
> exposure + attribution. Publishes `sector_exposure`.
>
> **Phase R7 · Three frontend designs.** Multi-page (Overview · Pies · Holdings ·
> Sectors · Quant & Rankings) with per-pie and per-stock drill-downs. Three
> distinct looks over the same `/api`. Omri picks one.
>
> ---

Phases ordered by **leverage** (impact per hour of work). Stop after any phase if Omri is happy — every phase produces a usable system.

## Phase 0 · Bring up the scaffold

**Goal:** server runs, serves the mock HTML, opens an empty WebSocket. Mock still uses simulated data.

- Set up `uv` env, install deps from `pyproject.toml`.
- Implement `main.py`: FastAPI app, static file mount serving `claude-jarvis-mock.html` at `/`, WebSocket endpoint at `/ws/stream` that accepts connections and pings every 30s.
- Implement `GET /api/snapshot` returning a hardcoded snapshot matching `04_UI_CONTRACT.md`.
- Implement `Hub` class: in-memory state dict + `broadcast(type, payload)` method that sends to all connected WS clients.
- Verify by opening `http://localhost:8000` — mock should load and the WS should connect (check browser devtools).

**Done when:** browser loads dashboard from server, WS handshake completes, no console errors.

## Phase 1 · Free public data (Hyperliquid + Polymarket)

**Goal:** the crypto and polymarket panels go live. No keys needed.

- Implement `adapters/hyperliquid.py`. Subscribe to `allMids`. Poll `meta` every 30s for funding/OI. Push deltas via Hub.
- Frontend: rewrite the `setInterval` blocks for the crypto panel to be WS-driven. Map symbols, preserve flash animations.
- Implement `adapters/polymarket.py`. Poll `gamma-api.polymarket.com/markets` every 30s for the configured market list. Optionally connect to the WS for tighter updates. Push deltas via Hub.
- Frontend: replace polymarket simulation with WS handlers.

**Done when:** crypto marks track Hyperliquid in real time; Polymarket probabilities match the actual site. Refresh the page and the data is still there.

**Estimated effort:** half a day.

## Phase 2 · Alpaca paper account

**Goal:** real positions and equity snapshots from US accounts. Equity curve starts accumulating real history.

- `adapters/alpaca.py` connects via the official `alpaca-py` SDK.
- On startup: pull current positions, portfolio history (last 1Y if available), account state.
- Subscribe to the trade updates WS for fills.
- Subscribe to market data WS for US tickers in current positions + watchlist (RKLB, LEU, ESLT, etc.).
- Persist NAV snapshots to SQLite every minute.
- Frontend: rewire equities panel and the parts of the hero strip that come from real account data.

**Done when:** logging into Alpaca paper, taking a paper trade, and seeing it appear in Jarvis within a few seconds.

**Estimated effort:** one day.

## Phase 3 · Computed metrics

**Goal:** every number in the Risk panel and Hero strip is computed from real data, not a placeholder.

- Implement `metrics/returns.py`: pull NAV series from SQLite, compute Sharpe, Sortino, Calmar, max DD, current DD, VaR, CVaR, vol, skew, kurtosis.
- Implement `metrics/exposure.py`: from position snapshots, compute gross/net/cash, by-asset-class.
- Implement `metrics/correlation.py`: pull return matrix, compute Pearson correlation.
- Background task: `metrics_recompute` runs every 30s, updates Hub.

**Done when:** numbers in Risk panel match what you'd get running the same formulas in a Jupyter notebook against the same data.

**Estimated effort:** half a day. The math is one-liners; the data plumbing is the work.

## Phase 4 · LSE equities (yfinance) + manual portfolio

**Goal:** the Trading 212 ISA positions display alongside Alpaca positions.

- `adapters/manual_portfolio.py` reads `~/.jarvis/portfolio.json`. Format:
  ```json
  {
    "positions": [
      { "ticker": "VWRL", "exchange": "LSE", "shares": 145, "cost_basis": 98.12 },
      { "ticker": "SGLP", "exchange": "LSE", "shares": 23, "cost_basis": 12.41 }
    ]
  }
  ```
- File-watch with `watchfiles` so editing the JSON reloads positions live.
- `adapters/yfinance_adapter.py` polls Yahoo every 15s for the LSE tickers' current prices. Combines with manual position data to produce NAV % and P&L.
- Merge into the same `equities` slice as Alpaca positions — frontend shouldn't care about origin.

**Done when:** editing `portfolio.json` shows up in the dashboard within a few seconds.

**Estimated effort:** half a day.

## Phase 5 · Options (Alpaca chains + Greeks)

**Goal:** the options panel shows real positions with live Greeks.

- Extend `adapters/alpaca.py` to fetch positions in option contracts (Alpaca returns them in `/v2/positions` with `asset_class: "us_option"`).
- For each option position, fetch the snapshot for that contract from `/v1beta1/options/snapshots/{underlying}`.
- Map Alpaca Greek format to UI contract shape.
- IV rank: accumulate daily IV samples to a new SQLite table; compute rank once 30 days of history exist.

**Done when:** Alpaca paper option positions appear in panel with correct deltas.

**Estimated effort:** half a day if there are positions; the IV rank history takes 30 calendar days to be useful.

## Phase 6 · Algo engine integration

**Goal:** strategy P&L, order tape, signal log, and execution quality go live.

- Define the JSON-lines protocol Omri's engine will write. See `03_DATA_SOURCES.md` panels 09–12 for event shapes. Coordinate with Omri.
- Implement `adapters/engine_tail.py` that follows `~/.jarvis/engine.jsonl` (handle log rotation: if file shrinks, reopen from start).
- On each event: update relevant state slice, broadcast delta.

**Done when:** running a strategy in the algo engine produces fills that appear in Jarvis's tape within ~100ms.

**Estimated effort:** one day, including the engine-side integration.

## Phase 7 · Polish

Nice-to-haves once everything works:

- IV rank from a paid source (ORATS) if 30-day accumulation is too slow.
- Hyperliquid signed endpoint for actual perp positions (if Omri trades there).
- Hyperliquid WS for L2 order book on selected symbols.
- Email/Slack/Telegram alerts on RISK events.
- Export functions: daily P&L attribution PDF, monthly returns CSV.
- Strategy params editable from UI (writes to a config file the engine reads).

These are optional. Phase 6 ships a fully functional system.

---

## What to do first, concretely

1. `cd backend && uv sync` (or `pip install -e .`)
2. Read `06_SETUP.md`.
3. Implement Phase 0 to get the server up.
4. Implement Phase 1 (Hyperliquid + Polymarket) and stop. Hand back to Omri for review.

Don't implement everything before getting feedback. Each phase should land as a separate commit with the dashboard in a working state.
