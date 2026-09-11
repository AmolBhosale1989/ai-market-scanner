# Market Hunt V3 — Broad U.S. Stock Opportunity Scanner

This branch upgrades the original 18-symbol prototype into a broad U.S. discovery engine.

## Implemented now
- Automatically builds a broad U.S.-listed stock universe from Nasdaq Trader symbol directories
- Batch Yahoo Finance daily-history download (no API key)
- Price and average-dollar-volume filters
- EMA20 / EMA50 / EMA200, SMA200, RSI, ATR, MACD, RVOL
- 20-day relative strength versus SPY
- Early bullish-formation detector: rising EMA50, EMA50 support, EMA200 pivot, compression below resistance, tight range, bull-flag/pullback, volume contraction/expansion
- Stages: DISCOVER → FORMING → ARMED → CONFIRMED
- Daily / weekly / monthly support and resistance
- Entry trigger, technical stop, +5% / +8% / +10% targets and R:R
- Ranked top opportunities plus full liquid-candidate export
- Streamlit dashboard

## Not implemented yet
Catalyst/news calendar, earnings-estimate analysis, options flow, social sentiment, live intraday VWAP/opening-range confirmation, alerts, database, and paper-trade journal.

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
Yahoo Finance is a practical free prototype data source but may throttle very large scans. Validate the discovery logic before paying for a dedicated market-data API.

This project is for research and decision support only. It does not guarantee returns or place live trades.
