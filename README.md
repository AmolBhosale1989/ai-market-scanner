# Market Hunt V3 — Broad U.S. Stock Opportunity Scanner

Market Hunt V3 is a broad U.S. technical discovery engine with a lightweight catalyst/news layer.

## Implemented now
- Automatically builds a broad U.S.-listed stock universe from Nasdaq Trader symbol directories
- Batch Yahoo Finance daily-history download (no API key)
- Price and average-dollar-volume filters
- EMA20 / EMA50 / EMA200, SMA200, RSI, ATR, MACD, RVOL
- 20-day relative strength versus SPY
- Early bullish-formation detector: rising EMA50, EMA50 support, EMA200 pivot, compression below resistance, tight range, bull-flag/pullback, volume contraction/expansion
- Stages: DISCOVER → FORMING → ARMED → CONFIRMED, plus EXTENDED rejection
- Daily / weekly / monthly support and resistance
- Minimum 5% clean runway requirement before ARMED / CONFIRMED
- Entry trigger, technical stop, +5% / +8% / +10% targets and R:R
- Catalyst/news enrichment for the strongest technical candidates using Yahoo Finance data
- Fresh-news recency scoring, positive/negative headline keyword scoring, and upcoming-earnings detection
- Catalyst-aware final score and final decision without allowing news alone to create a BUY
- Ranked top opportunities plus full liquid-candidate export
- Streamlit dashboard

## Catalyst layer behavior
The scanner enriches up to 60 of the strongest technical candidates after the broad technical scan. This keeps the free-data prototype practical instead of making thousands of sequential news requests.

It checks:
- recent company news, emphasizing the last 72 hours
- bullish catalyst terms such as guidance raises, estimate beats, contracts, approvals, partnerships and launches
- negative-risk terms such as guidance cuts, offerings/dilution, investigations, recalls and bankruptcies
- upcoming earnings, emphasizing events inside the next 7 days

Catalyst data improves ranking and can block a technically valid trade when fresh negative news is detected. A catalyst does not override weak technicals, poor R:R, insufficient runway, or EXTENDED status.

## Not implemented yet
Deep earnings-estimate/revision models, FDA/PDUFA calendar feeds, conference/investor-day calendars, options flow, social sentiment, live intraday VWAP/opening-range confirmation, alerts, database, and paper-trade journal.

## Install
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Run
Refresh broad ticker universe:
```bash
python -m scanner.main --refresh-universe
```

Fast smoke test:
```bash
python -m scanner.main --limit 300 --top 20
```

Dashboard:
```bash
streamlit run app.py
```

Outputs:
- `outputs/latest_scan.csv`
- `outputs/all_candidates.csv`

## Important
Yahoo Finance is a practical free prototype source and can throttle or omit news/earnings data. The catalyst layer therefore reports NO DATA/ERROR rather than treating missing data as bullish. A production/commercial version should eventually use a licensed structured news and corporate-events provider.

This project is for research and decision support only. It does not guarantee returns or place live trades.
