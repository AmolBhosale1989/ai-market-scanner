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


## Structure-aware entry/stop model
Market Hunt now derives trade risk from visible chart structure instead of a single generic ATR stop.

Developing setups use a small volatility-aware buffer above daily resistance. Stops are anchored to the closest valid recent structure among the 5-day swing low, EMA20, 10-day swing low, daily support and EMA50, with an ATR buffer beneath the anchor.

Safety constraints prevent artificial R/R inflation:
- minimum stop distance is 0.75 ATR
- anchors more than 7% below entry are ignored for the active swing setup
- risk wider than 6% is capped and penalized
- resistance-capped effective target and minimum 2.5 effective R/R remain unchanged

The backtest records entry model, stop basis and risk percentage so this model can be calibrated empirically.


## Pullback/retest entry model
Market Hunt can now choose between a standard resistance breakout and a pullback/retest entry.

A retest candidate is considered only when:
- the technical stage is FORMING or ARMED
- price is above EMA20 and EMA20 is above EMA50
- a real support reference (EMA20, 5-day swing low, or daily support) is roughly 0.5% to 4% below price
- the retest improves effective R/R by at least 0.35 versus the breakout plan
- modeled risk remains between roughly 0.6% and 4.5%

Retest entries use a TOUCH_AND_RECLAIM condition. Historical testing requires the future daily bar to trade through the retest entry price; live confirmation requires the intraday session to touch the retest zone and then recover above the entry while also holding VWAP with sufficient intraday RVOL.

The breakout path remains the fallback when the retest is not structurally valid or does not materially improve asymmetry.


## Retest quality filter
Pullback/retest entries are now much more selective. A retest requires both:
- rising EMA20 over the last five sessions
- 5-day volume at or below the 20-day average

It must also satisfy at least four of six additional confirmations:
- constructive 5-day return
- tight 10-day range
- higher-low structure
- bullish daily close in the upper part of the candle
- RSI in a healthy 48-68 zone
- repeated support respect near the retest reference

The scanner records retest quality score, EMA20 slope, support-touch count, higher-low state and bullish-close state so the historical calibration can measure whether these filters reduce stop-outs.


## Market and sector regime filter
Pullback/retest entries now consider the broader market regime. SPY is scored from price versus EMA20, EMA20 versus EMA50, EMA20 slope, and 5/20-day momentum. Retest entries are disabled in a WEAK market regime; breakout entries remain available if the stock itself is strong enough.

After theme/sector enrichment, a classified retest is also downgraded when its matched theme is WEAK. Unclassified stocks are not rejected solely for lacking a theme tag.

Historical calibration uses the SPY regime as it existed at each signal date, avoiding use of today's market state in past trades. The live scanner applies both current SPY regime and current theme/sector state.


## Event-first catalyst discovery
Market Hunt now scans the most liquid 400 stocks independently of the technical shortlist for upcoming earnings in the next 7 days. This closes the previous gap where a major company could have a known event but remain invisible until the chart had already moved.

The event-first watchlist:
- prioritizes the most liquid tradable stocks
- scans 1-7 days ahead for upcoming earnings
- marks <=3 days as HIGH priority, 3-5 days as MEDIUM and later events as WATCH
- merges current technical state, entry, stop, runway, R/R and market regime after the broad scan
- remains visible even when a stock has no qualifying technical setup yet

Output: `outputs/upcoming_events.csv`, also published automatically to the deployed dashboard.


## Forward-event discovery beyond earnings
The event-first scanner also checks a smaller high-liquidity subset for announced future events with an explicit date in the next seven days. Supported categories include FDA/PDUFA or regulatory decisions, investor/analyst/capital-markets days, conferences/presentations, product launches, clinical-data releases and contract-award announcements. Vague phrases such as "next week" are intentionally ignored unless an explicit date can be parsed.

## Theme classification confidence
Theme matching is now industry/sector-first. A direct Yahoo industry match receives high confidence; industry keywords receive medium confidence; business-summary keywords are low-confidence fallback only. Low-confidence matches receive no theme ranking bonus and cannot veto a retest merely because the matched theme is weak.

## Exchange-aware live monitoring
Live market state now uses the NYSE trading calendar rather than fixed clock assumptions. This covers DST, U.S. exchange holidays and scheduled early closes. The GitHub intraday window is deliberately broad in UTC; the scanner itself decides whether the NYSE session is actually live.

First-observation actionable states now generate alerts. A ticker first seen already TRIGGERED/LIVE_CONFIRMED/INVALIDATED/TARGET_HIT can therefore alert immediately instead of waiting for a second state transition.

## Forward paper-trading journal
The intraday monitor automatically maintains a persistent paper journal for ARMED/CONFIRMED candidates. It records first/last seen time, planned entry, stop, effective target and R/R, entry model, market regime, theme/catalyst context, monitor state and eventual outcome. Trigger, live-confirmed and close timestamps are retained, together with forward return and R-multiple when a paper trade closes.


## Alpha Vantage earnings calendar
Upcoming earnings discovery uses Alpha Vantage's broad earnings calendar and intersects it with Market Hunt's liquid tradable universe. Set the GitHub Actions repository secret `ALPHA_VANTAGE_API_KEY` to enable this layer. Yahoo earnings-calendar endpoints are no longer used because repeated authorization/crumb failures made them unreliable in GitHub Actions.

The scanner writes `outputs/event_status.csv` so the dashboard can distinguish a healthy empty event window from a missing provider key or provider failure.
