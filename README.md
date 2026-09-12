# Market Hunt V3 — Broad U.S. Stock Opportunity Scanner

Market Hunt V3 keeps a broad U.S. master universe for discovery, but it no longer performs expensive deep analysis on every listed symbol.

## Two-stage universe architecture

### Pass 1 — Broad master universe
The scanner starts with roughly 5,000+ U.S.-listed common stocks from Nasdaq Trader directories. It performs only a lightweight 3-month daily-data check.

A stock must currently pass:
- price >= $5
- 20-day average share volume >= 500,000
- 20-day average dollar volume >= $20 million

ETFs, warrants, rights, units, preferred shares and similar non-common-stock instruments are already excluded by the universe builder.

### Pass 2 — Tradable universe
Only stocks passing the first gate receive the more expensive one-year technical analysis, pattern detection, support/resistance, theme tagging, catalyst enrichment and live confirmation.

This keeps broad-market coverage while avoiding wasted deep scans on illiquid microcaps and other names we would not realistically trade.

The resulting eligibility table is saved to:
- `outputs/tradable_universe.csv`

## Implemented
- Broad U.S.-listed master universe
- Two-pass tradability filter before deep analysis
- Data-health gate with retry logic
- Trending-theme ranking using liquid ETF proxies and 5/20/60-day momentum vs SPY
- Candidate theme tagging; theme strength is a bonus, not mandatory
- EMA/RSI/MACD/ATR/RVOL/relative-strength technical engine
- DISCOVER → FORMING → ARMED → CONFIRMED stages
- EXTENDED/chase protection
- Daily / weekly / monthly support and resistance
- Entry, stop, +5/+8/+10 targets and R:R
- Optional positive catalyst/news enrichment
- Fresh negative catalyst risk veto
- Live 5-minute VWAP, opening-range, trigger and intraday-RVOL confirmation
- Streamlit dashboard and CSV outputs

## Live signal rule
A live BUY requires an ARMED/CONFIRMED setup, adequate runway/R:R and live technical confirmation. A positive catalyst and leading theme can increase confidence/ranking but are not mandatory.

## Outputs
- `outputs/tradable_universe.csv`
- `outputs/trending_themes.csv`
- `outputs/latest_scan.csv`
- `outputs/all_candidates.csv`
- `outputs/scan_health.csv`

Yahoo Finance remains a practical free prototype data source and can throttle or omit data. Health gates prevent incomplete scans from being treated as valid.

This project is for research and decision support only. It does not guarantee returns or place live trades.
