# 07 · Feature Catalogue — 100 features

The target for the rebuild. Every feature is grounded in data the system can
actually obtain: the Trading 212 public API (positions, pies, orders,
dividends, transactions, account) and ~1y of daily price history per symbol
from yfinance (cached at `~/.jarvis/perf_prices.pkl`). Nothing here needs a
data source we don't already have, except per-ticker fundamentals/sector/
analyst targets, which come from `yfinance` `.info` / `.get_info()` and are
cached.

## Principles (non-negotiable)

- **Generalized.** Works for ANY Trading 212 account by swapping the API key.
  The account label and base currency are read live from the API. Nothing
  personal is hardcoded in source.
- **Personalization lives in one editable file** (`~/.jarvis/jarvis.config.json`,
  seeded from `backend/data/jarvis.config.example.json`). Display name,
  base currency override, benchmarks, risk-free rate, Monte Carlo settings,
  goal value, thematic tag map, per-ticker notes, and theme all live there.
  See `08_PERSONALIZATION.md`.
- **Predicted profit is the headline** metric at every level (portfolio, pie,
  stock): Monte Carlo expected 1Y profit in account currency + probability of
  profit.
- **Maximalist.** If a statistic can be computed from the data, it is shown.
- **Multi-page.** Overview · Pies · Holdings · Sectors · Quant & Rankings,
  with a drill-down view for every individual pie and every individual stock.
- **Read-only.** No order placement, ever.

Legend: `[B]` already exists in the backend · `[+]` new backend work ·
`[~]` extends something partial.

---

## I · Headline & Overview (predicted-profit centric)

1. `[+]` Predicted 1Y profit hero — Monte Carlo median terminal value minus current value, account-wide, in £ and %.
2. `[B]` Probability of profit over 1Y (share of MC paths ending above today).
3. `[~]` Predicted-profit confidence band: P5 / P25 / median / P75 / P95 terminal value in £.
4. `[+]` Predicted profit vs the same money invested in the benchmark (forward, not just historical).
5. `[~]` "Profit at risk": 1Y VaR and CVaR in £ shown beside predicted profit (upside/downside framing).
6. `[B]` Live KPI strip: total value, invested, free cash, pie cash, unrealised P&L, realised, FX P&L, net deposits, dividend run-rate.
7. `[+]` Today's move: day P&L in £/% and the holdings that drove it (winners/losers of the day).
8. `[+]` Money-weighted (IRR) vs time-weighted return reconciliation, with inception date.

## II · Performance & Returns

9. `[B]` Reconstructed portfolio value curve (cashflow-aware, anchored to the live account value).
10. `[~]` "Same cash in the benchmark" overlay, with a second selectable benchmark (e.g. All-World VWRL).
11. `[~]` Multi-horizon return table: 1w / 1m / 3m / 6m / 1y / ITD.
12. `[B]` CAGR, total return, average daily return.
13. `[+]` Rolling return chart (rolling 1m / 3m return over time).
14. `[+]` Underwater (drawdown-over-time) chart.
15. `[B]` Best / worst day, plus best/worst week and month with dates.
16. `[B]` Win rate (up vs down days) and longest up/down streaks.
17. `[+]` Monthly returns heatmap (calendar grid of monthly %).
18. `[+]` Daily-return distribution histogram with a normal overlay (skew/kurtosis made visible).

## III · Risk & Tail

19. `[B]` Annualised volatility and downside deviation.
20. `[B]` Sharpe, Sortino, Calmar (risk-free assumption shown, set in config).
21. `[B]` VaR 95/99 and CVaR / expected shortfall (1-day and 1-year).
22. `[B]` Max drawdown, current drawdown, days underwater, recovery estimate.
23. `[B]` Skew and kurtosis (fat-tail diagnostics).
24. `[B]` Ulcer index and Martin (UPI) ratio.
25. `[B]` Omega ratio, gain-to-pain, tail ratio.
26. `[B]` Beta, alpha (annualised), correlation, R² vs benchmark.
27. `[B]` Up-capture / down-capture / batting average vs benchmark.
28. `[B]` Tracking error and information ratio vs benchmark.

## IV · Monte Carlo & Forward Prediction

29. `[B]` Portfolio 1Y Monte Carlo fan chart (bootstrap, percentile bands).
30. `[+]` Selectable MC horizon (1m / 3m / 6m / 1y / 3y).
31. `[+]` Two MC engines — bootstrap (empirical) and parametric GBM — toggle to compare.
32. `[+]` Probability of reaching a target value (config goal) within the horizon.
33. `[~]` Probability of gain > X% and loss > X% (configurable thresholds).
34. `[+]` Expected maximum drawdown over the horizon (path-level statistic).
35. `[+]` Per-pie Monte Carlo (each pie's own fan + terminal distribution).
36. `[+]` Per-stock Monte Carlo (each holding's own forward cone).
37. `[+]` Predicted-profit leaderboard: rank holdings by MC expected £ profit contribution.
38. `[+]` Portfolio cone with benchmark cone overlaid (relative forward prediction).

## V · Per-Pie Analysis (full quant for each of the 6 pies)

39. `[~]` Per-pie drill-down page with its own value curve and full stats block.
40. `[~]` Per-pie Sharpe / Sortino / Calmar / vol / beta / alpha.
41. `[+]` Per-pie Monte Carlo predicted profit + probability of profit.
42. `[+]` Pie target-vs-actual allocation drift (expected vs current slice %, rebalance gap).
43. `[+]` Pie contribution to the whole portfolio (value, P&L, predicted-profit share).
44. `[+]` Pie-vs-pie comparison: rank all pies by return, risk, predicted profit, score.
45. `[+]` Per-pie sector and geography breakdown.
46. `[B]` Per-pie dividend income and reinvested vs cash split.
47. `[+]` Per-pie best/worst slice and internal concentration (HHI).
48. `[+]` Per-pie correlation to the rest of the portfolio (diversification benefit).
49. `[~]` Per-pie status, progress-to-goal, self-managed vs auto-invest.
50. `[+]` Per-pie curve with buy/sell markers reconstructed from order history.

## VI · Per-Stock Analysis (full quant + prediction for each of the 99 holdings)

51. `[+]` Per-stock drill-down page: price history, position details, full analytics.
52. `[~]` Per-stock return since purchase (T212 exact) and trailing 1w/1m/3m/6m/1y (yfinance).
53. `[+]` Per-stock annualised vol, Sharpe, beta vs benchmark, correlation.
54. `[+]` Per-stock max drawdown and current drawdown.
55. `[+]` Per-stock Monte Carlo forward cone + predicted 1Y profit (£ on the held quantity).
56. `[+]` Per-stock momentum (12-1), relative strength vs benchmark, trend slope.
57. `[+]` Per-stock 50/200-day moving averages and golden/death-cross flag.
58. `[+]` Per-stock 52-week range position (where the price sits in its yearly band).
59. `[+]` Per-stock analyst price target (yfinance) and implied upside vs current price.
60. `[+]` Per-stock fundamentals snapshot (market cap, P/E, sector, industry) where available.
61. `[+]` Per-stock contribution to portfolio P&L and to portfolio risk (risk contribution).
62. `[+]` Per-stock weight vs an equal-risk reference weight (informational only).
63. `[+]` Per-stock dividend yield and income contribution.
64. `[+]` Per-stock sensitivity: effect on portfolio risk/return of doubling/halving the position.

## VII · Sector & Thematic Exposure

65. `[+]` Total sector exposure (Technology, Energy, Industrials, …) from yfinance sector data.
66. `[+]` Industry-level breakdown (finer than sector).
67. `[+]` Thematic exposure (Space, Quantum, AI, Defense, Nuclear/Energy) via the configurable tag map.
68. `[+]` Sector P&L attribution (which sectors made or lost money).
69. `[+]` Sector predicted-profit contribution (forward-looking).
70. `[+]` Sector concentration; most- and least-diversified sectors.
71. `[+]` Sector treemap (area = value, colour = return or score).
72. `[+]` Geography × sector matrix (where the risk actually sits).

## VIII · Ranking & Scoring

73. `[+]` Headline ranking by predicted 1Y profit (£) across all 99 holdings.
74. `[+]` Composite 0–100 score per holding (momentum, risk-adjusted return, trend, value, quality sub-scores).
75. `[+]` Rank pies by predicted profit and by composite score.
76. `[+]` "Conviction vs weight" scatter (score vs NAV weight — find under/over-weights).
77. `[~]` Movers & laggards leaderboards across horizons.
78. `[+]` Risk flags: high vol, deep drawdown, expensive valuation, negative momentum.
79. `[+]` Opportunity flags: high score but low weight; strong momentum at reasonable risk.
80. `[+]` Master ranking table — every computed metric, sortable and filterable.

## IX · Correlation & Diversification

81. `[+]` Full holdings correlation matrix (heatmap) from daily returns.
82. `[+]` Pie-level correlation matrix.
83. `[+]` Most-correlated and most-diversifying pairs.
84. `[~]` Effective number of holdings (diversification ratio) and HHI concentration.
85. `[+]` Marginal diversification: which holding cuts portfolio vol the most/least.
86. `[+]` Correlation-to-portfolio ranking per holding.

## X · Income, Cashflow & Behaviour

87. `[B]` Dividend history, 30-day run-rate, annualised projection — by holding and pie.
88. `[+]` Forward dividend calendar / expected income (from yields).
89. `[B]` Deposits/withdrawals timeline and net invested over time.
90. `[B]` Trading activity: fills per day, volume, by source (pie auto-invest vs manual).
91. `[~]` Realised vs unrealised P&L split and turnover.
92. `[+]` Average holding period and cost-basis aging.

## XI · Benchmarking & Live Context (read-only)

93. `[~]` Benchmark panel: S&P 500 and All-World, with the portfolio's relative position.
94. `[B]` Crypto (Hyperliquid) and Polymarket context strips (optional, kept).
95. `[B]` Market clock and session status (LSE / US open/closed).

## XII · System, Generalization & UX

96. `[+]` Fully generalized: any T212 key works; account label/currency read live; nothing personal hardcoded.
97. `[+]` Editable `jarvis.config.json`: name, currency, benchmarks, rf rate, MC settings, goal, thematic tags, per-ticker notes, theme.
98. `[~]` Data-quality / coverage badges (how much of each curve is priced, staleness, last refresh).
99. `[+]` Three switchable frontend designs sharing one data layer; the chosen design is remembered.
100. `[+]` Global drill-down, filter, search; everything cross-links (sector → holding → stock → pie); CSV export of any table.

---

## Build order

1. **Backend + data handling first** (this is where the analytics live):
   config/personalization loader → sector & fundamentals adapter → per-stock
   analytics + Monte Carlo → per-pie full quant + Monte Carlo → ranking /
   predicted-profit engine → correlation engine → new API endpoints.
2. **Then three distinct frontend designs** over the same `/api` surface, for
   Omri to choose from. Designs differ in look and interaction, not in data.
