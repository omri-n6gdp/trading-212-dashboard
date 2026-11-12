# 08 · Personalization & Generalization

The dashboard must work for **any** Trading 212 account with **zero code
changes** — only an API key and an optional config file. Nothing personal is
hardcoded in source.

## What makes it generalized

- **The account** is identified solely by `TRADING212_API_KEY` /
  `TRADING212_API_SECRET` in `backend/.env`. Account id, base currency, name,
  positions, pies, and history all come from the API at runtime.
- **Display / preference data** lives in one editable JSON file:
  `~/.jarvis/jarvis.config.json`, seeded on first run from
  `backend/data/jarvis.config.example.json`. Edit it and the dashboard reflects
  the change within a refresh cycle (the file is watched).
- **No ticker lists, pie names, or amounts** are written into Python or HTML.

## `jarvis.config.json` schema

```json
{
  "display_name": "My Portfolio",
  "base_currency": null,                 // null = use the account currency from the API
  "benchmarks": [
    { "symbol": "SPY",    "label": "S&P 500",    "currency": "USD" },
    { "symbol": "VWRL.L", "label": "All-World",  "currency": "GBP" }
  ],
  "primary_benchmark": "SPY",
  "risk_free_rate": 0.04,                // annual, for Sharpe/Sortino
  "monte_carlo": {
    "horizon_days": 252,
    "n_sims": 4000,
    "engine": "bootstrap",               // "bootstrap" | "gbm"
    "seed": 7,
    "gain_threshold_pct": 20,
    "loss_threshold_pct": 20,
    "drift_shrink": 0.45,                // predicted drift = shrink*historical + (1-shrink)*baseline
    "drift_baseline": 0.05,              // modest baseline annual drift the forecast reverts toward
    "drift_clip_lo": -0.35,              // clip the per-asset annual drift to a sane band
    "drift_clip_hi": 0.40
  },
  "goal_value": null,                    // optional target value for "prob of reaching goal"
  "ranking": {
    "headline": "predicted_profit",      // what the master ranking sorts by
    "score_weights": {                   // composite 0-100 score blend
      "momentum": 0.25, "risk_adjusted": 0.25,
      "trend": 0.20, "low_volatility": 0.15, "value": 0.15
    }
  },
  "thematic_tags": {                     // optional: maps tickers to themes for thematic exposure
    "Space":   ["RKLB", "ASTS", "LUNR", "JEDG", "ASTR"],
    "Quantum": ["QBTS", "IONQ", "RGTI", "QNTM"],
    "AI":      ["NOW", "ZETA", "MGNI", "PLTR"],
    "Energy":  ["VST", "LEU", "CEG"],
    "Defense": ["ESLT", "RTX"]
  },
  "notes": {                             // optional free-text notes per ticker/pie, shown in drill-downs
    "RKLB": "Long-term space thesis."
  },
  "theme": "elevated"                    // selected frontend design id
}
```

## Rules for implementers

- Read config through one loader (`src/config_user.py`) that merges the file
  over built-in defaults, so a missing or partial file still works.
- If `base_currency` is null, use the account currency reported by T212.
- Thematic tags and notes are matched on the short ticker (the symbol shown in
  the UI), case-insensitive.
- The example template must contain only generic placeholders — never Omri's
  real values.
- The chosen frontend `theme` is the only UI state persisted server-side; the
  three designs otherwise read identical `/api` data.
