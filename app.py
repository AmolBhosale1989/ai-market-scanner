import os
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Market Hunt V3", page_icon="⚡", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
:root{--bg:#07101d;--panel:#0e1b2d;--panel2:#12243a;--line:#203a58;--text:#f2f7ff;--muted:#91a6bf;--green:#65e6b2;--amber:#ffd166;--blue:#73b7ff}
.stApp{background:radial-gradient(circle at 8% 0%,#152b47 0,var(--bg) 38%);color:var(--text)}
.block-container{max-width:1380px;padding-top:1.25rem;padding-bottom:5rem}
.hero{padding:24px 26px;border:1px solid var(--line);border-radius:24px;background:linear-gradient(135deg,#142b48,#0c1727 62%);
box-shadow:0 18px 45px #0006;margin-bottom:16px}.eyebrow{font-size:.75rem;letter-spacing:.14em;color:var(--green);font-weight:800}
.hero h1{font-size:clamp(2rem,5vw,3.35rem);line-height:1;margin:.4rem 0}.hero p{color:var(--muted);max-width:760px;margin:.7rem 0 0}
[data-testid="stMetric"]{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:18px;padding:14px 15px;box-shadow:0 10px 28px #0004}
[data-testid="stMetricLabel"]{color:var(--muted)}[data-testid="stMetricValue"]{color:var(--text)}
.status{display:inline-flex;gap:7px;align-items:center;padding:6px 11px;border:1px solid #275171;border-radius:999px;background:#10253a;color:var(--blue);font-size:.78rem}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 12px var(--green)}
.section-note{color:var(--muted);font-size:.88rem;margin-top:-.55rem;margin-bottom:.8rem}
.signal{border:1px solid var(--line);border-radius:18px;padding:16px;background:linear-gradient(145deg,#102039,#0c1727);margin:.4rem 0}
.signal b{font-size:1.15rem}.good{color:var(--green)}.warn{color:var(--amber)}.muted{color:var(--muted)}
.stTabs [data-baseweb="tab-list"]{gap:8px;overflow-x:auto}.stTabs [data-baseweb="tab"]{background:#0c192a;border:1px solid #1e3855;border-radius:999px;padding:8px 16px}
.stTabs [aria-selected="true"]{background:#183353!important;color:#fff!important}
div[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:16px;overflow:hidden}
@media(max-width:640px){.block-container{padding:1rem .72rem 5rem}.hero{padding:19px;border-radius:20px}.hero p{font-size:.9rem}
[data-testid="stMetric"]{padding:11px}.stTabs [data-baseweb="tab"]{padding:7px 12px}}
</style>""", unsafe_allow_html=True)

REMOTE_BASE = os.getenv("SCAN_DATA_BASE_URL", "https://raw.githubusercontent.com/AmolBhosale1989/ai-market-scanner/scan-data/dashboard-data").rstrip("/")
LOCAL_DIR = Path("outputs")

@st.cache_data(ttl=60, show_spinner=False)
def remote_csv(name):
    response = requests.get(f"{REMOTE_BASE}/{name}", timeout=8)
    response.raise_for_status()
    return pd.read_csv(StringIO(response.text))

def load_csv(name):
    try:
        return remote_csv(name), "published"
    except Exception:
        path = LOCAL_DIR / name
        if path.exists():
            try: return pd.read_csv(path), "local"
            except Exception: pass
    return pd.DataFrame(), "unavailable"

def number(value, default=0):
    try: return float(value)
    except (TypeError, ValueError): return default

def columns(frame, preferred):
    return [name for name in preferred if name in frame.columns]

files = {
    "scan_meta":"scan_metadata.csv", "live_meta":"live_metadata.csv", "health":"scan_health.csv",
    "live":"intraday_live.csv", "transitions":"state_transitions.csv", "themes":"trending_themes.csv",
    "recommendations":"recommended_trades.csv", "watchlist":"watchlist.csv",
    "picks":"latest_scan.csv", "tradable":"tradable_universe.csv", "candidates":"all_candidates.csv",
    "events":"upcoming_events.csv", "event_status":"event_status.csv", "journal":"paper_journal.csv",
    "performance":"performance_summary.csv", "performance_setup":"performance_by_setup.csv",
    "calibration":"probability_calibration.csv", "monitor":"monitor_health.csv", "gate":"validation_gate.csv",
}
data, sources = {}, {}
for key, filename in files.items():
    data[key], sources[key] = load_csv(filename)

st.markdown("""<div class="hero"><div class="eyebrow">PERSONAL RESEARCH TERMINAL · V3</div>
<h1>Market Hunt</h1><p>Broad U.S. discovery, leading themes, multi-timeframe structure,
event intelligence and live confirmation—distilled into actionable research states.</p></div>""", unsafe_allow_html=True)

scan_meta, live_meta, health, monitor = data["scan_meta"], data["live_meta"], data["health"], data["monitor"]
scan_stamp = str(scan_meta.iloc[0].get("generated_at_utc", "Waiting for first scan")) if not scan_meta.empty else "Waiting for first scan"
live_stamp = str(live_meta.iloc[0].get("updated_at_utc", "Waiting for monitor")) if not live_meta.empty else "Waiting for monitor"
source_state = "CONNECTED" if sources["picks"] != "unavailable" else "WAITING"
st.markdown(f'<span class="status"><span class="dot"></span>{source_state} · base {sources["picks"]} · refreshes every 60s</span>', unsafe_allow_html=True)
st.caption(f"Base scan: {scan_stamp} UTC · Live monitor: {live_stamp} UTC")

h = health.iloc[0] if not health.empty else {}
mh = monitor.iloc[0] if not monitor.empty else {}
m1,m2,m3,m4,m5 = st.columns(5)
m1.metric("Scan health", str(h.get("status", "WAITING")))
m2.metric("Master universe", f'{int(number(h.get("master_universe_symbols"))):,}')
m3.metric("Tradable", f'{int(number(h.get("tradable_symbols"))):,}')
m4.metric("Analyzable", f'{number(h.get("analyzable_coverage"))*100:.1f}%')
m5.metric("Live alerts", f'{int(number(mh.get("alerts_generated"))):,}')

overview, opportunities, live_tab, event_tab, validation, system = st.tabs(
    ["Overview", "Opportunities", "Live monitor", "Events", "Validation", "System"]
)

with overview:
    st.subheader("Today at a glance")
    st.markdown('<div class="section-note">Only evidence-backed rows published by the production workflows are displayed.</div>', unsafe_allow_html=True)
    themes, recommendations, watchlist, live = data["themes"], data["recommendations"], data["watchlist"], data["live"]
    a,b,c,d = st.columns(4)
    a.metric("Live recommendations", len(recommendations))
    b.metric("Research watchlist", len(watchlist))
    c.metric("Leading themes", len(themes))
    confirmed = int(live["monitor_state"].astype(str).eq("LIVE_CONFIRMED").sum()) if not live.empty and "monitor_state" in live else 0
    d.metric("Live confirmed", confirmed)
    if not recommendations.empty:
        for _, row in recommendations.head(3).iterrows():
            ticker = str(row.get("ticker","—")); stage = str(row.get("stage","WATCH"))
            decision = str(row.get("final_decision", row.get("decision","RESEARCH")))
            score = number(row.get("market_hunt_score")); rr = number(row.get("effective_rr"))
            tone = "good" if "BUY" in decision or stage == "CONFIRMED" else "warn"
            st.markdown(f'<div class="signal"><b>{ticker}</b> · <span class="{tone}">{stage}</span>'
                        f'<br><span class="muted">{decision} · score {score:.1f} · R/R {rr:.2f}×</span></div>', unsafe_allow_html=True)
    else:
        st.info("No stock currently passes every recommendation gate. Preliminary setups remain in the research watchlist.")
    if not themes.empty:
        st.subheader("Leading themes")
        st.dataframe(themes.head(10)[columns(themes,["theme_rank","theme","etf","theme_score","theme_state","ret5_pct","ret20_pct","rel5_vs_spy","rel20_vs_spy"])],
                     hide_index=True,use_container_width=True)

with opportunities:
    recommendations = data["recommendations"]
    st.subheader("Live-confirmed recommendations")
    st.markdown('<div class="section-note">Only stocks passing liquidity, volatility, catalyst, spread, runway, R/R and live VWAP/ORB/RVOL gates appear here.</div>', unsafe_allow_html=True)
    recommendation_cols=["ticker","company_name","live_price","stage","theme","market_hunt_score","catalyst_status",
                         "catalyst_score","intraday_rvol","bid_ask_spread_pct","entry_trigger","stop",
                         "effective_target","effective_rr","live_trade_action"]
    if recommendations.empty:
        st.info("No actionable recommendation currently passes every gate.")
    else:
        st.dataframe(recommendations[columns(recommendations,recommendation_cols)],hide_index=True,use_container_width=True)
        st.download_button("Download recommendations", recommendations.to_csv(index=False), "market_hunt_recommendations.csv", "text/csv", use_container_width=True)

    picks = data["watchlist"] if not data["watchlist"].empty else data["picks"]
    st.subheader("Research watchlist")
    st.markdown('<div class="section-note">FORMING, DISCOVER and waiting setups are research candidates—not trade recommendations.</div>', unsafe_allow_html=True)
    if picks.empty: st.info("No watchlist results are available yet.")
    else:
        query = st.text_input("Find ticker or company", placeholder="AXTI, IOVA…")
        view = picks.copy()
        if query:
            mask = view.astype(str).apply(lambda col: col.str.contains(query,case=False,na=False)).any(axis=1)
            view = view[mask]
        stages = sorted(view["stage"].dropna().astype(str).unique()) if "stage" in view else []
        selected = st.multiselect("Stage", stages, default=stages)
        if selected and "stage" in view: view = view[view["stage"].astype(str).isin(selected)]
        priority=["ticker","company_name","price","stage","theme","theme_state","market_hunt_score","final_decision",
                  "avg_share_volume20","median_dollar_volume20","atr_pct","adr20_pct",
                  "market_regime_state","catalyst_status","entry_trigger","entry_model","entry_condition","stop","stop_basis",
                  "risk_pct","effective_target","effective_rr","target_5","target_8","target_10",
                  "runway_to_next_resistance_pct","pattern"]
        st.dataframe(view[columns(view,priority)],hide_index=True,use_container_width=True)
        st.download_button("Download watchlist", view.to_csv(index=False), "market_hunt_watchlist.csv", "text/csv", use_container_width=True)
    with st.expander("All deep-scanned candidates"):
        candidates=data["candidates"]
        st.dataframe(candidates,hide_index=True,use_container_width=True) if not candidates.empty else st.write("Not available.")

with live_tab:
    live, transitions, journal = data["live"], data["transitions"], data["journal"]
    st.subheader("Intraday confirmation")
    st.markdown('<div class="section-note">VWAP, opening range and RVOL update research states; no broker orders are placed.</div>', unsafe_allow_html=True)
    if not live.empty:
        preferred=["ticker","monitor_state","previous_state","state_changed","premarket_price","premarket_gap_pct","premarket_volume",
                   "premarket_status","live_price","entry_trigger","stop","live_vwap","opening_range_high","intraday_rvol",
                   "live_confirmation_score","theme","catalyst_status","live_trade_action","checked_at_et"]
        st.dataframe(live[columns(live,preferred)],hide_index=True,use_container_width=True)
    else: st.info("Live monitor output is not available yet.")
    with st.expander("State transition history"):
        st.dataframe(transitions.tail(100).iloc[::-1],hide_index=True,use_container_width=True) if not transitions.empty else st.write("No transitions yet.")
    with st.expander("Paper-trading journal"):
        st.dataframe(journal.tail(200).iloc[::-1],hide_index=True,use_container_width=True) if not journal.empty else st.write("No journal rows yet.")

with event_tab:
    events, event_status = data["events"], data["event_status"]
    st.subheader("Upcoming catalysts")
    if not event_status.empty:
        row=event_status.iloc[0]
        st.caption(f'{row.get("provider","")} · {row.get("status","")} · {row.get("detail","")}')
    if events.empty: st.info("Event calendar output is not available yet.")
    else:
        preferred=["ticker","company_name","event_type","event_date_utc","earnings_report_time","days_to_event","event_priority",
                   "pre_event_decision","event_opportunity_score","pre_earnings_intel_score","estimate_revision_score",
                   "beat_rate_pct","median_surprise_pct","latest_surprise_pct","surprise_streak",
                   "prior_earnings_reaction_abs_avg_pct","options_implied_move_pct","pre_event_setup_state",
                   "entry_trigger","stop","effective_target","effective_rr"]
        st.dataframe(events[columns(events,preferred)].head(100),hide_index=True,use_container_width=True)

with validation:
    performance, setup, calibration, gate = data["performance"], data["performance_setup"], data["calibration"], data["gate"]
    st.subheader("Forward validation")
    if not gate.empty:
        g=gate.iloc[0]
        st.info(f'Readiness gate: {g.get("gate_status","PAPER_VALIDATION")} · closed signals {int(number(g.get("closed_signals")))}/{int(number(g.get("min_closed_signals"),30))}')
        q1,q2,q3,q4=st.columns(4)
        q1.metric("Paper samples",int(number(g.get("closed_signals"))))
        q2.metric("Win rate",f'{number(g.get("win_rate_pct")):.1f}%')
        q3.metric("Average R",f'{number(g.get("avg_r_multiple")):.2f}')
        q4.metric("Profit factor",f'{number(g.get("profit_factor_r")):.2f}')
        if not bool(g.get("ready_for_real_money",False)):
            st.warning(f'Live-capital gate remains locked. Missing: {g.get("failed_checks","forward evidence")}')
    if not performance.empty:
        p=performance.iloc[0]; c1,c2,c3,c4,c5=st.columns(5)
        c1.metric("Signals",int(number(p.get("signals")))); c2.metric("Closed",int(number(p.get("closed_signals"))))
        c3.metric("Win rate",f'{number(p.get("win_rate_pct")):.1f}%'); c4.metric("Average R",f'{number(p.get("avg_r_multiple")):.2f}')
        c5.metric("Target hit",f'{number(p.get("target_hit_rate_pct")):.1f}%')
    else: st.info("Forward-validation samples are still being collected.")
    if not setup.empty:
        st.subheader("Performance by setup")
        st.dataframe(setup,hide_index=True,use_container_width=True)
    st.subheader("Probability calibration")
    if calibration.empty: st.info("Calibration output is not available yet.")
    else:
        usable=calibration[calibration["calibration_status"].eq("USABLE")] if "calibration_status" in calibration else pd.DataFrame()
        if usable.empty: st.warning("Probabilities remain provisional until score buckets have enough closed trades.")
        st.dataframe(calibration,hide_index=True,use_container_width=True)

with system:
    st.subheader("Pipeline and data health")
    st.write("5k+ universe → liquidity gate → themes → technical structure → D/W/M levels → runway and R/R → catalysts → live VWAP/ORB/RVOL → research state")
    status_rows=pd.DataFrame([{"dataset":key,"rows":len(data[key]),"source":sources[key]} for key in files])
    st.dataframe(status_rows,hide_index=True,use_container_width=True)
    tradable=data["tradable"]
    if not tradable.empty:
        passed=int(tradable["tradable"].sum()) if "tradable" in tradable else len(tradable)
        st.metric("Current tradability gate",f"{passed:,} symbols")
    st.warning("Research and decision support only. Data may be delayed or incomplete. Stops can gap and no setup guarantees a 5–10% move.")
