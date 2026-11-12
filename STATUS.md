# Status

## Rebuild (2026-05-30) — analytics engine + three frontends

Major expansion. The dashboard is now a generalized quant-analytics terminal.
See `07_FEATURES.md` (100-feature catalogue) and `08_PERSONALIZATION.md`.

**New backend (all live, verified against the real account):**
- `src/config_user.py` — personalization loader (`~/.jarvis/jarvis.config.json`,
  seeded from `data/jarvis.config.example.json`). Nothing personal is hardcoded;
  any T212 key works.
- `src/adapters/fundamentals.py` — yfinance `.info` per ticker (sector, industry,
  market cap, P/E, yield, analyst target). Cached, throttle-resilient. → `fundamentals`.
- `src/metrics/security.py` — per-stock full quant + per-stock Monte Carlo +
  predicted 1Y profit. `metrics/sectors.py`, `metrics/ranking.py`,
  `metrics/correlation.py` — sector/thematic exposure, composite scoring +
  predicted-profit ranking + flags, correlation/diversification.
- `src/metrics/analytics.py` — orchestrator; publishes `security_analytics`,
  `sector_exposure`, `rankings`, `correlation_full`, `config`. Powers the
  `/api/stock/{t}` and `/api/pie/{id}` drill-downs.
- `src/metrics/performance.py` — extended: per-pie full stats + per-pie Monte
  Carlo + predicted profit + contribution + rebalance gap.
- `src/metrics/quant.py` — Monte Carlo now uses a **drift-tamed** model
  (shrink historical drift toward a baseline, clip to a sane band) so the
  predicted-profit headline is defensible, not a runaway compounding of momentum.

**New REST endpoints:** `/api/config`, `/api/securities`, `/api/rankings`,
`/api/sectors`, `/api/correlation`, `/api/stock/{ticker}`, `/api/pie/{id}`,
`/api/theme/{id}` (POST), `/api/export/{table}.csv`.

**Frontends (multi-page, share `static/jarvis-core.js`):**
- `/` launcher → choose a design. `/a` Elevated Terminal (dark, editorial-trader),
  `/b` Quant Research Lab (light, clinical), `/c` The Ledger (warm editorial).
  `/legacy` keeps the original mock.
- Pages: Overview · Pies · Holdings · Sectors · Quant/Rankings, with per-pie
  and per-stock drill-downs. Headline metric throughout = **predicted profit**.

Run: `cd backend && uv run python -m uvicorn src.main:app --port 8000`, open
http://localhost:8000.

---

# Status — first build pass

What I changed since the handover package was written.

## What's working end-to-end

Run:
```bash
cd backend
uv sync
uv run uvicorn src.main:app --port 8000
```
Then open http://localhost:8000.

The page bootstraps from `/api/snapshot`, opens a WebSocket to `/ws/stream`,
and overrides the random-walk simulation per panel as real data arrives.
Panels with no live adapter (strategies, tape, signals, execution, equity
curve until Alpaca is keyed) keep their original simulation so the page
stays visually alive.

## Live adapters

| Panel | Source | Status |
|---|---|---|
| Crypto perps | Hyperliquid WS (`allMids`) + REST (`metaAndAssetCtxs`) | Live, no key required |
| Polymarket | gamma-api markets, filtered by operator-interest keywords | Live, no key required |
| Equities (LSE) | yfinance, 30s poll | Live |
| Equities (Trading 212 ISA) | Trading 212 public API | Code complete, **needs `TRADING212_API_KEY` in .env** |
| Equities + Options + Equity Curve (US) | Alpaca paper REST | Code complete, **needs Alpaca paper keys in .env** |
| Manual portfolio | `~/.jarvis/portfolio.json`, file-watched | Live (seeded from template on first boot) |
| Hero strip / Risk / Exposure | Computed in `metrics/loop.py` every 30s | Live — fills out as soon as Alpaca seeds NAV history |
| Footer (mem/cpu/uptime) | psutil | Live |

## Trading 212 — the doc was wrong

The handover note said "Trading 212 has no public API". That's outdated.
There is a public REST API in beta for Invest / Stocks ISA accounts:
https://docs.trading212.com/api

The auth header in the official docs example shows Basic auth, but every
working community wrapper (and the live server) accepts the raw key:

```
Authorization: <YOUR_API_KEY>
```

Generate the key from the T212 app → Settings → API. Set
`TRADING212_API_KEY=...` in `backend/.env`. The adapter
(`src/adapters/trading212.py`) is read-only — it fetches
`/equity/account/cash`, `/equity/portfolio`, and the instrument metadata,
and merges positions into the `equities` slice tagged `LSE` for `_EQ`
suffix tickers, `T212` otherwise.

The manual `portfolio.json` workaround is still supported but no longer
necessary for ISA positions once the key is in place.

## What's not wired

- **Engine tail (`engine.jsonl`)** — strategies, tape, signals, and
  execution panels keep their simulation until the operator's algo engine
  writes events. Contract spec is in `03_DATA_SOURCES.md`.
- **IV rank** — needs 30 days of daily IV accumulation; not started.
- **Hyperliquid signed endpoint** for the operator's own perp positions —
  the public market data is in; positions would need the wallet+nonce
  signing flow.
- **Polymarket WS streaming** — currently 30s REST poll; sufficient for
  a watch panel. Wire the CLOB WS if tighter is needed.

## Safety

No order placement anywhere. Every adapter is read-only. The `mode`
badge stays `PAPER` in the footer. Alpaca uses the paper base URL
(`paper-api.alpaca.markets`); T212 only calls `/equity/...` read paths.

## Files added/touched

- `backend/src/adapters/hyperliquid.py` — new
- `backend/src/adapters/polymarket.py` — new
- `backend/src/adapters/trading212.py` — new
- `backend/src/adapters/alpaca.py` — new
- `backend/src/adapters/yfinance_adapter.py` — new
- `backend/src/adapters/manual_portfolio.py` — new
- `backend/src/metrics/loop.py` — new
- `backend/src/main.py` — wired adapters into the lifespan
- `backend/src/config.py` — added `trading212_api_key`
- `backend/.env.example` — added T212 key
- `backend/static/claude-jarvis-mock.html` — appended a live-data layer
  (WS client + snapshot bootstrap + per-panel handlers) and gated the
  random-walk `setInterval`s so they bail out once real data arrives.
