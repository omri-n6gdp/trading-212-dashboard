# Adapters

Each file in this folder is one data source. The pattern is the same for all:

```python
async def run(hub: Hub) -> None:
    """Long-lived task. Handle reconnection internally."""
    backoff = 1
    while True:
        try:
            await _connect_and_stream(hub)
            backoff = 1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("adapter dropped")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
```

When the adapter has fresh data, it calls one of:

```python
hub.update("crypto_perps", {"BTC-PERP": {"mark": 68420.4}})  # merge
hub.replace("correlation", {"tickers": [...], "matrix": [...]})  # full replace
hub.append("tape", fill_dict)  # ring buffer
```

That's it. The Hub handles WS broadcast.

## Files to create (per roadmap)

| File | Phase | Source |
|---|---|---|
| `hyperliquid.py` | 1 | Hyperliquid public WS + REST |
| `polymarket.py` | 1 | Polymarket gamma-api REST + CLOB WS |
| `alpaca.py` | 2 / 5 | alpaca-py SDK |
| `yfinance_adapter.py` | 4 | yfinance polling |
| `manual_portfolio.py` | 4 | watchfiles on portfolio.json |
| `engine_tail.py` | 6 | tail engine.jsonl |

Each one updates exactly the slices listed in `03_DATA_SOURCES.md`. Don't cross the streams.

## Reconnect policy

- Exponential backoff, base 1s, cap 30s.
- Reset backoff to 1s after a clean message.
- On `CancelledError`, re-raise immediately (graceful shutdown).
- Never let an adapter take down the app — outermost loop catches all other exceptions.

## Update cadence guidance

| Source | Polling / WS | Frequency |
|---|---|---|
| Hyperliquid `allMids` | WS push | ~250ms |
| Hyperliquid `meta` | REST poll | 30s |
| Polymarket markets | REST poll | 30s (or WS if reliable) |
| yfinance | REST poll | 15s |
| Alpaca trades | WS push | real-time |
| Alpaca account | REST poll | 60s |
| Engine log tail | file watch | real-time |
| Metrics recompute | timer | 30s |

Don't poll Hyperliquid REST more than once per 10s — they will throttle. WS is always preferred.
