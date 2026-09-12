import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(page_title="Market Hunt V3", page_icon="📈", layout="wide")
st.title("📈 Market Hunt V3 — Broad U.S. Opportunity Scanner")
st.caption("Broad-universe technical + catalyst discovery. Research/decision support only; no guaranteed returns.")

out = Path("outputs/latest_scan.csv")
all_out = Path("outputs/all_candidates.csv")

if out.exists():
    df = pd.read_csv(out)
    st.subheader("Top Market Hunt Opportunities")
    stages = sorted(df["stage"].dropna().unique())
    selected = st.multiselect("Stage", stages, default=stages)
    view = df[df["stage"].isin(selected)] if selected else df
    priority = [
        "ticker", "price", "stage", "final_score", "final_decision",
        "catalyst_score", "catalyst_status", "catalyst_type", "catalyst_bias",
        "catalyst_headline", "earnings_days", "entry_trigger", "stop",
        "target_5", "target_8", "target_10", "rr_to_8pct",
        "runway_to_next_resistance_pct", "pattern",
    ]
    cols = [c for c in priority if c in view.columns] + [c for c in view.columns if c not in priority]
    st.dataframe(view[cols], use_container_width=True, hide_index=True)
else:
    st.info("No scan results yet. Run: python -m scanner.main --refresh-universe")

if all_out.exists():
    with st.expander("All liquid scanned candidates"):
        st.dataframe(pd.read_csv(all_out), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Pipeline")
st.write("Broad U.S. universe → liquidity → EMA/RS/RVOL → FORMING patterns → D/W/M support & resistance → runway/R:R → top-candidate catalyst/news + earnings check → ranked shortlist")
