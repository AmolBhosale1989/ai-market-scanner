import os
import json
from io import StringIO
from pathlib import Path

import pandas as pd
import streamlit as st

from scanner.dashboard_data import fetch_remote_bundle
from scanner.legendary_agents import run_legendary_agents

st.set_page_config(page_title="Market Hunt V3", page_icon="⚡", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
:root{
  --bg:#050912;--bg2:#0a1020;--panel:rgba(15,24,42,.82);--panel2:rgba(20,32,55,.92);
  --line:rgba(120,155,210,.18);--line2:rgba(116,229,187,.28);--text:#f7fbff;--muted:#8fa4bd;
  --green:#72efc1;--green2:#34d399;--amber:#ffd166;--blue:#78b9ff;--violet:#a78bfa;--red:#ff7b8a;
}
html,body,[class*="css"]{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.stApp{
  background:
    radial-gradient(circle at 8% -10%,rgba(60,130,246,.22),transparent 28%),
    radial-gradient(circle at 95% 0%,rgba(114,239,193,.10),transparent 24%),
    linear-gradient(180deg,var(--bg2) 0%,var(--bg) 32%,#04070d 100%);
  color:var(--text);
}
.block-container{max-width:1460px;padding-top:1rem;padding-bottom:5rem}
header[data-testid="stHeader"]{background:transparent}
.hero{
  position:relative;overflow:hidden;padding:30px 32px;border:1px solid var(--line);border-radius:28px;
  background:linear-gradient(135deg,rgba(21,42,72,.96),rgba(8,15,28,.95) 58%,rgba(10,27,31,.95));
  box-shadow:0 30px 80px rgba(0,0,0,.42);margin-bottom:16px;
}
.hero:after{
  content:"";position:absolute;width:340px;height:340px;border-radius:50%;right:-110px;top:-170px;
  background:radial-gradient(circle,rgba(114,239,193,.20),transparent 65%);
}
.hero-grid{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;position:relative;z-index:1}
.eyebrow{font-size:.72rem;letter-spacing:.18em;color:var(--green);font-weight:850;text-transform:uppercase}
.hero h1{font-size:clamp(2.4rem,5vw,4.4rem);line-height:.95;margin:.55rem 0 .7rem;letter-spacing:-.045em}
.hero p{color:#a9bad0;max-width:760px;margin:0;font-size:1rem;line-height:1.55}
.hero-mark{font-size:4.2rem;opacity:.9;filter:drop-shadow(0 12px 35px rgba(114,239,193,.25))}
.status-row{display:flex;flex-wrap:wrap;gap:8px;margin:.6rem 0 .25rem}
.status{
  display:inline-flex;gap:8px;align-items:center;padding:7px 12px;border:1px solid var(--line);
  border-radius:999px;background:rgba(12,27,44,.72);color:#c7d7eb;font-size:.78rem;backdrop-filter:blur(14px)
}
.status strong{color:var(--text)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green)}
.pill{display:inline-flex;padding:5px 9px;border-radius:999px;font-size:.72rem;font-weight:800;letter-spacing:.02em;border:1px solid var(--line)}
.pill.good{background:rgba(52,211,153,.10);color:var(--green);border-color:rgba(52,211,153,.26)}
.pill.warn{background:rgba(255,209,102,.09);color:var(--amber);border-color:rgba(255,209,102,.25)}
.pill.bad{background:rgba(255,123,138,.09);color:var(--red);border-color:rgba(255,123,138,.25)}
.section-note{color:var(--muted);font-size:.9rem;margin-top:-.5rem;margin-bottom:1rem}
[data-testid="stMetric"]{
  background:linear-gradient(145deg,rgba(21,34,57,.92),rgba(10,18,31,.92));border:1px solid var(--line);
  border-radius:20px;padding:16px 17px;box-shadow:0 14px 34px rgba(0,0,0,.28);backdrop-filter:blur(14px)
}
[data-testid="stMetric"]:hover{transform:translateY(-1px);border-color:rgba(120,185,255,.32)}
[data-testid="stMetricLabel"]{color:#8fa4bd;font-weight:650}
[data-testid="stMetricValue"]{color:var(--text);font-weight:780;letter-spacing:-.02em}
.signal{
  border:1px solid var(--line);border-radius:20px;padding:17px 18px;
  background:linear-gradient(145deg,rgba(18,33,56,.95),rgba(8,15,28,.95));margin:.55rem 0;
  box-shadow:0 10px 24px rgba(0,0,0,.22)
}
.signal b{font-size:1.15rem}.good{color:var(--green)}.warn{color:var(--amber)}.muted{color:var(--muted)}
.agent-card{
  border:1px solid var(--line);border-radius:20px;padding:16px 18px;margin:.2rem 0 .7rem;
  background:linear-gradient(145deg,rgba(18,33,56,.82),rgba(8,15,28,.9));box-shadow:0 12px 28px rgba(0,0,0,.22)
}
.agent-card .name{font-size:1rem;font-weight:800;color:var(--text)} .agent-card .setup{font-size:.82rem;color:var(--muted);margin-top:2px}
.agent-card .count{font-size:1.55rem;font-weight:850;color:var(--green);letter-spacing:-.03em}
.stTabs [data-baseweb="tab-list"]{
  gap:8px;overflow-x:auto;background:rgba(7,13,24,.72);padding:6px;border:1px solid var(--line);border-radius:16px;
  position:sticky;top:.45rem;z-index:50;backdrop-filter:blur(18px)
}
.stTabs [data-baseweb="tab"]{
  background:transparent;border:1px solid transparent;border-radius:12px;padding:9px 15px;color:#9fb2c8;font-weight:700
}
.stTabs [aria-selected="true"]{
  background:linear-gradient(135deg,rgba(52,94,145,.72),rgba(25,68,81,.72))!important;
  border-color:rgba(120,185,255,.28)!important;color:#fff!important
}
div[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:18px;overflow:hidden;box-shadow:0 12px 30px rgba(0,0,0,.18)}
div[data-testid="stExpander"]{border:1px solid var(--line)!important;border-radius:16px!important;background:rgba(9,16,28,.50)}
.stButton>button,.stDownloadButton>button{
  border-radius:12px;border:1px solid var(--line);background:linear-gradient(135deg,#173a5f,#153a43);color:#fff;font-weight:750
}
.stTextInput input,.stMultiSelect>div>div{border-radius:12px!important}
@media(max-width:740px){
  .block-container{padding: .75rem .62rem 4rem}.hero{padding:22px 20px;border-radius:22px}
  .hero-grid{align-items:flex-start}.hero-mark{font-size:2.6rem}.hero h1{font-size:2.7rem}
  .hero p{font-size:.9rem}.stTabs [data-baseweb="tab"]{padding:8px 11px;font-size:.82rem}
  [data-testid="stMetric"]{padding:12px}
}
</style>""", unsafe_allow_html=True)

REMOTE_BASE = os.getenv("SCAN_DATA_BASE_URL", "https://raw.githubusercontent.com/AmolBhosale1989/ai-market-scanner/scan-data/dashboard-data").rstrip("/")
LOCAL_DIR = Path("outputs")
remote_payloads = {}
dashboard_fetch_health = {}

def remote_csv(name):
    payload = remote_payloads.get(name)
    if payload is None:
        raise FileNotFoundError(name)
    return pd.read_csv(StringIO(payload))

def load_csv(name):
    try:
        return remote_csv(name), "published"
    except Exception:
        path = LOCAL_DIR / name
        if path.exists():
            try: return pd.read_csv(path), "local"
            except Exception: pass
    return pd.DataFrame(), "unavailable"

def remote_json(name):
    payload = remote_payloads.get(name)
    if payload is None:
        raise FileNotFoundError(name)
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not a JSON object")
    return value

def load_json(name):
    try:
        return remote_json(name), "published"
    except Exception:
        path = LOCAL_DIR / name
        if path.exists():
            try: return json.loads(path.read_text()), "local"
            except Exception: pass
    return {}, "unavailable"

def number(value, default=0):
    try: return float(value)
    except (TypeError, ValueError): return default

def columns(frame, preferred):
    return [name for name in preferred if name in frame.columns]

def add_opportunity_context(frame):
    """Add readable RSI/volume states for opportunity tables without changing scan logic."""
    if frame is None or frame.empty:
        return frame
    out = frame.copy()

    if "rsi14" in out.columns:
        rsi = pd.to_numeric(out["rsi14"], errors="coerce")
        out["rsi_state"] = pd.Series("NEUTRAL / RESET", index=out.index)
        out.loc[rsi.between(55, 72, inclusive="both"), "rsi_state"] = "MOMENTUM SWEET SPOT"
        out.loc[rsi.gt(72) & rsi.le(80), "rsi_state"] = "STRONG / EXTENDED WATCH"
        out.loc[rsi.lt(45), "rsi_state"] = "WEAK MOMENTUM"

    if "rvol" in out.columns:
        vol = pd.to_numeric(out["rvol"], errors="coerce")
        out["volume_vs_20ma"] = vol.round(2)
        out["swing_volume_state"] = pd.Series("NORMAL VOLUME", index=out.index)
        out.loc[vol.lt(0.90), "swing_volume_state"] = "LOW-VOLUME PULLBACK"
        out.loc[vol.ge(1.30), "swing_volume_state"] = "VOLUME EXPANSION"
        out.loc[vol.ge(2.00), "swing_volume_state"] = "STRONG VOLUME EXPANSION"

    return out

files = {
    "scan_meta":"scan_metadata.csv", "live_meta":"live_metadata.csv", "health":"scan_health.csv",
    "live":"intraday_live.csv", "transitions":"state_transitions.csv", "themes":"trending_themes.csv",
    "recommendations":"recommended_trades.csv", "leaders":"liquid_leaders.csv", "watchlist":"watchlist.csv",
    "picks":"latest_scan.csv", "tradable":"tradable_universe.csv", "candidates":"all_candidates.csv",
    "events":"upcoming_events.csv", "event_status":"event_status.csv", "journal":"paper_journal.csv",
    "performance":"performance_summary.csv", "performance_setup":"performance_by_setup.csv",
    "calibration":"probability_calibration.csv", "monitor":"monitor_health.csv", "gate":"validation_gate.csv",
    "legendary":"legendary_setups.csv", "legendary_consensus":"legendary_consensus.csv",
    "trader_minervini":"trader_minervini.csv", "trader_oneil":"trader_oneil.csv",
    "trader_weinstein":"trader_weinstein.csv", "trader_darvas":"trader_darvas.csv",
    "trader_livermore":"trader_livermore.csv", "trader_qullamaggie":"trader_qullamaggie.csv",
    "trader_druckenmiller":"trader_druckenmiller.csv", "trader_lawwaisum":"trader_lawwaisum.csv",
    "trader_martinluk":"trader_martinluk.csv",
    "trader_top500swing":"trader_top500swing.csv",
    "trader_highmomentumbeta":"trader_highmomentumbeta.csv",
    "trader_superstock":"trader_superstock.csv",
    "trader_brownmoose":"trader_brownmoose.csv",
    "trader_venu":"trader_venu.csv",
    "v45_ranked":"v4_5_ranked_candidates.csv",
    "v45_validation":"v4_5_validation.csv",
    "v4_shadow_summary":"v4_shadow_strategy_summary.csv",
    "v4_shadow_daily":"v4_shadow_daily_comparison.csv",
    "v4_shadow_breakdowns":"v4_shadow_breakdowns.csv",
    "v4_shadow_observations":"v4_shadow_observations.csv",
    "v4_model_monitor":"v4_model_monitor.csv",
    "v5_ranked":"v5_ranked_candidates.csv",
    "v5_validation":"v5_validation.csv",
    "v6_ranked":"v6_ranked_candidates.csv",
    "v6_validation":"v6_validation.csv",
    "v7_portfolio":"v7_paper_portfolio.csv",
    "v72_validation":"v7_2_criteria_validation.csv",
    "v72_grid":"v7_2_criteria_grid.csv",
    "v73_comparison":"v7_3_challenger_comparison.csv",
    "social_queue":"social_content_queue.csv",
    "social_calendar":"social_content_calendar.csv",
    "v81_operational":"v8_1_operational_health.csv",
    "v9_readiness":"v9_readiness.csv",
    "v4_options_microstructure":"v4_options_microstructure.csv",
}
json_files = [
    "v4_6_cutover_evaluation.json", "v4_5_model.json", "v5_model.json",
    "v6_model.json", "v7_allocation_health.json", "v7_1_evidence_health.json",
    "v7_2_criteria_proposal.json", "v7_3_challenger_health.json",
    "social_engine_health.json", "v8_1_operational_health.json", "v9_readiness.json",
]

@st.cache_data(ttl=60, show_spinner=False)
def remote_dashboard_bundle(remote_base, filenames):
    return fetch_remote_bundle(remote_base, filenames)

remote_payloads, dashboard_fetch_health = remote_dashboard_bundle(
    REMOTE_BASE, tuple([*files.values(), *json_files])
)
data, sources = {}, {}
for key, filename in files.items():
    data[key], sources[key] = load_csv(filename)
v46_cutover, v46_source = load_json("v4_6_cutover_evaluation.json")
v45_model, v45_model_source = load_json("v4_5_model.json")
v5_model, v5_model_source = load_json("v5_model.json")
v6_model, v6_model_source = load_json("v6_model.json")
v7_health, v7_health_source = load_json("v7_allocation_health.json")
evidence_health, evidence_health_source = load_json("v7_1_evidence_health.json")
v72_proposal, v72_proposal_source = load_json("v7_2_criteria_proposal.json")
v73_health, v73_health_source = load_json("v7_3_challenger_health.json")
social_health, social_health_source = load_json("social_engine_health.json")
v81_health, v81_health_source = load_json("v8_1_operational_health.json")
v9_readiness, v9_readiness_source = load_json("v9_readiness.json")

# Resilience fallback: if the full-scan publisher is delayed or GitHub Actions
# is queued, derive legendary-agent lists from the latest published deep-scan
# candidate table directly on Render. Remote scan-data remains the preferred
# source and automatically replaces these local fallback files when available.
if data["legendary"].empty and not data["candidates"].empty:
    try:
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        run_legendary_agents(data["candidates"], LOCAL_DIR, top_n=25)
        legendary_keys = [
            "legendary", "legendary_consensus",
            "trader_minervini", "trader_oneil", "trader_weinstein",
            "trader_darvas", "trader_livermore", "trader_qullamaggie",
            "trader_druckenmiller", "trader_lawwaisum", "trader_martinluk",
            "trader_top500swing", "trader_highmomentumbeta", "trader_superstock", "trader_brownmoose", "trader_venu",
        ]
        for key in legendary_keys:
            data[key], sources[key] = load_csv(files[key])
    except Exception as exc:
        st.warning(f"Legendary-agent fallback could not run: {exc}")

st.markdown("""<div class="hero"><div class="hero-grid"><div>
<div class="eyebrow">AI MARKET INTELLIGENCE · V3 LIVE · V4–V6 SHADOW · V7 PAPER · V7.2–V7.3 CRITERIA RESEARCH</div>
<h1>Market Hunt</h1>
<p>Discover liquid U.S. swing opportunities, momentum leaders, catalyst-driven setups and multi-agent consensus from one research command center.</p>
</div><div class="hero-mark">⚡</div></div></div>""", unsafe_allow_html=True)

scan_meta, live_meta, health, monitor = data["scan_meta"], data["live_meta"], data["health"], data["monitor"]
scan_stamp = str(scan_meta.iloc[0].get("generated_at_utc", "Waiting for first scan")) if not scan_meta.empty else "Waiting for first scan"
live_stamp = str(live_meta.iloc[0].get("updated_at_utc", "Waiting for monitor")) if not live_meta.empty else "Waiting for monitor"
source_state = "CONNECTED" if sources["picks"] != "unavailable" else "WAITING"
st.markdown(
    f'<div class="status-row">'
    f'<span class="status"><span class="dot"></span><strong>{source_state}</strong></span>'
    f'<span class="status">Data source: <strong>{sources["picks"]}</strong></span>'
    f'<span class="status">Auto refresh: <strong>60s</strong></span>'
    f'</div>', unsafe_allow_html=True
)
st.caption(f"Base scan · {scan_stamp} UTC   |   Live monitor · {live_stamp} UTC")

h = health.iloc[0] if not health.empty else {}
mh = monitor.iloc[0] if not monitor.empty else {}
m1,m2,m3,m4,m5 = st.columns(5)
m1.metric("Scan health", str(h.get("status", "WAITING")))
m2.metric("Master universe", f'{int(number(h.get("master_universe_symbols"))):,}')
m3.metric("Tradable", f'{int(number(h.get("tradable_symbols"))):,}')
m4.metric("Analyzable", f'{number(h.get("analyzable_coverage"))*100:.1f}%')
m5.metric("Live alerts", f'{int(number(mh.get("alerts_generated"))):,}')

overview, opportunities, legendary_tab, live_tab, event_tab, v4_tab, social_tab, validation, system = st.tabs(
    ["Overview", "Opportunities", "Legendary setups", "Live monitor", "Events", "V4 Intelligence", "Social Studio", "Validation", "System"]
)

with overview:
    st.subheader("Today at a glance")
    st.markdown('<div class="section-note">Only evidence-backed rows published by the production workflows are displayed.</div>', unsafe_allow_html=True)
    themes, recommendations, leaders, watchlist, live = data["themes"], data["recommendations"], data["leaders"], data["watchlist"], data["live"]
    a,b,c,d,e = st.columns(5)
    a.metric("Live recommendations", len(recommendations))
    b.metric("Tracked leaders", len(leaders))
    c.metric("Research watchlist", len(watchlist))
    d.metric("Leading themes", len(themes))
    confirmed = int(live["monitor_state"].astype(str).eq("LIVE_CONFIRMED").sum()) if not live.empty and "monitor_state" in live else 0
    e.metric("Live confirmed", confirmed)
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
    candidates = add_opportunity_context(data["candidates"])
    st.subheader("Broad ranked opportunities")
    st.markdown('<div class="section-note">This is generated from the full deep-scanned U.S. candidate universe—not a preset ticker list. It includes liquid FORMING, DISCOVER, ARMED and CONFIRMED setups ranked by Market Hunt score, and requires at least one +10% close-to-close day in the last 30 trading sessions.</div>', unsafe_allow_html=True)

    if candidates.empty:
        st.info("Broad candidate scan is not available yet.")
    else:
        broad = candidates.copy()
        if "liquidity_gate_passed" in broad.columns:
            broad = broad[broad["liquidity_gate_passed"].astype(str).str.lower().isin(["true","1","yes"])]
        if "volatility_gate_passed" in broad.columns:
            broad = broad[broad["volatility_gate_passed"].astype(str).str.lower().isin(["true","1","yes"])]
        if "stage" in broad.columns:
            broad = broad[broad["stage"].astype(str).isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])]
        if "explosive_move_30d" in broad.columns:
            explosive = broad["explosive_move_30d"].astype(str).str.lower().isin(["true","1","yes"])
            broad = broad[explosive]
        elif "max_up_day_30d_pct" in broad.columns:
            broad = broad[pd.to_numeric(broad["max_up_day_30d_pct"], errors="coerce").ge(10.0)]
        if "market_hunt_score" in broad.columns:
            broad["market_hunt_score"] = pd.to_numeric(broad["market_hunt_score"], errors="coerce")
            broad = broad.sort_values("market_hunt_score", ascending=False)

        q1, q2, q3 = st.columns([2,1,1])
        with q1:
            broad_query = st.text_input("Search broad opportunities", placeholder="Ticker or company…", key="broad_opportunity_search")
        with q2:
            broad_stages = sorted(broad["stage"].dropna().astype(str).unique()) if "stage" in broad else []
            broad_selected = st.multiselect("Setup stage", broad_stages, default=broad_stages, key="broad_stage_filter")
        with q3:
            broad_limit = st.selectbox("Show top", [25,50,100,200], index=2, key="broad_limit")

        broad_view = broad.copy()
        if broad_query:
            mask = broad_view.astype(str).apply(lambda col: col.str.contains(broad_query,case=False,na=False)).any(axis=1)
            broad_view = broad_view[mask]
        if broad_selected and "stage" in broad_view:
            broad_view = broad_view[broad_view["stage"].astype(str).isin(broad_selected)]

        broad_cols = ["ticker","company_name","price","stage","market_hunt_score","technical_score","formation_score",
                      "rs20_vs_spy","rsi14","rsi_state","volume_vs_20ma","swing_volume_state",
                      "max_up_day_30d_pct","ten_pct_up_days_30d","explosive_move_30d","atr_pct","adr20_pct",
                      "avg_dollar_volume","catalyst_status","catalyst_score","entry_trigger","entry_model","stop",
                      "effective_target","effective_rr","runway_to_next_resistance_pct","pattern"]
        st.caption(f"Showing {min(len(broad_view), int(broad_limit))} of {len(broad_view):,} matching broad-market candidates")
        st.dataframe(broad_view.head(int(broad_limit))[columns(broad_view,broad_cols)], hide_index=True, use_container_width=True)
        st.download_button("Download broad opportunities", broad_view.to_csv(index=False),
                           "market_hunt_broad_opportunities.csv", "text/csv", use_container_width=True,
                           key="download_broad_opportunities")

    superstocks = data["trader_superstock"]
    st.subheader("Superstock candidates")
    st.markdown('<div class="section-note">Explosive liquid leaders with at least one +10% day in the last 30 sessions, ranked for outsized-move potential. A+ is a research priority, not an automatic trade.</div>', unsafe_allow_html=True)
    if superstocks.empty:
        st.info("No current Superstock candidates yet. The next successful full scan will populate this list.")
    else:
        super_cols=["ticker","company_name","price","superstock_grade","superstock_action","legendary_score","stage","theme",
                    "market_hunt_score","rs20_vs_spy","rsi14","rsi_state","max_up_day_30d_pct","ten_pct_up_days_30d",
                    "atr_pct","adr20_pct","effective_rr","runway_to_next_resistance_pct","catalyst_status","catalyst_score",
                    "entry_trigger","stop","effective_target","pattern"]
        st.dataframe(superstocks[columns(superstocks,super_cols)].head(25), hide_index=True, use_container_width=True)

    brown = data["trader_brownmoose"]
    st.subheader("Brownmoose-style staged setups")
    st.markdown('<div class="section-note">Research approximation from observed subscriber examples: B1/B2 entries are mapped from EMA/support confluence, while T1/T2/T3 come from overhead resistance. No automatic orders are placed.</div>', unsafe_allow_html=True)
    if brown.empty:
        st.info("No current Brownmoose-style setup passes the confluence and R/R filters.")
    else:
        brown_cols=["ticker","company_name","price","brownmoose_grade","brownmoose_state","brownmoose_confluence_score",
                    "brown_b1","brown_b1_source","brown_b2","brown_b2_source","brown_invalidation",
                    "brown_t1","brown_t2","brown_t3","brown_rr_to_t1","stage","theme","market_hunt_score",
                    "rs20_vs_spy","rsi14","atr_pct","adr20_pct","catalyst_status","pattern"]
        st.dataframe(brown[columns(brown,brown_cols)].head(25), hide_index=True, use_container_width=True)

    venu = data["trader_venu"]
    st.subheader("Venu-style leaders")
    st.markdown('<div class="section-note">Research approximation with two modes: longer-horizon Position Leaders and tactical Rotation Swings. Theme leadership, catalyst/fundamental proxy, relative strength, trend alignment and pullback/breakout quality drive ranking.</div>', unsafe_allow_html=True)
    if venu.empty:
        st.info("No current Venu-style leader or rotation setup passes the filters.")
    else:
        venu_cols=["ticker","company_name","price","venu_grade","venu_mode","venu_trend_stack","venu_theme_leadership",
                   "venu_fundamental_proxy_score","legendary_score","stage","theme","market_hunt_score","rs20_vs_spy",
                   "rsi14","atr_pct","adr20_pct","effective_rr","runway_to_next_resistance_pct","catalyst_status",
                   "entry_trigger","entry_model","stop","effective_target","pattern"]
        st.dataframe(venu[columns(venu,venu_cols)].head(25), hide_index=True, use_container_width=True)

    leaders = add_opportunity_context(data["leaders"])
    st.subheader("Liquid market leaders")
    st.markdown('<div class="section-note">Widely followed stocks remain visible even without a trade setup. Gate failures are shown explicitly and never promoted to BUY.</div>', unsafe_allow_html=True)
    leader_cols=["ticker","company_name","gate_price","price","leader_status","stage","market_hunt_score",
                 "rsi14","rsi_state","volume_vs_20ma","swing_volume_state",
                 "avg_share_volume20","median_dollar_volume20","adr20_pct","atr_pct",
                 "catalyst_status","effective_rr","runway_to_next_resistance_pct","pattern"]
    if leaders.empty:
        st.info("Leader tracker will appear after the next full-universe scan.")
    else:
        st.dataframe(leaders[columns(leaders,leader_cols)],hide_index=True,use_container_width=True)

    recommendations = add_opportunity_context(data["recommendations"])
    st.subheader("Live-confirmed recommendations")
    st.markdown('<div class="section-note">Only stocks passing liquidity, volatility, catalyst, spread, runway, R/R and live VWAP/ORB/RVOL gates appear here.</div>', unsafe_allow_html=True)
    recommendation_cols=["ticker","company_name","live_price","stage","theme","market_hunt_score",
                         "rsi14","rsi_state","volume_vs_20ma","swing_volume_state",
                         "live_above_vwap","volume_vs_9ma","opening_30m_rvol","opening_volume_spike_2x",
                         "catalyst_status","catalyst_score","intraday_rvol","bid_ask_spread_pct","entry_trigger","stop",
                         "effective_target","effective_rr","live_trade_action"]
    if recommendations.empty:
        st.info("No actionable recommendation currently passes every gate.")
    else:
        st.dataframe(recommendations[columns(recommendations,recommendation_cols)],hide_index=True,use_container_width=True)
        st.download_button("Download recommendations", recommendations.to_csv(index=False), "market_hunt_recommendations.csv", "text/csv", use_container_width=True)

    picks = data["watchlist"] if not data["watchlist"].empty else data["picks"]
    picks = add_opportunity_context(picks)
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
                  "rsi14","rsi_state","volume_vs_20ma","swing_volume_state",
                  "avg_share_volume20","median_dollar_volume20","atr_pct","adr20_pct",
                  "market_regime_state","catalyst_status","entry_trigger","entry_model","entry_condition","stop","stop_basis",
                  "risk_pct","effective_target","effective_rr","target_5","target_8","target_10",
                  "runway_to_next_resistance_pct","pattern"]
        st.dataframe(view[columns(view,priority)],hide_index=True,use_container_width=True)
        st.download_button("Download watchlist", view.to_csv(index=False), "market_hunt_watchlist.csv", "text/csv", use_container_width=True)
    with st.expander("All deep-scanned candidates"):
        candidates=data["candidates"]
        st.dataframe(candidates,hide_index=True,use_container_width=True) if not candidates.empty else st.write("Not available.")


with legendary_tab:
    st.subheader("Legendary trader setup agents")
    st.markdown('<div class="section-note">Independent screening agents translate publicly described trading principles into objective research filters. They are approximations for scanning/backtesting, not exact reproductions of any trader\'s discretionary process.</div>', unsafe_allow_html=True)

    consensus = data["legendary_consensus"]
    if not consensus.empty:
        st.subheader("Multi-agent consensus")
        st.markdown('<div class="section-note">Stocks detected by multiple trader agents are ranked first. Agreement is a research signal, not a trade recommendation.</div>', unsafe_allow_html=True)
        st.dataframe(consensus.head(50), hide_index=True, use_container_width=True)
    else:
        st.info("Legendary-agent consensus will appear after the next completed full scan.")

    trader_views = [
        ("Mark Minervini", "Trend Template / VCP", "trader_minervini"),
        ("William O'Neil", "CAN SLIM-style Breakout", "trader_oneil"),
        ("Stan Weinstein", "Stage 2 Breakout", "trader_weinstein"),
        ("Nicolas Darvas", "Darvas Box Breakout", "trader_darvas"),
        ("Jesse Livermore", "Pivot / Line of Least Resistance", "trader_livermore"),
        ("Kristjan Kullamägi", "Momentum Breakout / EP-style", "trader_qullamaggie"),
        ("Stanley Druckenmiller", "Liquidity + Catalyst + Technical Confirmation", "trader_druckenmiller"),
        ("Law Wai-Sum", "Growth Momentum + Position/Swing Hybrid", "trader_lawwaisum"),
        ("Martin Luk", "Asymmetric 5:1 Momentum Swing", "trader_martinluk"),
        ("Top-500 Swing Agent", "2–7 Day Liquid Swing Ideas", "trader_top500swing"),
        ("High Momentum / High Beta Agent", "Fast-Mover Momentum + Beta/Volatility", "trader_highmomentumbeta"),
        ("Superstock Agent", "Explosive Leader / Early Supertrend Candidate", "trader_superstock"),
        ("Brownmoose Agent", "B1/B2 Confluence + Retest + Multi-Target Plan", "trader_brownmoose"),
        ("Venu Agent", "Position Leader + Rotation Swing", "trader_venu"),
    ]

    preview_cols = st.columns(3)
    for idx, (trader_name, setup_name, key) in enumerate(trader_views[-3:]):
        frame = data[key]
        with preview_cols[idx]:
            st.markdown(
                f'<div class="agent-card"><div class="name">{trader_name}</div>'
                f'<div class="setup">{setup_name}</div>'
                f'<div class="count">{len(frame)}</div>'
                f'<div class="setup">current matches</div></div>',
                unsafe_allow_html=True,
            )

    for trader_name, setup_name, key in trader_views:
        frame = data[key]
        with st.expander(f"{trader_name} · {setup_name}", expanded=False):
            if frame.empty:
                st.write("No current matches.")
            else:
                preferred = ["ticker","company_name","price","venu_grade","venu_mode","venu_trend_stack","venu_theme_leadership","venu_fundamental_proxy_score","brownmoose_grade","brownmoose_state","brownmoose_confluence_score","brown_b1","brown_b1_source","brown_b2","brown_b2_source","brown_invalidation","brown_t1","brown_t2","brown_t3","brown_rr_to_t1","superstock_grade","superstock_action","momentum_grade","setup_match","legendary_score","stage","theme",
                             "market_hunt_score","rs20_vs_spy","rsi14","rsi_state","atr_pct","adr20_pct","entry_trigger","entry_model",
                             "stop","effective_target","effective_rr","runway_to_next_resistance_pct",
                             "catalyst_status","intraday_rvol","setup_reason"]
                st.dataframe(frame[columns(frame, preferred)], hide_index=True, use_container_width=True)
                st.download_button(
                    f"Download {trader_name} list",
                    frame.to_csv(index=False),
                    f"{key}.csv",
                    "text/csv",
                    key=f"download_{key}",
                    use_container_width=True,
                )

with live_tab:
    live, transitions, journal = data["live"], data["transitions"], data["journal"]
    st.subheader("Intraday confirmation")
    st.markdown('<div class="section-note">VWAP, opening range and RVOL update research states; no broker orders are placed.</div>', unsafe_allow_html=True)
    if not live.empty:
        preferred=["ticker","monitor_state","previous_state","state_changed","premarket_price","premarket_gap_pct","premarket_volume",
                   "premarket_status","live_price","entry_trigger","stop","live_vwap","live_above_vwap","opening_range_high","intraday_rvol",
                   "volume_vs_9ma","opening_30m_rvol","opening_volume_spike_2x","live_confirmation_score","theme","catalyst_status","live_trade_action","checked_at_et"]
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

with v4_tab:
    st.subheader("V4–V7.3 research intelligence")
    st.markdown('<div class="section-note">V3 remains primary. These rankings and probabilities are evidence-only until every production gate passes.</div>', unsafe_allow_html=True)
    monitor_frame = data["v4_model_monitor"]
    monitor_row = monitor_frame.iloc[0] if not monitor_frame.empty else {}
    cutover_status = str(v46_cutover.get("status", "COLLECTING"))
    model_status = str(v45_model.get("promotion_status", "COLLECTING"))
    monitor_status = str(monitor_row.get("status", "COLLECTING"))
    v5_status = str(v5_model.get("promotion_status", "COLLECTING"))
    v6_status = str(v6_model.get("promotion_status", "COLLECTING"))
    v7_status = str(v7_health.get("status", "COLLECTING"))
    k1,k2,k3,k4 = st.columns(4)
    k1.metric("Primary ranking", "V3")
    k2.metric("V4.5 model", model_status)
    k3.metric("Model health", monitor_status)
    k4.metric("Cutover", cutover_status)
    v72_status = str(v72_proposal.get("status", "COLLECTING"))
    v73_status = str(v73_health.get("status", "WAITING_FOR_PROPOSAL"))
    k5,k6,k7,k8,k9 = st.columns(5)
    k5.metric("V5 adaptive", v5_status)
    k6.metric("V6 uncertainty", v6_status)
    k7.metric("V7 paper plan", v7_status)
    k8.metric("V7.2 criteria", v72_status)
    k9.metric("V7.3 challenger", v73_status)
    evidence_status = str(evidence_health.get("status", "COLLECTING"))
    mature_evidence = int(number(evidence_health.get("mature_training_samples")))
    e1,e2,e3,e4 = st.columns(4)
    e1.metric("Evidence pipeline", evidence_status)
    e2.metric("Mature samples", mature_evidence)
    e3.metric("V5 progress", f"{min(mature_evidence, 100)}/100")
    e4.metric("V6 progress", f"{min(mature_evidence, 220)}/220")
    if evidence_health.get("failed_checks"):
        st.error("Evidence collection alert: " + ", ".join(map(str, evidence_health["failed_checks"])))
    failed = v46_cutover.get("failed_gates", [])
    if v46_cutover.get("eligible"):
        st.success("V4.5 has passed the evidence gates and is eligible for a manual, version-pinned cutover.")
    else:
        st.warning("V4.5 remains safely in shadow mode. Open gates: " + (", ".join(map(str, failed)) if failed else "evidence still collecting"))

    ranked = data["v45_ranked"]
    st.subheader("Current calibrated candidates")
    if ranked.empty:
        st.info("V4.5 ranked candidates are waiting for the first outcome workflow.")
    else:
        ranked = ranked.copy()
        ranked["v4_rank"] = range(1, len(ranked) + 1)
        if "market_hunt_score" in ranked:
            ranked["v3_rank"] = pd.to_numeric(ranked["market_hunt_score"], errors="coerce").rank(method="min", ascending=False)
        ranked_cols = ["ticker","company_name","price","stage","v45_calibrated_score","v45_p5_probability","v45_p10_probability","v45_p15_probability","market_hunt_score","technical_score","catalyst_score","theme","market_regime_state","intraday_rvol","entry_trigger","stop","effective_target","effective_rr","v45_model_version"]
        ranked_cols = ["ticker","v3_rank","v4_rank"] + [name for name in ranked_cols if name != "ticker"]
        st.dataframe(ranked[columns(ranked, ranked_cols)].head(30), hide_index=True, use_container_width=True)

    with st.expander("Options flow and microstructure evidence"):
        evidence = data["v4_options_microstructure"]
        if evidence.empty:
            st.write("Bounded V4.4 evidence is waiting for the next intraday cycle.")
        else:
            evidence_cols = ["ticker","options_status","call_volume","put_volume","call_put_volume_ratio","call_open_interest","put_open_interest","unusual_call_contracts","unusual_put_contracts","call_implied_volatility","put_implied_volatility","microstructure_status","volume_acceleration_5m","price_pressure_5m_pct","last_bar_close_location","last_bar_dollar_volume"]
            st.dataframe(evidence[columns(evidence, evidence_cols)], hide_index=True, use_container_width=True)

    shadow_summary = data["v4_shadow_summary"]
    st.subheader("V3 versus V4.5 forward evidence")
    if shadow_summary.empty:
        st.info("Point-in-time comparison is collecting; five later sessions are required before an observation matures.")
    else:
        st.dataframe(shadow_summary, hide_index=True, use_container_width=True)
    shadow_daily = data["v4_shadow_daily"]
    if not shadow_daily.empty and "as_of_session" in shadow_daily:
        chart = shadow_daily.copy()
        chart["as_of_session"] = pd.to_datetime(chart["as_of_session"], errors="coerce")
        chart = chart.dropna(subset=["as_of_session"]).set_index("as_of_session")
        if "top_k_agreement_pct" in chart:
            st.caption("Daily top-20 V3/V4.5 ranking agreement")
            st.line_chart(chart[["top_k_agreement_pct"]])

    with st.expander("Performance by theme, catalyst and market regime"):
        breakdowns = data["v4_shadow_breakdowns"]
        st.dataframe(breakdowns, hide_index=True, use_container_width=True) if not breakdowns.empty else st.write("No mature segment evidence yet.")
    with st.expander("V5 regime-adaptive research ranking"):
        v5_ranked = data["v5_ranked"]
        if v5_ranked.empty:
            st.write("V5 is waiting for sufficient resolved outcomes.")
        else:
            v5_cols = ["ticker","company_name","price","v5_adaptive_score","v5_p5_probability","v5_p10_probability","v5_p15_probability","market_regime_state","theme","catalyst_type","entry_model","market_hunt_score","v5_model_version","v5_model_status"]
            st.dataframe(v5_ranked[columns(v5_ranked, v5_cols)].head(30), hide_index=True, use_container_width=True)
        if not data["v5_validation"].empty:
            st.dataframe(data["v5_validation"], hide_index=True, use_container_width=True)

    with st.expander("V6 uncertainty-aware ensemble ranking"):
        v6_ranked = data["v6_ranked"]
        if v6_ranked.empty:
            st.write("V6 is waiting for sufficient chronological train, calibration and holdout evidence.")
        else:
            v6_cols = ["ticker","company_name","price","v6_robust_score","v6_decision","v6_confidence","v6_p5_probability","v6_p5_lower","v6_p5_upper","v6_p10_probability","v6_p10_lower","v6_p10_upper","v6_p15_probability","v6_p15_lower","v6_p15_upper","v6_max_disagreement_pp","market_regime_state","theme","effective_rr","entry_trigger","stop","v6_model_version"]
            st.dataframe(v6_ranked[columns(v6_ranked, v6_cols)].head(30), hide_index=True, use_container_width=True)
        if not data["v6_validation"].empty:
            st.dataframe(data["v6_validation"], hide_index=True, use_container_width=True)

    with st.expander("V7 portfolio-aware paper plan"):
        st.caption("Paper simulation only. No broker execution is enabled.")
        v7_portfolio = data["v7_portfolio"]
        if v7_portfolio.empty:
            st.write("V7 is holding until V6 validates and eligible candidates pass all portfolio risk limits.")
        else:
            v7_cols = ["v7_allocation_rank","ticker","company_name","sector","theme","v6_robust_score","v6_confidence","v7_paper_shares","v7_entry","v7_stop","v7_position_notional","v7_position_risk","v7_position_risk_pct","v7_cumulative_risk_pct","v7_action"]
            st.dataframe(v7_portfolio[columns(v7_portfolio, v7_cols)], hide_index=True, use_container_width=True)

    with st.expander("V7.2 bounded criteria optimizer"):
        st.caption("Shadow proposal only. It cannot change production criteria or enable broker execution.")
        proposed = v72_proposal.get("recommended_change", {})
        if v72_status == "PROPOSAL_ELIGIBLE" and proposed:
            st.warning(
                "Manual review candidate: "
                f"{proposed.get('criterion')} {proposed.get('operator')} {proposed.get('proposed_value')}. "
                "No production change has been applied."
            )
        else:
            st.write(v72_proposal.get("reason", "Waiting for sufficient mature point-in-time evidence."))
        if not data["v72_validation"].empty:
            st.dataframe(data["v72_validation"], hide_index=True, use_container_width=True)
        if not data["v72_grid"].empty:
            st.caption("Allowlisted one-parameter candidates evaluated on the training window")
            st.dataframe(data["v72_grid"], hide_index=True, use_container_width=True)

    with st.expander("V7.3 prospective challenger"):
        st.caption("Future-only shadow validation. Historical observations cannot validate a newly registered challenger.")
        active = v73_health.get("active_challenger") or {}
        if active:
            change = active.get("change", {})
            st.write(
                f"{active.get('challenger_id', 'challenger')} · starts after "
                f"{active.get('start_after_session', 'registration')} · "
                f"{change.get('criterion')} {change.get('operator')} {change.get('proposed_value')}"
            )
        st.write(v73_health.get("reason", "Waiting for a V7.2 holdout-passing proposal."))
        c1,c2,c3 = st.columns(3)
        c1.metric("Future sessions", int(number(v73_health.get("future_sessions"))))
        c2.metric("Baseline samples", int(number(v73_health.get("baseline_samples"))))
        c3.metric("Challenger samples", int(number(v73_health.get("challenger_samples"))))
        if not data["v73_comparison"].empty:
            st.dataframe(data["v73_comparison"], hide_index=True, use_container_width=True)

with social_tab:
    st.subheader("Market Hunt Social Studio")
    st.markdown(
        '<div class="section-note">Verified-scan drafts for X. Nothing in this queue is authorized, scheduled or published automatically.</div>',
        unsafe_allow_html=True,
    )
    queue = data["social_queue"]
    s1,s2,s3,s4 = st.columns(4)
    s1.metric("Engine", str(social_health.get("status", "WAITING")))
    s2.metric("Drafts", int(number(social_health.get("drafts_generated"))))
    s3.metric("Approved", 0)
    s4.metric("External actions", int(number(social_health.get("external_actions_taken"))))
    if bool(social_health.get("publish_authorized", False)):
        st.error("Safety invariant failed: generated content must not be pre-authorized.")
    if queue.empty:
        st.info(social_health.get("reason", "Social drafts are waiting for the next healthy broad scan."))
    else:
        for _, row in queue.iterrows():
            st.markdown(f"**{row.get('content_type', 'DRAFT')} · {row.get('suggested_slot_et', '')}**")
            st.code(str(row.get("draft_text", "")), language=None)
            st.caption(
                f"{row.get('content_id', '')} · {row.get('status', '')} · source: "
                f"{row.get('source_dataset', '')}"
            )
        with st.expander("Calendar and approval queue"):
            st.dataframe(data["social_calendar"], hide_index=True, use_container_width=True)

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
    st.write("5k+ universe → liquidity gate → themes → technical structure → D/W/M levels → runway and R/R → catalysts → live VWAP/ORB/RVOL → V4 calibration/monitoring → V5 adaptive ranking → V6 uncertainty/abstention → V7 paper risk allocation → V7.2 bounded proposals → V7.3 prospective challengers → guarded cutover")
    status_rows=pd.DataFrame([{"dataset":key,"rows":len(data[key]),"source":sources[key]} for key in files])
    st.dataframe(status_rows,hide_index=True,use_container_width=True)
    st.subheader("V8.1 operational health")
    o1,o2,o3,o4=st.columns(4)
    o1.metric("Operational state", str(v81_health.get("status", "COLLECTING")))
    o2.metric("Remote files", int(number(dashboard_fetch_health.get("remote_files_loaded"))))
    o3.metric("Remote failures", int(number(dashboard_fetch_health.get("remote_files_failed"))))
    o4.metric("Load time", f'{number(dashboard_fetch_health.get("elapsed_seconds")):.2f}s')
    if v81_health.get("failed_checks"):
        st.error("Operational failures: " + ", ".join(map(str, v81_health["failed_checks"])))
    elif v81_health.get("collecting_checks"):
        st.info("Operational evidence collecting: " + ", ".join(map(str, v81_health["collecting_checks"])))
    st.caption(f"Health probe: {v81_health.get('health_endpoint', '/_stcore/health')} · source: {v81_health_source}")
    st.subheader("V9 readiness (manual review only)")
    r1,r2,r3=st.columns(3)
    r1.metric("Readiness state", str(v9_readiness.get("status", "BLOCKED_BY_V8_VALIDATION")))
    r2.metric("Mature evidence", int(number(v9_readiness.get("mature_evidence"))))
    r3.metric("Broker execution", "DISABLED" if not bool(v9_readiness.get("broker_execution_enabled", False)) else "ENABLED")
    if v9_readiness.get("failed_gates"):
        st.warning("V9 remains blocked by: " + ", ".join(map(str, v9_readiness["failed_gates"])))
    st.caption(f"No automatic activation; explicit manual approval is always required · source: {v9_readiness_source}")
    tradable=data["tradable"]
    if not tradable.empty:
        passed=int(tradable["tradable"].sum()) if "tradable" in tradable else len(tradable)
        st.metric("Current tradability gate",f"{passed:,} symbols")
    st.warning("Research and decision support only. Data may be delayed or incomplete. Stops can gap and no setup guarantees a 5–10% move.")
