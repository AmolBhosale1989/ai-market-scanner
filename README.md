# AI Market Scanner V2

Agentic AI U.S. stock opportunity scanner focused on identifying high-quality 10–15% move setups.

## V2 Starter Scope
- Market regime analysis
- Sector ranking
- Stock technical scoring
- Volume / RVOL analysis
- Risk / target generation
- Opportunity and Risk scores
- BUY / WAIT / WATCH / NO TRADE decision framework
- Streamlit mobile dashboard
- Backtesting scaffold
- Agent architecture scaffold
- GitHub Actions daily workflow scaffold

## Later modules
- News catalyst agent
- Options agent
- Social sentiment agent
- Historical +10% / +15% probability engine
- Bull vs Bear debate agents
- Supabase storage
- Telegram alerts
- Paper-trading journal

## Important
This project is for research and decision support. It does not guarantee returns and does not place live trades.

## Run locally
1. Install Python 3.11+
2. Create a virtual environment
3. Install dependencies:
   pip install -r requirements.txt
4. Run scanner:
   python -m scanner.main
5. Run dashboard:
   streamlit run app.py
