import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(page_title="Market Hunt V3",page_icon="📈",layout="wide")
st.title("📈 Market Hunt V3 — U.S. Opportunity Scanner")
st.caption("Technical discovery + theme momentum + optional catalysts + intraday state monitoring. Research/decision support only.")

out=Path("outputs/latest_scan.csv")
all_out=Path("outputs/all_candidates.csv")
health_out=Path("outputs/scan_health.csv")
themes_out=Path("outputs/trending_themes.csv")
live_out=Path("outputs/intraday_live.csv")
transitions_out=Path("outputs/state_transitions.csv")
tradable_out=Path("outputs/tradable_universe.csv")

if health_out.exists():
    h=pd.read_csv(health_out)
    if len(h):
        r=h.iloc[0]
        c=st.columns(4)
        c[0].metric("Scan",str(r.get("status","?")))
        c[1].metric("Master universe",f"{int(r.get('master_universe_symbols',0)):,}")
        c[2].metric("Tradable universe",f"{int(r.get('tradable_symbols',0)):,}")
        c[3].metric("Analyzable",f"{float(r.get('analyzable_coverage',0))*100:.1f}%")

if live_out.exists():
    live=pd.read_csv(live_out)
    st.subheader("⚡ Live Monitor")
    live_cols=["ticker","monitor_state","previous_state","state_changed","live_price","entry_trigger",
               "stop","live_vwap","opening_range_high","intraday_rvol","live_confirmation_score",
               "theme","catalyst_status","live_trade_action","checked_at_et"]
    st.dataframe(live[[c for c in live_cols if c in live.columns]],use_container_width=True,hide_index=True)

if transitions_out.exists():
    transitions=pd.read_csv(transitions_out)
    if len(transitions):
        with st.expander("State transition history"):
            st.dataframe(transitions.tail(100).iloc[::-1],use_container_width=True,hide_index=True)

if themes_out.exists():
    themes=pd.read_csv(themes_out)
    st.subheader("🔥 Trending Themes")
    theme_cols=["theme_rank","theme","etf","theme_score","theme_state","ret5_pct","ret20_pct","rel5_vs_spy","rel20_vs_spy"]
    st.dataframe(themes.head(12)[[c for c in theme_cols if c in themes.columns]],use_container_width=True,hide_index=True)

if out.exists():
    df=pd.read_csv(out)
    st.subheader("Top Market Hunt Opportunities")
    stages=sorted(df["stage"].dropna().unique())
    selected=st.multiselect("Stage",stages,default=stages)
    view=df[df["stage"].isin(selected)] if selected else df
    priority=["ticker","company_name","price","stage","theme","theme_state","theme_score",
              "market_hunt_score","final_decision","market_regime_state","market_regime_score","theme_state","sector_regime_ok","catalyst_score","catalyst_status",
              "entry_trigger","entry_model","entry_condition","retest_reference","retest_distance_pct",
              "retest_quality_score","ema20_slope5_pct","support_touch_count","higher_low","bullish_close","entry_buffer_pct",
              "stop","stop_basis","stop_anchor","risk_pct",
              "effective_target","effective_rr","target_5","target_8","target_10","rr_to_8pct",
              "runway_to_next_resistance_pct","pattern"]
    cols=[c for c in priority if c in view.columns]+[c for c in view.columns if c not in priority]
    st.dataframe(view[cols],use_container_width=True,hide_index=True)
else:
    st.info("No base scan results yet.")

if tradable_out.exists():
    with st.expander("Tradable universe"):
        t=pd.read_csv(tradable_out)
        count=int(t["tradable"].sum()) if "tradable" in t.columns else len(t)
        st.write(f"{count:,} stocks currently pass the tradability gate.")
        st.dataframe(t.head(500),use_container_width=True,hide_index=True)

if all_out.exists():
    with st.expander("All deep-scanned candidates"):
        st.dataframe(pd.read_csv(all_out),use_container_width=True,hide_index=True)

st.divider()
st.subheader("Pipeline")
st.write("5k+ master universe → fast liquidity/price gate → tradable universe → themes → early technical structure → D/W/M S/R → runway/R:R → optional catalyst → ARMED/CONFIRMED → 15-minute live VWAP/ORB/RVOL state monitor → BUY/FAILED/INVALIDATED")
