# Market Hunt V3 — U.S. Opportunity Scanner

Market Hunt V3 uses a broad exchange-listed master universe only as the discovery perimeter, then filters to a liquid tradable universe before expensive analysis.

Core pipeline:
5,000+ master symbols -> fast price/liquidity gate -> tradable universe -> theme momentum -> pre-move technical structure -> D/W/M support/resistance -> runway/R:R -> optional catalyst/news -> ARMED/CONFIRMED -> 15-minute intraday state monitor.

Tradability defaults:
- price >= $5
- 20-day average share volume >= 500,000
- 20-day average dollar volume >= $20 million

Intraday state engine:
- ARMED
- TRIGGERED
- LIVE_CONFIRMED
- FAILED_BREAKOUT
- INVALIDATED

Positive catalysts and leading themes are bonuses, not mandatory trade gates. Fresh negative catalyst risk remains a veto. The live monitor uses 5-minute data, VWAP, the completed 30-minute opening range, technical trigger state and time-normalized intraday RVOL.

Automation:
- full base scan on weekdays before the U.S. session
- intraday monitor every 15 minutes across the U.S. market-time window
- persistent state using GitHub Actions cache
- intraday artifacts and GitHub job-summary alerts
- optional Telegram alerts when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID repository secrets are configured

Dashboard:
The Streamlit dashboard shows scan health, tradable-universe size, trending themes, live monitor state, transition history and ranked opportunities. render.yaml is included for Render deployment.

Main outputs:
outputs/tradable_universe.csv
outputs/trending_themes.csv
outputs/latest_scan.csv
outputs/all_candidates.csv
outputs/scan_health.csv
outputs/intraday_live.csv
outputs/state_transitions.csv
outputs/live_alerts.txt

Yahoo Finance remains a free prototype source and can throttle or omit data. Health gates are used so incomplete scans are not presented as valid.

Research and decision support only. No guaranteed returns and no automatic trade execution.
