# Market Hunt V3 — Broad U.S. Stock Opportunity Scanner

Market Hunt V3 scans a broad U.S. stock universe for early technical setups, validates catalysts/news, and applies a live intraday confirmation layer to advanced candidates.

## Implemented
- Broad U.S.-listed universe from Nasdaq Trader directories
- Data-health gate with retry logic and scan coverage report
- Daily technical engine: EMA20/50/200, SMA200, RSI, ATR, MACD, RVOL, relative strength
- Early formation stages: DISCOVER → FORMING → ARMED → CONFIRMED, with EXTENDED rejection
- Daily / weekly / monthly support and resistance
- Minimum 5% clean runway before ARMED / CONFIRMED
- Entry trigger, stop, +5 / +8 / +10 targets and R:R
- Verified catalyst/news enrichment with ticker/company relevance validation
- Upcoming-earnings detection
- Live confirmation for top advanced setups using 5-minute data:
  - regular-session VWAP
  - 30-minute opening range
  - trigger reached/not reached
  - time-normalized cumulative intraday RVOL versus prior sessions
  - explicit market-open/stale-session protection
- Streamlit dashboard and CSV exports

## Live signal rule
A live BUY can only be produced when the U.S. regular session is live and the candidate is already ARMED/CONFIRMED. The prototype requires the price above VWAP, above the completed opening-range high, at/above the technical trigger, intraday RVOL >= 1.20, sufficient runway/R:R, and no fresh negative catalyst risk. A positive catalyst is optional: it adds confidence and a +CATALYST label, but is not required for BUY.

When the market is closed, premarket, or intraday data is stale, the scanner returns WAIT rather than a live BUY.

## Outputs
- `outputs/latest_scan.csv`
- `outputs/all_candidates.csv`
- `outputs/scan_health.csv`

## Still to add
Theme/sector momentum scoring, deeper earnings-estimate/revision models, FDA/PDUFA and conference calendars, options flow, social sentiment, alerts, database, and paper-trade journal.

Yahoo Finance remains a practical free prototype source and can throttle or omit data. The health gate prevents low-coverage scans from being presented as valid.

This project is for research and decision support only. It does not guarantee returns or place live trades.
