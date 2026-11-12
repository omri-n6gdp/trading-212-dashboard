# Metrics

Computed quant statistics from the NAV time series and position snapshots.

## Files

| File | Computes |
|---|---|
| `returns.py` | Sharpe, Sortino, Calmar, max DD, current DD, VaR, CVaR, vol, skew, kurtosis |
| `exposure.py` | Gross / net / cash %, by-asset-class breakdown |
| `correlation.py` | Pair correlation matrix |
| `loop.py` | Background task that recomputes every 30s |

## Inputs

- `db.py` exposes `get_nav_series(days)` and `get_position_snapshots()` from SQLite.
- The metrics functions take pandas DataFrames/Series, no I/O.

## Reference formulas

All annualised assume 252 trading days unless otherwise noted.

```python
# returns.py
def sharpe(daily_returns: pd.Series, rf: float = 0.0) -> float:
    excess = daily_returns - rf / 252
    if excess.std() == 0:
        return 0.0
    return float(excess.mean() / excess.std() * np.sqrt(252))

def sortino(daily_returns: pd.Series, rf: float = 0.0) -> float:
    excess = daily_returns - rf / 252
    downside = excess[excess < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(excess.mean() / downside.std() * np.sqrt(252))

def max_drawdown(nav: pd.Series) -> float:
    """Returns the worst peak-to-trough drawdown as a negative number."""
    cum_max = nav.cummax()
    drawdown = (nav - cum_max) / cum_max
    return float(drawdown.min())

def calmar(daily_returns: pd.Series, nav: pd.Series) -> float:
    """Annualised return / |max drawdown|."""
    mdd = abs(max_drawdown(nav))
    if mdd == 0:
        return 0.0
    ann_ret = (1 + daily_returns.mean()) ** 252 - 1
    return float(ann_ret / mdd)

def var_historical(daily_returns: pd.Series, conf: float = 0.95) -> float:
    """Returned as a positive number representing the loss."""
    return float(-np.percentile(daily_returns, (1 - conf) * 100))

def cvar_historical(daily_returns: pd.Series, conf: float = 0.95) -> float:
    var = -var_historical(daily_returns, conf)
    tail = daily_returns[daily_returns <= var]
    if len(tail) == 0:
        return 0.0
    return float(-tail.mean())
```

```python
# exposure.py
def gross_exposure(positions: pd.DataFrame) -> float:
    """positions has columns: weight (signed fraction of NAV)."""
    return float(positions["weight"].abs().sum() * 100)

def net_exposure(positions: pd.DataFrame) -> float:
    return float(positions["weight"].sum() * 100)

def by_asset_class(positions: pd.DataFrame) -> dict[str, float]:
    """positions also has 'asset_class' column."""
    grouped = positions.groupby("asset_class")["weight"].apply(lambda s: s.abs().sum())
    out = {k: float(v * 100) for k, v in grouped.items()}
    out.setdefault("cash", max(0.0, 100.0 - sum(out.values())))
    return out
```

```python
# correlation.py
def pair_corr(returns_df: pd.DataFrame) -> pd.DataFrame:
    """returns_df: columns are symbols, rows are dates."""
    return returns_df.corr(method="pearson")
```

## Loop

```python
# loop.py
async def run(hub: Hub):
    while True:
        try:
            nav = db.get_nav_series(days=365)
            positions = db.get_position_snapshots(latest=True)
            
            if len(nav) >= 30:
                rets = nav.pct_change().dropna()
                hub.update("risk", {
                    "vol_ann_pct": float(rets.std() * np.sqrt(252) * 100),
                    "skew": float(scipy.stats.skew(rets)),
                    "kurtosis": float(scipy.stats.kurtosis(rets, fisher=False)),
                    "var_99_pct": var_historical(rets, 0.99) * 100,
                    "expected_shortfall_pct": cvar_historical(rets, 0.99) * 100,
                })
                hub.update("hero", {
                    "sharpe": sharpe(rets),
                    "sortino": sortino(rets),
                    "calmar": calmar(rets, nav),
                    "max_dd_pct": max_drawdown(nav) * 100,
                    "current_dd_pct": ((nav.iloc[-1] / nav.cummax().iloc[-1]) - 1) * 100,
                    # ... etc
                })
            
            if not positions.empty:
                hub.update("exposure", {"by_class": by_asset_class(positions)})
            
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("metrics loop failed")
            await asyncio.sleep(5)
```
