# Claude Jarvis — Backend

FastAPI server. Single process. Bind to localhost.

## Layout

```
backend/
├── pyproject.toml
├── .env.example
├── src/
│   ├── __init__.py
│   ├── main.py            # FastAPI app entry
│   ├── config.py          # Settings (pydantic-settings)
│   ├── schemas.py         # Pydantic models matching UI contract
│   ├── hub.py             # State + WS broadcast
│   ├── db.py              # SQLite (NAV history)
│   ├── adapters/          # One file per data source
│   │   ├── __init__.py
│   │   └── README.md      # Implement these
│   └── metrics/           # Computed quant metrics
│       ├── __init__.py
│       └── README.md      # Implement these
├── static/
│   └── claude-jarvis-mock.html  # Symlink or copy from /reference
└── data/
    └── portfolio.example.json
```

## Run

```bash
uv sync
uv run uvicorn src.main:app --reload
```

## Where to start

See `../05_ROADMAP.md`. TL;DR: Phase 0 first (get the server serving the mock), then Phase 1 (Hyperliquid + Polymarket).

## Module ownership

| Module | What it does | Phase introduced |
|---|---|---|
| `main.py` | App, routes, lifespan | 0 |
| `hub.py` | State dict + WS broadcast | 0 |
| `config.py` | Env vars, settings | 0 |
| `schemas.py` | UI contract models | 0 |
| `db.py` | SQLite for NAV history | 2 |
| `adapters/hyperliquid.py` | Crypto perps | 1 |
| `adapters/polymarket.py` | Prediction markets | 1 |
| `adapters/alpaca.py` | US equities + options + portfolio | 2, 5 |
| `adapters/yfinance_adapter.py` | LSE equities | 4 |
| `adapters/manual_portfolio.py` | ISA position JSON | 4 |
| `adapters/engine_tail.py` | Algo engine log tail | 6 |
| `metrics/returns.py` | Sharpe, DD, etc. | 3 |
| `metrics/exposure.py` | Gross/net/cash | 3 |
| `metrics/correlation.py` | Pair corr | 3 |
