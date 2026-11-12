# 06 · Setup

Local dev only. macOS (operator runs M3 MacBook Air).

## Prereqs

- Python 3.11+ (`brew install python@3.11`)
- `uv` (recommended): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- An Alpaca paper account: https://app.alpaca.markets/ → API Keys → generate key. **Paper, not live.**

Optional, later:
- A Polymarket wallet address (for reading own positions).
- A Hyperliquid wallet (for reading own perp positions).

## First-time install

```bash
cd backend
uv sync                    # creates .venv, installs deps
cp .env.example .env       # then edit with your keys
```

Or with pip:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
```

## Run

```bash
uv run uvicorn src.main:app --reload --port 8000
```

Then open http://localhost:8000.

The mock HTML is served at `/`. WebSocket at `/ws/stream`. Snapshot at `/api/snapshot`.

## Data files

Jarvis maintains state under `~/.jarvis/`. Created automatically on first run.

```
~/.jarvis/
├── state.db                # SQLite: NAV history, position history
├── portfolio.json          # Manual entries for Trading 212 ISA positions
└── engine.jsonl            # Algo engine writes here; Jarvis tails it
```

`portfolio.json` template lives in `backend/data/portfolio.example.json`.

## .env keys

```
ALPACA_API_KEY=PK...        # paper key
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets

POLYMARKET_WALLET=0x...     # optional, for own positions

LOG_LEVEL=INFO
```

Never commit `.env`. It's in `.gitignore`.

## Running tests

There are none yet. Add a `tests/` dir as you go. Use `pytest` and `pytest-asyncio`.

## Troubleshooting

**yfinance returns empty data:**
Yahoo's unofficial endpoints break periodically. Try `pip install --upgrade yfinance`. If still broken, pin to `0.2.40` or switch to a paid feed.

**Alpaca WebSocket connection rejected:**
Free Alpaca data is IEX-only. The SIP feed requires a paid plan. Subscribe to `iex` feed explicitly:
```python
stream = StockDataStream(api_key, api_secret, feed='iex')
```

**Polymarket WS disconnects:**
Their CLOB WS is occasionally flaky. The adapter should reconnect with backoff; if it doesn't, REST polling at 5s intervals is acceptable fallback.

**Hyperliquid funding rate shows 0:**
Funding rates fire hourly; check `prevDayPx` and `markPx` instead of the funding-specific field if the latest hour hasn't settled yet.
