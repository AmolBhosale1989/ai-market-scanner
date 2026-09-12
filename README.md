# Market Hunt V3 — U.S. Opportunity Scanner

Market Hunt V3 uses a broad exchange-listed master universe as the discovery perimeter, filters to a liquid tradable universe, ranks themes, finds early technical structures and monitors actionable names intraday.

## Trade-quality engine
The scanner now uses resistance-capped effective risk/reward:
- mechanical +8% target is still shown for reference
- the actionable target is `min(+8% target, nearest higher weekly/monthly resistance)`
- `effective_rr` is calculated from that realistic target and the technical stop
- ARMED/CONFIRMED requires clean runway and effective R/R >= 2.5 by default

This prevents a setup from qualifying merely because a theoretical +8% target looks attractive when major resistance is closer.

## Catalyst freshness
Catalyst relevance and freshness are separate:
- <=24h: full weight
- 24–72h: 80% keyword weight
- 72h–7d: context only, heavily discounted
- >7d: historical context, zero current-news contribution

ACTIVE/STRONG catalyst status now requires genuinely fresh relevant news or a near-term earnings event. Historical headlines can remain visible but cannot create an active catalyst tag. Positive catalysts remain optional; a fresh material negative catalyst remains a risk veto.

## Historical backtest framework
Run:
`python -m scanner.backtest`

Default behavior:
- uses the most liquid names from `outputs/tradable_universe.csv`
- performs walk-forward historical signal generation
- only evaluates historically ARMED/CONFIRMED setups meeting today's runway/effective-R:R rules
- checks whether the trigger was reached over the next 7 sessions
- evaluates stop, resistance-capped target or time exit
- uses a conservative stop-first assumption if stop and target are both touched on the same daily bar

Outputs:
- `outputs/backtest_trades.csv`
- `outputs/backtest_summary.csv`

The backtest is a validation framework, not proof of future performance. Daily OHLC cannot establish exact intraday ordering, slippage or fill quality.

## Current pipeline
5,000+ master symbols -> fast tradability gate -> tradable universe -> theme momentum -> pre-move structure -> D/W/M support/resistance -> resistance-capped effective R/R -> optional fresh catalyst -> ARMED/CONFIRMED -> 15-minute live VWAP/ORB/RVOL state monitor.

Research and decision support only. No guaranteed returns and no automatic trade execution.


## R/R calibration workflow
A dedicated calibration run now tests the most liquid 100 stocks across three years and compares effective R/R thresholds of 1.5, 2.0, 2.5 and 3.0. This is intended to calibrate selectivity from historical outcomes instead of lowering the 2.5 threshold merely to produce more trades.
