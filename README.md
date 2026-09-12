# Market Hunt V3 — Broad U.S. Stock Opportunity Scanner

Market Hunt V3 scans a broad U.S. stock universe for early technical setups, ranks trending themes, validates optional catalysts/news, and applies live intraday confirmation to advanced candidates.

## Implemented
- Broad U.S.-listed universe from Nasdaq Trader directories
- Data-health gate with retry logic and scan coverage report
- Trending-theme ranking using liquid ETF proxies and 5/20/60-day momentum vs SPY
- Candidate theme tagging using company sector/industry/business profile
- Theme momentum is an additive ranking bonus, never a mandatory trade filter
- Daily technical engine: EMA20/50/200, SMA200, RSI, ATR, MACD, RVOL, relative strength
- Early formation stages: DISCOVER → FORMING → ARMED → CONFIRMED, with EXTENDED rejection
- Daily / weekly / monthly support and resistance
- Minimum 5% clean runway before ARMED / CONFIRMED
- Entry trigger, stop, +5 / +8 / +10 targets and R:R
- Verified catalyst/news enrichment with ticker/company relevance validation
- Positive catalyst is optional; fresh negative catalyst remains a risk veto
- Upcoming-earnings detection
- Live confirmation using 5-minute VWAP, 30-minute opening range, trigger state and time-normalized intraday RVOL
- Streamlit dashboard and CSV exports

## Trending themes
The scanner currently tracks themes including semiconductors, AI/robotics, cloud, cybersecurity, biotech, genomics, defense/aerospace, energy, oil services, uranium/nuclear, copper/mining, gold miners, clean energy, infrastructure, homebuilders, regional banks, fintech and cannabis.

Each theme gets a momentum score based primarily on relative performance versus SPY across 5, 20 and 60 trading days plus ETF trend structure. Output is saved to:
- `outputs/trending_themes.csv`

Theme strength can improve ranking, but a stock can still qualify without a leading-theme tag if its technical structure, runway, R:R and live confirmation are strong.

## Live signal rule
A live BUY can only be produced when the U.S. regular session is live and the candidate is already ARMED/CONFIRMED. The prototype requires price above VWAP, above the completed opening-range high, at/above the technical trigger, intraday RVOL >= 1.20, sufficient runway/R:R, and no fresh negative catalyst risk. A positive catalyst is optional.

## Outputs
- `outputs/trending_themes.csv`
- `outputs/latest_scan.csv`
- `outputs/all_candidates.csv`
- `outputs/scan_health.csv`

## Still to add
Deeper earnings-estimate/revision models, FDA/PDUFA and conference calendars, options flow, social sentiment, alerts, database, and paper-trade journal.

Yahoo Finance remains a practical free prototype source and can throttle or omit data. The health gate prevents low-coverage scans from being presented as valid.

This project is for research and decision support only. It does not guarantee returns or place live trades.
