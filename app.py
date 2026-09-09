import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(page_title="AI Market Scanner", page_icon="📈", layout="wide")

st.title("📈 AI U.S. 10–15% Opportunity Scanner")
st.caption("V2 starter dashboard — research only, no guaranteed returns.")

out = Path("outputs/latest_scan.csv")

if out.exists():
    df = pd.read_csv(out)
    st.subheader("Top Opportunities")
    st.dataframe(df, use_container_width=True)
else:
    st.info("No scan results yet. Run: python -m scanner.main")

st.divider()
st.subheader("Decision Framework")
st.write("Market → Sector → Stock → Volume → Risk/Reward → AI Evidence → BUY / WAIT / WATCH / NO TRADE")
