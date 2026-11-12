# 01 · Brief

## What this is

A single-page algorithmic trading dashboard called **Claude Jarvis** that displays live data from multiple venues: crypto perps, US equities, ETFs, options, and Polymarket prediction markets. Plus computed quant metrics (Sharpe, drawdown, VaR, exposure, correlation, etc.) and a live order tape from the operator's algo engine.

Current state: the UI is **fully built as a static mock** in `reference/claude-jarvis-mock.html` — every panel renders correctly, every value updates on a staggered interval, but all data is randomly generated client-side. No real network calls anywhere.

Goal: keep the UI intact, replace the simulation with real data feeds.

## What already exists (operator's side)

Omri has, in some form on his local machine:

- A **FastAPI backend** with a vectorised backtest engine (vectorbt-style).
- A **100-strategy library** organised into 9 categories: SMC (sweep, FVG, order block), trend (EMA momentum), mean reversion (Z-score), breakout (ATR), stat-arb (pairs), options (vol-risk premium), NLP (news sentiment).
- **Alpaca paper trading** already integrated — keys exist, account exists.
- An HTML dashboard from prior iterations (not this one, but similar).
- A multi-AI portfolio experiment using Trading 212 Pies (Claude, GPT, Gemini each running their own slice).

**Important:** assume nothing about how clean any of that code is. The handover target is a *new* clean repo, not a graft onto the existing engine. If integration with his engine becomes useful later, that's a Phase 5 problem (see roadmap).

## Example positions (referenced in the mock)

Example tickers the mock displays (illustrative; the real version reads positions from the API):

- **Core ETFs (LSE):** VWRL, SMH, SGLP, VHYG, VFEG
- **Individual equities:** RKLB (Rocket Lab), LEU (Centrus Energy), ESLT (Elbit Systems)
- **Tanker thesis (US):** FRO (Frontline), DHT (DHT Holdings), INSW (International Seaways)
- **Speculative:** ASTS (AST SpaceMobile), LUNR (Intuitive Machines), SERV (Serve Robotics), OPRA (Opera)

Crypto perps referenced in the mock (these are aspirational — operator does not yet trade these live, the panel can display public market data without any position):

- BTC, ETH, SOL, HYPE, DOGE, BNB on Hyperliquid or Bybit perpetuals

## What "real" means per panel

This is the dividing line that matters:

1. **Market data** (prices, funding rates, implied probabilities) — pull from public APIs. Always real.
2. **Operator positions** (NAV %, P&L per ticker) — pull from Alpaca where available; manual JSON file for everything Alpaca doesn't cover (LSE positions, Trading 212 ISA).
3. **Computed metrics** (Sharpe, drawdown, VaR, correlations) — computed locally from a NAV time series. Real once we have the inputs.
4. **Strategy P&L + order tape + signal log** — comes from the operator's algo engine. Only "real" once the engine is wired up. Until then, leave the simulation in place or read from a JSON file the operator can hand-update.

See `03_DATA_SOURCES.md` for the per-panel breakdown.

## Non-goals

- **No backtesting features in the dashboard.** This is a live monitoring tool. Backtests live in the existing engine.
- **No order entry.** Read-only. The operator places trades via Alpaca/Trading 212/Hyperliquid directly. Jarvis observes, computes, and displays.
- **No auth, no login, no user management.** Single-operator local tool. Bind to `localhost` only.
- **No mobile layout work.** Desktop dashboard. The mock has minimal responsive rules; leave them or improve them, but don't invest heavily.
