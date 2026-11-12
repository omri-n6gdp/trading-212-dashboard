# 03 · Data Sources

The mock has 12 numbered panels plus a header strip, hero strip, and footer. This doc maps every numerical element to a real data source. Read this top to bottom before writing code.

Legend:
- **Free** = no key required.
- **Account** = need an account (free tier ok unless noted).
- **Paid** = budget required.
- **Local** = computed from other data.
- **Manual** = operator-maintained JSON file.

---

## Header strip

| Element | Source | Implementation |
|---|---|---|
| `SYSTEM ARMED` indicator | Self | Always `ARMED` while server is up. Switch to `HALT` if any adapter is in backoff > 30s. |
| Latency | Self | Measure round-trip on Alpaca WS ping. Display `p50` over last 60s. |
| Fill rate | Self | `filled_orders / submitted_orders` over last 24h from algo engine logs. Until engine is wired, default to `99.4%`. |
| Venues `7/7` | Self | Count of adapters in `CONNECTED` state. |
| Uptime | Self | `time.monotonic()` from server start. |
| LSE OPEN/CLOSED | Self | Logic already in the mock: weekday 08:00–16:30 London time. Keep it. |
| UTC clock | Self | Client-side, already implemented. |

---

## Hero strip (6 cells)

Every cell here is computed from the NAV time series (see `metrics_recompute` task).

| Cell | Computation |
|---|---|
| MTD / YTD / ITD return | `(nav_now / nav_at_start_of_period) - 1`. ITD = inception-to-date. |
| Sharpe / Sortino / Calmar | Standard formulas on daily returns. Annualised. Rolling 90D. |
| Win rate / profit factor | From algo engine trade log. Until wired: read from a manual JSON file. |
| Max DD / current DD | Peak-to-trough on the equity curve. |
| VaR 95% / CVaR 95% | Historical: 5th percentile of daily returns. CVaR = mean of returns below VaR. |
| Gross / net exposure | `gross = sum(|weight_i|)`, `net = sum(weight_i)`. From position snapshot. |

Implementation lives in `backend/src/metrics/`. See `04_UI_CONTRACT.md` for the exact return shape.

---

## Panel 01 · Equity Curve

**Source:** SQLite `nav_snapshot` table.

**On startup:** populate the curve with the last 24h of minute-level snapshots.

**During session:** every minute, the metrics task appends a new row and pushes a `equity.append` delta.

**Benchmark line:** ACWI ETF (US-listed, free via yfinance) sampled at the same cadence. Computed similarly — cumulative return from session start.

**The hard bit:** populating the historical NAV in the first place. Options:

1. If operator has Alpaca portfolio history → use Alpaca `/v2/account/portfolio/history` (returns up to 1Y of equity values).
2. Otherwise, start the series from "now" and build it up over time. Display "Backfilling — N days of history" until 30 days accumulate.

Don't try to backfill Trading 212. There is no API.

---

## Panel 02 · Risk · Rolling 90D

**All locally computed** from the NAV return series.

| Metric | Formula |
|---|---|
| Volatility (ann.) | `std(daily_returns) * sqrt(252)` |
| Skew | `scipy.stats.skew(daily_returns)` |
| Kurtosis | `scipy.stats.kurtosis(daily_returns, fisher=False)` (use the non-Fisher convention so normal ≈ 3) |
| VaR 99% (1D) | `-percentile(daily_returns, 1)` (historical method) |
| Expected shortfall | `-mean(returns where return <= var_99)` |
| Avg holding | From trade log: `mean(exit_ts - entry_ts)`. Engine-dependent. |
| Turnover (1D) | `sum(|trade_notional|) / nav`. Engine-dependent. |
| Kelly fraction | `(win_rate * avg_win - loss_rate * avg_loss) / avg_win`. Engine-dependent. Cap at 0.5 (half-Kelly). |
| Treynor ratio | `(return - rf) / beta`. Beta from regression against ACWI. |

The "engine-dependent" ones need the algo engine to log trades. Until it does, surface `—` or read from a manual JSON.

---

## Panel 03 · Exposure by Asset Class

**Source:** position snapshot, grouped.

```
equities/etf  = sum(weight of equity tickers)
crypto perps  = sum(weight of crypto positions)
options       = sum(|delta-adjusted notional| of options positions) / nav
polymarket    = sum(position size in markets) / nav
cash          = 1 - sum of above (clamped to >= 0)
```

For options, delta-adjusted notional is `delta * contract_size * spot * contracts / nav`. Pull spot from same equities feed.

---

## Panel 04 · Pair Correlation · 60D

**Source:** pandas on a returns matrix.

```python
returns = nav_snapshot.pivot('ts', 'symbol', 'pct_change').tail(60)
corr = returns.corr(method='pearson')
```

Tickers shown in the mock are `VWRL, SMH, RKLB, BTC, GOLD, CASH`. CASH always correlates 0 with everything. GOLD = `GLD` (US ETF) as proxy.

Symbols can be hardcoded in the adapter for now; later, make it dynamic from current holdings.

---

## Panel 05 · Crypto Perps · LIVE  **← Phase 1 priority**

**Source:** Hyperliquid public WebSocket. **Free, no auth.**

**Endpoint:** `wss://api.hyperliquid.xyz/ws`

**SDK:** [`hyperliquid-python-sdk`](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) — has an `Info` class that wraps the WS.

**What to subscribe to:**
- `allMids` → mark prices for every coin (fires every ~250ms)
- `meta` → contract info including funding rates and open interest (poll every 30s via REST: `https://api.hyperliquid.xyz/info` with body `{"type": "meta"}`)
- `metaAndAssetCtxs` → combined snapshot, useful on startup

**Data shape returned by `meta`:**
```json
{
  "universe": [
    { "name": "BTC", "szDecimals": 5, "maxLeverage": 50 },
    ...
  ]
}
```

**Data shape returned by `metaAndAssetCtxs`:**
```json
[ {"universe": [...]},
  [ {"funding": "0.0000125", "openInterest": "24135.41", "prevDayPx": "67234.0", "dayNtlVlm": "1234567890.0", "premium": "0.0001"}, ... ]
]
```

Funding rates from Hyperliquid are **hourly**, expressed as fraction (e.g. `0.0000125` = 0.00125%). The UI shows 8h funding — multiply by 8 for display equivalence with Binance/Bybit conventions.

Symbols in the mock: BTC, ETH, SOL, HYPE, DOGE, BNB. All exist on Hyperliquid. Display names as `BTC-PERP` etc.

**Sparklines:** keep a 30-tick ring buffer per symbol of last marks. Push to frontend in the snapshot, update via `crypto_perps.spark` deltas.

**Operator position field:** if Omri trades on Hyperliquid, his positions can be fetched via the signed user endpoint. For now, leave the position column blank or read from manual JSON.

---

## Panel 06 · Options Book

**Source:** Alpaca options data API (paper or live key works). **Account required, free for paper.**

**Endpoint:** `https://data.alpaca.markets/v1beta1/options/snapshots/{underlying_symbol}`

**Auth:** APCA-API-KEY-ID and APCA-API-SECRET-KEY headers.

**Returns:** snapshot of the full chain with IV and Greeks already computed.

```json
{
  "snapshots": {
    "SPY250530C00528000": {
      "impliedVolatility": 0.142,
      "greeks": { "delta": 0.62, "gamma": 0.041, "theta": -0.18, "vega": 0.12 },
      "latestQuote": { ... }
    }
  }
}
```

**Operator's positions** in options: read from Alpaca positions endpoint (`/v2/positions`). Cross-reference each option position with the snapshot to get Greeks.

**IV Rank:** not provided by Alpaca. Compute as `(current_iv - 1y_min_iv) / (1y_max_iv - 1y_min_iv) * 100`. Needs 1Y of IV history. Two paths:

1. Sample IV daily at close and accumulate over time. Display `—` for IVR until 30 days of history.
2. Use an external paid source (ORATS, IVolatility).

Pick (1) for free.

**Mock contracts referenced:** SPY 28MAY 528C, QQQ 30MAY 478P, SMH 20JUN 280C, RKLB 18JUL 22.5C, VIX 18JUN 18C. The dates will be stale; the operator either replaces them or reads his actual options positions.

---

## Panel 07 · Polymarket · ACTIVE POSITIONS  **← Phase 1 priority**

**Source:** Polymarket CLOB API. **Free, no auth for reads.**

**Two endpoints to know:**

1. **Markets list (REST):** `https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=100`
   - Returns metadata: question, condition_id, end_date, volume, outcomes, last prices.
2. **Live price feed (WS):** `wss://ws-subscriptions-clob.polymarket.com/ws/market`
   - Subscribe per asset_id (one per Yes/No outcome).

**Sample REST response:**
```json
{
  "markets": [{
    "question": "Will Starship achieve orbital catch in 2026?",
    "conditionId": "0x...",
    "outcomes": "[\"Yes\", \"No\"]",
    "outcomePrices": "[\"0.67\", \"0.33\"]",
    "volume24hr": 2400000.0,
    "endDate": "2026-12-31T23:59:59Z",
    "clobTokenIds": "[\"123...\", \"456...\"]"
  }]
}
```

Note that `outcomes`, `outcomePrices`, `clobTokenIds` are JSON-encoded strings, not arrays. Parse them.

**Operator's positions:** Polymarket positions tied to a wallet. If Omri has a wallet, `https://data-api.polymarket.com/positions?user={address}` returns them. Until then, positions = manual JSON entries (he tells you "I'm YES on Starship 2026 at 0.54").

**Markets in the mock:** Starship 2026, Fed cut Sep 2026, Man City Premier League, Artemis III, BTC > $120k. Some of these are placeholder — pull live market questions from the API to populate. The operator's interests are: space industry, premier league football, crypto, Fed policy.

**Implied probability P&L:** `(current_price - entry_price) / entry_price` for YES; inverse for NO.

---

## Panel 08 · Equities / ETF

**Two sub-sources** because the operator holds positions on both US and UK exchanges:

### US tickers (RKLB, LEU, ESLT, FRO, DHT, INSW, ASTS, LUNR, SERV, OPRA, SMH-US)

**Source:** Alpaca market data. **Free with paper account.**

**Endpoint:** `https://data.alpaca.markets/v2/stocks/snapshots?symbols=RKLB,LEU,...`

**Free tier:** IEX feed only (not full SIP). That's fine for a dashboard — you get last trade, bid/ask, daily bar. Latency ~real-time during market hours.

### LSE tickers (VWRL, SGLP, VHYG, VFEG, ESLT.L)

**Source:** yfinance (unofficial Yahoo API). **Free, rate-limited.**

```python
import yfinance as yf
tickers = yf.Tickers("VWRL.L SMH.L SGLP.L VHYG.L VFEG.L")
data = tickers.tickers["VWRL.L"].info  # last price, day change, etc.
# Or: yf.download(...) for time series
```

**Caveats:**
- Yahoo data is 15-min delayed for some LSE tickers.
- yfinance breaks intermittently when Yahoo changes their internal endpoints. Pin a working version (currently `0.2.40` as of mid-2026).
- Use sparingly: poll once per 15s, not per second.

**NAV % and P&L per ticker:** read from position snapshot in SQLite, joined against current price.

**Sparklines:** keep a 30-tick ring buffer per symbol of recent prices.

---

## Panel 09 · Strategy P&L · MTD

**Source:** operator's algo engine, via a JSON-lines file at `~/.jarvis/engine.jsonl`.

**Contract:** the engine appends one line per event:

```json
{"ts":"2026-05-27T14:23:01Z","type":"trade","strategy":"ICT-FVG","symbol":"RKLB","pnl_bps":34,"side":"EXIT"}
{"ts":"2026-05-27T14:25:14Z","type":"signal","strategy":"EMA-9-21","symbol":"BTC-PERP","score":0.84}
```

Backend tails the file (using `aiofiles` or just polling `os.stat` for new bytes). On each new line, update in-memory state and broadcast.

**MTD strategy P&L:** sum of `pnl_bps` per strategy since month start, converted to % (`bps / 100`).

**If the engine isn't writing yet:** leave the simulation in place, or read from a static JSON file the operator updates manually.

---

## Panel 10 · Execution Quality · 24H

**Source:** algo engine logs (same `engine.jsonl`).

| Metric | Computation |
|---|---|
| Avg slippage | Mean of `(fill_price - midprice_at_send) / midprice_at_send` in bps. Negative = price improvement. |
| Fill latency | `fill_ts - submit_ts` per fill. Display p50 and p99. |
| Reject rate | `rejected_orders / submitted_orders` |
| Taker / maker | Tagged per fill by the engine; show split. |
| Iceberg use | Engine flag per order. |
| VWAP miss | `(avg_fill_price - arrival_price_vwap)` in bps. Arrival VWAP = VWAP over the interval the order was active. |

All of these need the engine to log timestamps and midpoints. Operator's job to instrument; Jarvis just consumes.

---

## Panel 11 · Order Tape

**Source:** `engine.jsonl`, `type: trade` events.

Stream new events to frontend via `tape.append`. Frontend already has the ring buffer logic (22 rows). Match the existing row format.

---

## Panel 12 · Signal · Engine Log

**Source:** `engine.jsonl`, all event types except `trade`.

Levels (already styled in the mock):
- `INFO` — connectivity, heartbeats, scheduled jobs
- `SIG` — signal generated by a strategy
- `EXEC` — order submitted, filled, cancelled
- `RISK` — exposure changes, vol expansion alerts
- `WARN` — degraded conditions (funding extremes, data gaps)
- `HALT` — strategy or system disabled

Engine writes the level; backend forwards as-is.

---

## Footer

| Element | Source |
|---|---|
| Node ID | Hardcode `ldn-1-a3` or read from env |
| Data sources list | Hardcode reflecting actual connected adapters |
| Engine label | Hardcode |
| MEM | `psutil.Process().memory_info().rss` formatted GB |
| CPU | `psutil.cpu_percent(interval=None)` |
| WS connection counter | Live count of connected adapters |
| `PAPER · SIM MODE` | Keep this badge. Until live trading is wired (which is not the goal of this project), it stays amber. |

---

## Quick reference: free vs paid

| Source | Status | What you get |
|---|---|---|
| Hyperliquid WS + REST | Free, no auth | Marks, funding, OI, full perp universe |
| Polymarket CLOB | Free, no auth | Markets, prices, volumes, WS streaming |
| yfinance | Free, no auth | Delayed Yahoo data, all global tickers |
| Alpaca paper | Free, account | US equity snapshots, options snapshots with Greeks, positions, portfolio history |
| Polygon | Free tier (5 req/min) or paid | Real-time US equity if free tier insufficient |
| ORATS / IVolatility | Paid | Real IV rank and historical IV |
| Trading 212 | No API | Manual JSON entries only |

If a "real-time" claim ever needs to be backed up, document it in code with a comment showing the source endpoint.
