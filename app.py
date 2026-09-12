import os
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Market Hunt V3",page_icon="📈",layout="wide")
st.title("📈 Market Hunt V3 — U.S. Opportunity Scanner")
st.caption("Technical discovery + theme momentum + optional catalysts + intraday state monitoring. Research/decision support only.")

REMOTE_BASE=os.getenv(
    "SCAN_DATA_BASE_URL",
    "https://raw.githubusercontent.com/AmolBhosale1989/ai-market-scanner/scan-data/dashboard-data",
).rstrip("/")
LOCAL_DIR=Path("outputs")

@st.cache_data(ttl=60,show_spinner=False)
def _remote_csv(name: str):
    url=f"{REMOTE_BASE}/{name}"
    r=requests.get(url,timeout=8)
    r.raise_for_status()
    return pd.read_csv(StringIO(r.text))

def load_csv(name: str):
    try:
        return _remote_csv(name),"scan-data"
    except Exception:
        path=LOCAL_DIR/name
        if path.exists():
            try:
                return pd.read_csv(path),"local"
            except Exception:
                pass
    return pd.DataFrame(),"unavailable"

scan_meta,scan_meta_source=load_csv("scan_metadata.csv")
live_meta,live_meta_source=load_csv("live_metadata.csv")
health,_=load_csv("scan_health.csv")
live,_=load_csv("intraday_live.csv")
transitions,_=load_csv("state_transitions.csv")
themes,_=load_csv("trending_themes.csv")
df,_=load_csv("latest_scan.csv")
tradable,_=load_csv("tradable_universe.csv")
all_candidates,_=load_csv("all_candidates.csv")
events,_=load_csv("upcoming_events.csv")
event_status,_=load_csv("event_status.csv")
journal,_=load_csv("paper_journal.csv")
performance,_=load_csv("performance_summary.csv")
performance_by_setup,_=load_csv("performance_by_setup.csv")
calibration,_=load_csv("probability_calibration.csv")
monitor_health,_=load_csv("monitor_health.csv")

if not scan_meta.empty:
    stamp=str(scan_meta.iloc[0].get("generated_at_utc",""))
    st.caption(f"Base scan data: {stamp} UTC · source: {scan_meta_source}")
else:
    st.caption(f"Base scan source: {scan_meta_source}")

if not live_meta.empty:
    live_stamp=str(live_meta.iloc[0].get("updated_at_utc",""))
    st.caption(f"Live monitor data: {live_stamp} UTC · source: {live_meta_source}")

if not health.empty:
    r=health.iloc[0]
    c=st.columns(4)
    c[0].metric("Scan",str(r.get("status","?")))
    c[1].metric("Master universe",f"{int(r.get('master_universe_symbols',0)):,}")
    c[2].metric("Tradable universe",f"{int(r.get('tradable_symbols',0)):,}")
    c[3].metric("Analyzable",f"{float(r.get('analyzable_coverage',0))*100:.1f}%")
else:
    st.warning("Scan health data is not available yet.")

if not monitor_health.empty:
    mh=monitor_health.iloc[0]
    st.caption(
        f"Live monitor health: {mh.get('status','?')} · monitored {int(mh.get('monitored_candidates',0))} · "
        f"alerts {int(mh.get('alerts_generated',0))} · Telegram configured {bool(mh.get('telegram_configured',False))}"
    )

if not performance.empty:
    st.subheader("📊 Forward Validation")
    p=performance.iloc[0]
    c=st.columns(5)
    c[0].metric("Signals",int(p.get("signals",0)))
    c[1].metric("Closed",int(p.get("closed_signals",0)))
    c[2].metric("Win rate",f"{float(p.get('win_rate_pct',0) or 0):.1f}%")
    c[3].metric("Avg R",f"{float(p.get('avg_r_multiple',0) or 0):.2f}")
    c[4].metric("Target hit",f"{float(p.get('target_hit_rate_pct',0) or 0):.1f}%")
    if not performance_by_setup.empty:
        with st.expander("Performance by setup / regime / theme"):
            st.dataframe(performance_by_setup,use_container_width=True,hide_index=True)

if not calibration.empty:
    st.subheader("🎯 Probability Calibration")
    usable=calibration[calibration["calibration_status"].eq("USABLE")] if "calibration_status" in calibration.columns else pd.DataFrame()
    if usable.empty:
        st.info("Calibration is collecting forward samples. Probabilities remain provisional until each score bucket has enough closed trades.")
    st.dataframe(calibration,use_container_width=True,hide_index=True)

if not live.empty:
    st.subheader("⚡ Live Monitor")
    live_cols=["ticker","monitor_state","previous_state","state_changed","live_price","entry_trigger",
               "stop","live_vwap","opening_range_high","intraday_rvol","live_confirmation_score",
               "theme","catalyst_status","live_trade_action","checked_at_et"]
    st.dataframe(live[[c for c in live_cols if c in live.columns]],use_container_width=True,hide_index=True)

if not transitions.empty:
    with st.expander("State transition history"):
        st.dataframe(transitions.tail(100).iloc[::-1],use_container_width=True,hide_index=True)

if not journal.empty:
    st.subheader("🧪 Paper Trading Journal")
    journal_cols=["ticker","first_seen_et","stage","entry_trigger","stop","effective_target","effective_rr",
                  "entry_model","market_regime_state","theme","catalyst_status","paper_state",
                  "last_price","outcome","return_pct","r_multiple","closed_at_et"]
    st.dataframe(journal[[c for c in journal_cols if c in journal.columns]].tail(200).iloc[::-1],
                 use_container_width=True,hide_index=True)

if not event_status.empty:
    er=event_status.iloc[0]
    provider=str(er.get("provider",""))
    status=str(er.get("status",""))
    detail=str(er.get("detail",""))
    st.caption(f"Event calendar status: {provider} · {status}" + (f" · {detail}" if detail else ""))

if not events.empty:
    st.subheader("📅 Upcoming Events")
    event_cols=["ticker","company_name","event_type","event_date_utc","earnings_report_time","days_to_event","event_priority",
                "pre_event_decision","event_opportunity_score","pre_earnings_intel_score",
                "eps_estimate","forward_eps_estimate","forward_eps_analyst_count",
                "estimate_revision_score","eps_revision_7d_pct","eps_revision_30d_pct","eps_revision_60d_pct",
                "eps_revision_90d_pct","beat_rate_pct","median_surprise_pct",
                "latest_surprise_pct","surprise_streak","prior_earnings_reaction_avg_pct",
                "prior_earnings_reaction_abs_avg_pct","prior_earnings_positive_reaction_rate",
                "pre_event_compression_score","pre_event_range10_pct","pre_event_vol5_vs20",
                "guidance_revision_score","guidance_positive_mentions","guidance_negative_mentions",
                "revision_up_mentions","revision_down_mentions","options_implied_move_pct",
                "implied_move_vs_5pct","options_expiry","options_data_status","pre_event_setup_state","price","technical_score",
                "entry_trigger","entry_model","stop","effective_target","effective_rr",
                "market_regime_state","avg_dollar_volume20"]
    st.dataframe(events[[c for c in event_cols if c in events.columns]].head(100),
                 use_container_width=True,hide_index=True)

if not themes.empty:
    st.subheader("🔥 Trending Themes")
    theme_cols=["theme_rank","theme","etf","theme_score","theme_state","ret5_pct","ret20_pct","rel5_vs_spy","rel20_vs_spy"]
    st.dataframe(themes.head(12)[[c for c in theme_cols if c in themes.columns]],use_container_width=True,hide_index=True)

if not df.empty:
    st.subheader("Top Market Hunt Opportunities")
    stages=sorted(df["stage"].dropna().unique()) if "stage" in df.columns else []
    selected=st.multiselect("Stage",stages,default=stages)
    view=df[df["stage"].isin(selected)] if selected and "stage" in df.columns else df
    priority=["ticker","company_name","price","stage","theme","theme_state","theme_score",
              "market_hunt_score","final_decision","market_regime_state","market_regime_score","sector_regime_ok",
              "catalyst_score","catalyst_status","entry_trigger","entry_model","entry_condition",
              "retest_reference","retest_distance_pct","retest_quality_score","ema20_slope5_pct",
              "support_touch_count","higher_low","bullish_close","entry_buffer_pct","stop","stop_basis",
              "stop_anchor","risk_pct","effective_target","effective_rr","target_5","target_8","target_10",
              "rr_to_8pct","runway_to_next_resistance_pct","pattern"]
    cols=[c for c in priority if c in view.columns]+[c for c in view.columns if c not in priority]
    st.dataframe(view[cols],use_container_width=True,hide_index=True)
else:
    st.info("No base scan results are available yet. The next successful full scan will publish them automatically.")

if not tradable.empty:
    with st.expander("Tradable universe"):
        count=int(tradable["tradable"].sum()) if "tradable" in tradable.columns else len(tradable)
        st.write(f"{count:,} stocks currently pass the tradability gate.")
        st.dataframe(tradable.head(500),use_container_width=True,hide_index=True)

if not all_candidates.empty:
    with st.expander("All deep-scanned candidates"):
        st.dataframe(all_candidates,use_container_width=True,hide_index=True)

st.divider()
st.subheader("Pipeline")
st.write("5k+ master universe → fast liquidity/price gate → tradable universe → themes → early technical structure → D/W/M S/R → runway/R:R → optional catalyst → ARMED/CONFIRMED → 15-minute live VWAP/ORB/RVOL state monitor → BUY/FAILED/INVALIDATED")
