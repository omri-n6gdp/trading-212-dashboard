# 04 · UI Contract

Exact JSON shapes the frontend expects. Implement to these. If you find a shape that's awkward, change it everywhere (both ends) — don't shim.

## Snapshot endpoint

```
GET /api/snapshot
→ 200 application/json
```

Returns the full current state. Frontend calls this on page load before opening the WebSocket. Shape:

```json
{
  "ts": "2026-05-27T20:34:12Z",
  "system": { ... },
  "hero": { ... },
  "equity_curve": { ... },
  "risk": { ... },
  "exposure": { ... },
  "correlation": { ... },
  "crypto_perps": { ... },
  "options": [ ... ],
  "polymarket": [ ... ],
  "equities": [ ... ],
  "strategies": [ ... ],
  "execution": { ... },
  "tape": [ ... ],
  "signals": [ ... ],
  "footer": { ... }
}
```

## WebSocket stream

```
WS /ws/stream
```

Server-to-client only. Each message:

```json
{ "type": "<slice>.<verb>", "payload": { ... } }
```

Verbs: `update` (replace fields), `append` (add to ring buffer), `replace` (full slice replacement).

---

## Slice shapes

### `system`

```json
{
  "status": "ARMED",
  "latency_ms": 14,
  "fill_rate_pct": 99.4,
  "venues_connected": 7,
  "venues_total": 7,
  "uptime_seconds": 1219274,
  "strategies_active": 9,
  "lse_open": true
}
```

WS: `system.update`, partial fields ok.

---

### `hero`

```json
{
  "mtd_pct": 12.47,
  "ytd_pct": 34.18,
  "itd_pct": 89.42,
  "sharpe": 2.31,
  "sortino": 3.08,
  "calmar": 1.84,
  "win_rate_pct": 54.2,
  "profit_factor": 1.87,
  "trade_count": 2184,
  "avg_trade_bps": 18,
  "max_dd_pct": -4.21,
  "current_dd_pct": -0.43,
  "var_95_pct": 1.42,
  "cvar_95_pct": 2.18,
  "vol_ann_pct": 11.4,
  "gross_exposure_pct": 142,
  "net_exposure_pct": 67,
  "leverage": 1.42,
  "cash_pct": 38
}
```

WS: `hero.update`, partial ok.

---

### `equity_curve`

```json
{
  "points": [
    { "ts": "2026-05-27T08:00:00Z", "nav_pct": 0.0 },
    { "ts": "2026-05-27T08:01:00Z", "nav_pct": 0.0008 },
    ...
  ],
  "benchmark": [
    { "ts": "...", "value_pct": 0.0 },
    ...
  ],
  "stats": {
    "session_pct": 0.34,
    "alpha_ann_pct": 8.21,
    "beta_spx": 0.34,
    "r_squared": 0.41,
    "info_ratio": 1.21
  }
}
```

WS deltas:
- `equity_curve.append` → `{ "point": {...}, "benchmark": {...} }` (one new tick on each)
- `equity_curve.stats` → updates the `stats` object

---

### `risk`

```json
{
  "vol_ann_pct": 11.4,
  "skew": -0.34,
  "kurtosis": 3.41,
  "var_99_pct": 2.18,
  "expected_shortfall_pct": 2.84,
  "avg_holding_seconds": 15720,
  "turnover_pct": 38.2,
  "kelly_fraction": 0.42,
  "treynor_ratio": 0.18
}
```

`avg_holding_seconds` formatted client-side as `Xh Ym`. WS: `risk.update`.

---

### `exposure`

```json
{
  "by_class": {
    "equities_etf": 38.4,
    "crypto_perps": 12.1,
    "options": 8.2,
    "polymarket": 3.1,
    "cash": 38.2
  }
}
```

All values are % of NAV. WS: `exposure.update`.

---

### `correlation`

```json
{
  "tickers": ["VWRL", "SMH", "RKLB", "BTC", "GOLD", "CASH"],
  "matrix": [
    [1.00, 0.74, 0.42, 0.31, -0.14, 0.0],
    [0.74, 1.00, 0.58, 0.38, -0.21, 0.0],
    ...
  ]
}
```

WS: `correlation.replace` with full matrix (cheap to send).

---

### `crypto_perps`

Dict keyed by symbol:

```json
{
  "BTC-PERP": {
    "mark": 68420.4,
    "day_pct": 1.84,
    "funding_8h_pct": 0.0124,
    "open_interest": "2.41B",
    "position": { "side": "LONG", "nav_pct": 0.8 },
    "spark": [68100, 68150, 68210, ...]
  },
  "ETH-PERP": { ... }
}
```

`open_interest` is a pre-formatted string (`"2.41B"`, `"482M"`, etc.) — the backend does this once instead of having the frontend re-implement the SI formatting.

`position` is nullable.

WS deltas:
- `crypto_perps.update` → `{ "BTC-PERP": { "mark": 68425.1 } }` (partial fields)
- `crypto_perps.spark` → `{ "BTC-PERP": [...new 30-tick array...] }` (full replacement when changed materially)

---

### `options`

Array of contract positions:

```json
[
  {
    "contract": "SPY 28MAY 528C",
    "occ_symbol": "SPY250528C00528000",
    "quantity": 4,
    "side": "long",
    "label": "tail hedge" | null,
    "iv_pct": 14.2,
    "iv_rank": 62,
    "delta": 0.62,
    "gamma": 0.041,
    "theta": -0.18,
    "vega": 0.12,
    "pnl_bps": 34
  }
]
```

`label` shows in the small "×4 long" cell. Make it free-text or null.

WS: `options.update` with array of partial updates by `occ_symbol`. Or `options.replace` with the full array on each refresh — easier, fine for ~10 contracts.

---

### `polymarket`

Array of markets the operator has interest in (active positions or watchlist):

```json
[
  {
    "id": "starship-2026-catch",
    "question": "Will SpaceX achieve Starship orbital catch in 2026?",
    "implied_pct": 67,
    "volume_24h": "$2.4M",
    "resolves": "Dec 31",
    "position": {
      "side": "YES",
      "entry": 0.54,
      "pnl_pct": 24.1
    }
  }
]
```

`volume_24h` is pre-formatted string. `position` nullable.

WS: `polymarket.update` (one or more by `id`) or `polymarket.replace`.

---

### `equities`

Array, ordered by NAV weight desc:

```json
[
  {
    "ticker": "VWRL",
    "name": "Vanguard FTSE All-World",
    "exchange": "LSE",
    "nav_pct": 14.2,
    "day_pct": 0.42,
    "pnl_bps": 18,
    "spark": [98.4, 98.5, 98.3, ...]
  }
]
```

WS: `equities.update` for incremental price changes, `equities.replace` for full re-sort after position change.

---

### `strategies`

```json
[
  { "name": "Liquidity sweep",       "category": "SMC",      "pnl_mtd_pct": 3.42 },
  { "name": "ICT fair-value gap",    "category": "SMC",      "pnl_mtd_pct": 4.18 },
  ...
]
```

WS: `strategies.replace` whenever any P&L updates. ~9 entries, cheap.

---

### `execution`

```json
{
  "avg_slippage_bps": 0.4,
  "fill_latency_p50_ms": 18,
  "fill_latency_p99_ms": 41,
  "reject_rate_pct": 0.08,
  "taker_pct": 38,
  "maker_pct": 62,
  "iceberg_pct": 14,
  "vwap_miss_bps": -0.6
}
```

WS: `execution.update`.

---

### `tape`

Ring buffer of recent fills (most recent first). On snapshot, send last 22. On WS, send new fills as they happen.

```json
[
  {
    "ts": "2026-05-27T20:34:12.481Z",
    "venue": "HYPL",
    "side": "B",
    "symbol": "BTC-PERP",
    "price": 68421.5,
    "strategy": "ICT-FVG",
    "action": "ENTRY",
    "pnl_bps": null
  },
  {
    "ts": "2026-05-27T20:34:09.122Z",
    "venue": "ALPC",
    "side": "S",
    "symbol": "RKLB",
    "price": 22.41,
    "strategy": "EMA-9-21",
    "action": "EXIT",
    "pnl_bps": 27
  }
]
```

WS: `tape.append` → `{ "fill": { ... } }`.

---

### `signals`

```json
[
  {
    "ts": "2026-05-27T20:33:54Z",
    "level": "SIG",
    "message": "ICT-FVG signal <b>RKLB</b> @ 22.41 · score 0.84 · sized 0.6% NAV"
  }
]
```

`message` can contain `<b>` tags for emphasis. Sanitize on the backend before sending (whitelist `<b>` only); frontend trusts it.

WS: `signals.append` → `{ "signal": { ... } }`.

---

### `footer`

```json
{
  "node_id": "ldn-1-a3",
  "data_sources": ["polygon", "alpaca", "hyperliquid", "polymarket"],
  "engine": "vectorbt-pro · fastapi",
  "mem_gb": 2.1,
  "cpu_pct": 12,
  "ws_connected": 7,
  "ws_total": 7,
  "mode": "PAPER"
}
```

WS: `footer.update`.

---

## Type catalog (for the client-side dispatcher)

Copy-paste-friendly list of every `type` the client must handle:

```
system.update
hero.update
equity_curve.append
equity_curve.stats
risk.update
exposure.update
correlation.replace
crypto_perps.update
crypto_perps.spark
options.update
options.replace
polymarket.update
polymarket.replace
equities.update
equities.replace
strategies.replace
execution.update
tape.append
signals.append
footer.update
```

Frontend pattern:

```javascript
const handlers = {
  'system.update': (p) => updateSystem(p),
  'hero.update': (p) => updateHero(p),
  'crypto_perps.update': (p) => updateCryptos(p),
  // ...
};
ws.onmessage = (evt) => {
  const { type, payload } = JSON.parse(evt.data);
  const h = handlers[type];
  if (h) h(payload);
  else console.warn('unknown message type', type);
};
```
