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

def add_live_table_context(frame, live_frame, momentum_frame, rotation_frame):
    """Overlay current market price/state on deep-scan tables and derive a display-only live action state."""
    if frame is None or frame.empty:
        return frame
    out = frame.copy()
    if "ticker" not in out.columns:
        return out
    out["ticker"] = out["ticker"].astype(str)
    out["current_price"] = pd.to_numeric(out.get("price"), errors="coerce")
    out["live_state"] = ""
    out["momentum_signal"] = ""
    out["day_change_pct_live"] = pd.NA
    out["rel_vs_spy_live"] = pd.NA
    out["live_intraday_rvol"] = pd.NA
    out["theme_rotation_score_live"] = pd.NA
    out["live_confirmation_score_current"] = pd.NA
    out["display_state"] = out.get("stage", pd.Series("WATCH", index=out.index)).astype(str)

    if live_frame is not None and not live_frame.empty and "ticker" in live_frame.columns:
        lv=live_frame.copy()
        lv["ticker"]=lv["ticker"].astype(str)
        lv=lv.drop_duplicates("ticker", keep="first").set_index("ticker")
        if "live_price" in lv:
            out["current_price"]=out["ticker"].map(pd.to_numeric(lv["live_price"], errors="coerce")).combine_first(out["current_price"])
        if "monitor_state" in lv:
            out["live_state"]=out["ticker"].map(lv["monitor_state"]).fillna("")
        if "intraday_rvol" in lv:
            out["live_intraday_rvol"]=out["ticker"].map(pd.to_numeric(lv["intraday_rvol"], errors="coerce"))
        if "live_confirmation_score" in lv:
            out["live_confirmation_score_current"]=out["ticker"].map(pd.to_numeric(lv["live_confirmation_score"], errors="coerce"))

    if momentum_frame is not None and not momentum_frame.empty and "ticker" in momentum_frame.columns:
        ms=momentum_frame.copy()
        ms["ticker"]=ms["ticker"].astype(str)
        ms=ms.drop_duplicates("ticker", keep="first").set_index("ticker")
        if "price" in ms:
            out["current_price"]=out["ticker"].map(pd.to_numeric(ms["price"], errors="coerce")).combine_first(out["current_price"])
        if "signal" in ms:
            out["momentum_signal"]=out["ticker"].map(ms["signal"]).fillna("")
        if "intraday_rvol" in ms:
            out["live_intraday_rvol"]=out["ticker"].map(pd.to_numeric(ms["intraday_rvol"], errors="coerce")).combine_first(pd.to_numeric(out["live_intraday_rvol"], errors="coerce"))
        if "day_change_pct" in ms:
            out["day_change_pct_live"]=out["ticker"].map(pd.to_numeric(ms["day_change_pct"], errors="coerce"))
        if "rel_vs_spy_pct" in ms:
            out["rel_vs_spy_live"]=out["ticker"].map(pd.to_numeric(ms["rel_vs_spy_pct"], errors="coerce"))
        if "theme_rotation_score" in ms:
            out["theme_rotation_score_live"]=out["ticker"].map(pd.to_numeric(ms["theme_rotation_score"], errors="coerce"))

    if rotation_frame is not None and not rotation_frame.empty and "ticker" in rotation_frame.columns:
        rl=rotation_frame.copy()
        rl["ticker"]=rl["ticker"].astype(str)
        rl=rl.drop_duplicates("ticker", keep="first").set_index("ticker")
        if "last" in rl:
            out["current_price"]=out["ticker"].map(pd.to_numeric(rl["last"], errors="coerce")).combine_first(out["current_price"])
        if "day_change_pct" in rl:
            out["day_change_pct_live"]=out["ticker"].map(pd.to_numeric(rl["day_change_pct"], errors="coerce")).combine_first(pd.to_numeric(out["day_change_pct_live"], errors="coerce"))
        if "rel_vs_spy_pct" in rl:
            out["rel_vs_spy_live"]=out["ticker"].map(pd.to_numeric(rl["rel_vs_spy_pct"], errors="coerce")).combine_first(pd.to_numeric(out["rel_vs_spy_live"], errors="coerce"))
        if "theme_rotation_score" in rl:
            out["theme_rotation_score_live"]=out["ticker"].map(pd.to_numeric(rl["theme_rotation_score"], errors="coerce")).combine_first(pd.to_numeric(out["theme_rotation_score_live"], errors="coerce"))

    strong_buy = (
        out["momentum_signal"].eq("MOMENTUM BUY")
        | out["live_state"].eq("LIVE_CONFIRMED")
        | (
            pd.to_numeric(out["live_confirmation_score_current"], errors="coerce").fillna(0).ge(80)
            & pd.to_numeric(out["live_intraday_rvol"], errors="coerce").fillna(0).ge(1.2)
            & pd.to_numeric(out["rel_vs_spy_live"], errors="coerce").fillna(0).ge(1.0)
        )
    )
    out.loc[strong_buy, "display_state"]="STRONG BUY"
    out.loc[~strong_buy & out["live_state"].eq("TRIGGERED"), "display_state"]="TRIGGERED"
    return out

def add_live_legendary_context(frame, live_frame, momentum_frame, rotation_frame):
    """Overlay current-session market context on slower legendary-agent outputs."""
    if frame is None or frame.empty:
        return frame
    out = frame.copy()
    if "ticker" not in out.columns:
        return out
    out["ticker"] = out["ticker"].astype(str)
    out["current_price"] = pd.to_numeric(out.get("price"), errors="coerce")
    out["live_state"] = ""
    out["momentum_signal"] = ""
    out["live_intraday_rvol"] = pd.NA
    out["day_change_pct_live"] = pd.NA
    out["rel_vs_spy_live"] = pd.NA
    out["theme_rotation_score_live"] = pd.NA
    out["legendary_live_boost"] = 0.0

    if live_frame is not None and not live_frame.empty and "ticker" in live_frame.columns:
        lv = live_frame.copy()
        lv["ticker"] = lv["ticker"].astype(str)
        lv = lv.drop_duplicates("ticker", keep="first").set_index("ticker")
        if "live_price" in lv:
            out["current_price"] = out["ticker"].map(pd.to_numeric(lv["live_price"], errors="coerce")).combine_first(out["current_price"])
        if "monitor_state" in lv:
            out["live_state"] = out["ticker"].map(lv["monitor_state"]).fillna("")
        if "intraday_rvol" in lv:
            out["live_intraday_rvol"] = out["ticker"].map(pd.to_numeric(lv["intraday_rvol"], errors="coerce"))
        state_bonus = out["live_state"].map({"LIVE_CONFIRMED":25,"TRIGGERED":15,"ARMED":8}).fillna(0)
        out["legendary_live_boost"] += state_bonus

    if momentum_frame is not None and not momentum_frame.empty and "ticker" in momentum_frame.columns:
        ms = momentum_frame.copy()
        ms["ticker"] = ms["ticker"].astype(str)
        ms = ms.drop_duplicates("ticker", keep="first").set_index("ticker")
        if "price" in ms:
            out["current_price"] = out["ticker"].map(pd.to_numeric(ms["price"], errors="coerce")).combine_first(out["current_price"])
        if "signal" in ms:
            out["momentum_signal"] = out["ticker"].map(ms["signal"]).fillna("")
        if "intraday_rvol" in ms:
            out["live_intraday_rvol"] = out["ticker"].map(pd.to_numeric(ms["intraday_rvol"], errors="coerce")).combine_first(pd.to_numeric(out["live_intraday_rvol"], errors="coerce"))
        if "day_change_pct" in ms:
            out["day_change_pct_live"] = out["ticker"].map(pd.to_numeric(ms["day_change_pct"], errors="coerce"))
        if "rel_vs_spy_pct" in ms:
            out["rel_vs_spy_live"] = out["ticker"].map(pd.to_numeric(ms["rel_vs_spy_pct"], errors="coerce"))
        if "theme_rotation_score" in ms:
            out["theme_rotation_score_live"] = out["ticker"].map(pd.to_numeric(ms["theme_rotation_score"], errors="coerce"))
        sig_bonus = out["momentum_signal"].map({
            "MOMENTUM BUY":30,
            "WATCH / NEAR ENTRY":18,
            "EXTENDED / WAIT RETEST":6,
        }).fillna(0)
        out["legendary_live_boost"] += sig_bonus

    if rotation_frame is not None and not rotation_frame.empty and "ticker" in rotation_frame.columns:
        rl = rotation_frame.copy()
        rl["ticker"] = rl["ticker"].astype(str)
        rl = rl.drop_duplicates("ticker", keep="first").set_index("ticker")
        if "last" in rl:
            out["current_price"] = out["ticker"].map(pd.to_numeric(rl["last"], errors="coerce")).combine_first(out["current_price"])
        if "day_change_pct" in rl:
            out["day_change_pct_live"] = out["ticker"].map(pd.to_numeric(rl["day_change_pct"], errors="coerce")).combine_first(pd.to_numeric(out["day_change_pct_live"], errors="coerce"))
        if "rel_vs_spy_pct" in rl:
            out["rel_vs_spy_live"] = out["ticker"].map(pd.to_numeric(rl["rel_vs_spy_pct"], errors="coerce")).combine_first(pd.to_numeric(out["rel_vs_spy_live"], errors="coerce"))
        if "theme_rotation_score" in rl:
            out["theme_rotation_score_live"] = out["ticker"].map(pd.to_numeric(rl["theme_rotation_score"], errors="coerce")).combine_first(pd.to_numeric(out["theme_rotation_score_live"], errors="coerce"))
        out["legendary_live_boost"] += (
            pd.to_numeric(out["theme_rotation_score_live"], errors="coerce").fillna(0) * 0.12
            + pd.to_numeric(out["rel_vs_spy_live"], errors="coerce").fillna(0).clip(lower=0, upper=10)
        )

    base_score = pd.to_numeric(out.get("legendary_score"), errors="coerce") if "legendary_score" in out else pd.Series(50.0, index=out.index)
    out["current_legendary_score"] = (base_score.fillna(50) * 0.72 + out["legendary_live_boost"].clip(0,28)).clip(0,100)
    return out.sort_values(["current_legendary_score"], ascending=False)

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
    "live":"intraday_live.csv", "premarket":"premarket_discovery.csv", "theme_health":"theme_health.csv", "sector_rotation":"sector_rotation.csv", "rotation_leaders":"rotation_leaders.csv", "momentum_signals":"momentum_signals.csv", "transitions":"state_transitions.csv", "themes":"trending_themes.csv",
    "recommendations":"recommended_trades.csv", "leaders":"liquid_leaders.csv", "watchlist":"watchlist.csv",
    "picks":"latest_scan.csv", "tradable":"tradable_universe.csv", "candidates":"all_candidates.csv",
    "events":"upcoming_events.csv", "event_status":"event_status.csv", "journal":"paper_journal.csv",
    "performance":"performance_summary.csv", "performance_setup":"performance_by_setup.csv",
    "calibration":"probability_calibration.csv", "monitor":"monitor_health.csv", "gate":"validation_gate.csv",
    "daily_pick_log":"daily_top_pick_log.csv", "daily_pick_summary":"daily_top_pick_summary.csv",
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
    "v82_scorecard":"v8_2_evidence_scorecard.csv",
    "v9_readiness":"v9_readiness.csv",
    "v91_pilot_candidates":"v9_1_pilot_candidates.csv",
    "v4_live_meta":"v4_live_metadata.csv",
    "v4_monitor_shortlist":"v4_monitor_shortlist.csv",
    "v4_live_snapshot":"v4_live_snapshot.csv",
    "v4_worker_cycles":"v4_worker_cycles.csv",
    "v4_options_microstructure":"v4_options_microstructure.csv",
}
json_files = [
    "v4_6_cutover_evaluation.json", "v4_5_model.json", "v5_model.json",
    "v6_model.json", "v7_allocation_health.json", "v7_1_evidence_health.json",
    "v7_2_criteria_proposal.json", "v7_3_challenger_health.json",
    "social_engine_health.json", "v8_1_operational_health.json", "v8_2_evidence_scorecard.json",
    "v9_readiness.json", "v9_1_pilot_health.json",
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
v82_scorecard, v82_scorecard_source = load_json("v8_2_evidence_scorecard.json")
v9_readiness, v9_readiness_source = load_json("v9_readiness.json")
v91_pilot, v91_pilot_source = load_json("v9_1_pilot_health.json")

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
momentum_signal_frame = data.get("momentum_signals", pd.DataFrame())
momentum_buy_count = int(momentum_signal_frame["signal"].astype(str).eq("MOMENTUM BUY").sum()) if not momentum_signal_frame.empty and "signal" in momentum_signal_frame else 0
m5.metric("Alert events", f'{int(number(mh.get("alerts_generated"))):,}', help="State-change alerts generated by the base live monitor, including invalidations and breakout changes. This is not the number of current BUY opportunities.")

overview, opportunities, legendary_tab, live_tab, event_tab, v4_tab, social_tab, validation, system = st.tabs(
    ["Overview", "Opportunities", "Legendary setups", "Live monitor", "Events", "V4 Intelligence", "Social Studio", "Validation", "System"]
)

with overview:
    st.subheader("Today at a glance")
    st.markdown('<div class="section-note">Current actionable = unique live BUY opportunities. Confirmed now = LIVE_CONFIRMED or MOMENTUM BUY. Alert events = state changes such as triggers, failures or invalidations, so alert-event counts are intentionally separate.</div>', unsafe_allow_html=True)
    themes, recommendations, leaders, watchlist, live = data["themes"], data["recommendations"], data["leaders"], data["watchlist"], data["live"]
    sector_rotation, rotation_leaders, momentum_signals = data["sector_rotation"], data["rotation_leaders"], data["momentum_signals"]
    # One consistent current-actionable universe across strict swing and fast momentum engines.
    strict_rec_tickers = set(recommendations["ticker"].astype(str)) if not recommendations.empty and "ticker" in recommendations else set()
    base_confirmed_tickers = set(
        live.loc[live["monitor_state"].astype(str).eq("LIVE_CONFIRMED"), "ticker"].astype(str)
    ) if not live.empty and {"monitor_state","ticker"}.issubset(live.columns) else set()
    momentum_buy_tickers = set(
        momentum_signals.loc[momentum_signals["signal"].astype(str).eq("MOMENTUM BUY"), "ticker"].astype(str)
    ) if not momentum_signals.empty and {"signal","ticker"}.issubset(momentum_signals.columns) else set()

    confirmed_tickers = base_confirmed_tickers | momentum_buy_tickers
    actionable_tickers = strict_rec_tickers | confirmed_tickers

    a,b,c,d,e = st.columns(5)
    a.metric("Current actionable", len(actionable_tickers), help="Unique tickers currently actionable across strict swing recommendations and fast momentum BUY signals.")
    b.metric("Tracked leaders", len(leaders))
    c.metric("Research watchlist", len(watchlist))
    d.metric("Leading themes", len(themes))
    e.metric("Confirmed now", len(confirmed_tickers), help="Unique tickers currently LIVE_CONFIRMED or MOMENTUM BUY. This is a current state, not an alert-event count.")
    recommendations_live = add_live_table_context(recommendations, live, momentum_signals, rotation_leaders)
    if recommendations_live.empty and not momentum_signals.empty:
        recommendations_live = add_live_table_context(momentum_signals[momentum_signals["signal"].astype(str).eq("MOMENTUM BUY")].copy(), live, momentum_signals, rotation_leaders)
    if not recommendations_live.empty:
        for _, row in recommendations_live.head(5).iterrows():
            ticker = str(row.get("ticker","—")); stage = str(row.get("display_state", row.get("stage","WATCH")))
            decision = str(row.get("final_decision", row.get("decision","RESEARCH")))
            score = number(row.get("market_hunt_score")); rr = number(row.get("effective_rr"))
            tone = "good" if "BUY" in decision or stage == "CONFIRMED" else "warn"
            st.markdown(f'<div class="signal"><b>{ticker}</b> · <span class="{tone}">{stage}</span>'
                        f'<br><span class="muted">{decision} · score {score:.1f} · R/R {rr:.2f}×</span></div>', unsafe_allow_html=True)
    else:
        st.info("No stock currently passes every recommendation gate. Preliminary setups remain in the research watchlist.")
    if not momentum_signals.empty:
        st.subheader("Fast momentum signals")
        ms_cols=["ticker","theme","signal","price","day_change_pct","move_30m_pct","rel_vs_spy_pct","theme_rotation_score","intraday_rvol","vwap","opening_range_high","entry","stop","risk_pct","target_5pct","target_8pct","last_bar_et"]
        st.dataframe(momentum_signals[columns(momentum_signals,ms_cols)].head(30),hide_index=True,use_container_width=True)
    if not sector_rotation.empty:
        st.subheader("Live sector rotation")
        rot_cols=["rotation_rank","theme","etf","etf_change_pct","rel_vs_spy_pct","breadth_pct","rotation_score","rotation_state","updated_at_et"]
        st.dataframe(sector_rotation[columns(sector_rotation,rot_cols)].head(10),hide_index=True,use_container_width=True)
    if not rotation_leaders.empty:
        hot=rotation_leaders[rotation_leaders.get("rotation_leader",False).astype(str).str.lower().isin(["true","1","yes"])] if "rotation_leader" in rotation_leaders else rotation_leaders
        if not hot.empty:
            st.subheader("Rotation leaders")
            lead_cols=["rotation_rank","ticker","theme","last","day_change_pct","move_30m_pct","rel_vs_spy_pct","theme_rotation_state","rotation_leader_score","last_bar_et"]
            st.dataframe(hot[columns(hot,lead_cols)].head(25),hide_index=True,use_container_width=True)
    if not themes.empty:
        st.subheader("Leading themes")
        st.dataframe(themes.head(10)[columns(themes,["theme_rank","theme","etf","theme_score","theme_state","ret5_pct","ret20_pct","rel5_vs_spy","rel20_vs_spy"])],
                     hide_index=True,use_container_width=True)

with opportunities:
    premarket = data["premarket"]
    live = data["live"]
    momentum_signals = data["momentum_signals"]
    rotation_leaders = data["rotation_leaders"]

    st.subheader("Current Live Opportunities")
    st.markdown('<div class="section-note">This section updates from the live 15-minute engines. It prioritizes current momentum BUYs, near-entry setups, rotation leaders and live-confirmed base-scan names before the slower full-scan list below.</div>', unsafe_allow_html=True)

    live_parts = []
    if not momentum_signals.empty:
        ms = momentum_signals.copy()
        ms["opportunity_source"] = "FAST_MOMENTUM"
        ms["opportunity_state"] = ms.get("signal", "")
        ms["current_price"] = pd.to_numeric(ms.get("price"), errors="coerce")
        ms["live_score"] = (
            pd.to_numeric(ms.get("theme_rotation_score"), errors="coerce").fillna(0) * 0.45
            + pd.to_numeric(ms.get("rel_vs_spy_pct"), errors="coerce").fillna(0).clip(lower=0, upper=10) * 4
            + pd.to_numeric(ms.get("intraday_rvol"), errors="coerce").fillna(0).clip(lower=0, upper=5) * 3
        )
        sig_bonus = ms["opportunity_state"].map({
            "MOMENTUM BUY": 30,
            "WATCH / NEAR ENTRY": 18,
            "EXTENDED / WAIT RETEST": 8,
            "NO SIGNAL": 0,
        }).fillna(0)
        ms["live_score"] = (ms["live_score"] + sig_bonus).clip(0, 100)
        live_parts.append(ms)

    if not rotation_leaders.empty:
        rl = rotation_leaders.copy()
        if "rotation_leader" in rl.columns:
            rl = rl[rl["rotation_leader"].astype(str).str.lower().isin(["true","1","yes"])]
        if not rl.empty:
            rl["opportunity_source"] = "ROTATION"
            rl["opportunity_state"] = rl.get("theme_rotation_state", "ROTATION")
            rl["current_price"] = pd.to_numeric(rl.get("last"), errors="coerce")
            rl["live_score"] = (
                pd.to_numeric(rl.get("rotation_leader_score"), errors="coerce").fillna(0) * 0.55
                + pd.to_numeric(rl.get("theme_rotation_score"), errors="coerce").fillna(0) * 0.35
                + pd.to_numeric(rl.get("rel_vs_spy_pct"), errors="coerce").fillna(0).clip(lower=0, upper=10)
            ).clip(0,100)
            live_parts.append(rl)

    if not live.empty:
        lv = live.copy()
        lv["opportunity_source"] = "LIVE_MONITOR"
        lv["opportunity_state"] = lv.get("monitor_state", "")
        lv["current_price"] = pd.to_numeric(lv.get("live_price"), errors="coerce")
        lv["live_score"] = (
            pd.to_numeric(lv.get("live_confirmation_score"), errors="coerce").fillna(0) * 0.65
            + pd.to_numeric(lv.get("market_hunt_score"), errors="coerce").fillna(0) * 0.35
        ).clip(0,100)
        live_parts.append(lv)

    if live_parts:
        unified = pd.concat(live_parts, ignore_index=True, sort=False)
        unified["ticker"] = unified["ticker"].astype(str)
        priority = {
            "MOMENTUM BUY": 5,
            "LIVE_CONFIRMED": 5,
            "TRIGGERED": 4,
            "WATCH / NEAR ENTRY": 3,
            "ROTATION_LEADER": 3,
            "STRONG_ROTATION": 2,
            "EXTENDED / WAIT RETEST": 1,
        }
        unified["_priority"] = unified["opportunity_state"].map(priority).fillna(0)
        unified = unified.sort_values(["_priority","live_score"], ascending=[False,False])
        unified = unified.drop_duplicates(subset=["ticker"], keep="first")
        unified = unified.drop(columns=["_priority"], errors="ignore")
        live_cols=["ticker","theme","opportunity_source","opportunity_state","current_price",
                   "day_change_pct","move_30m_pct","rel_vs_spy_pct","intraday_rvol",
                   "live_confirmation_score","live_score","entry","entry_trigger","stop",
                   "target_5pct","target_8pct","last_bar_et","checked_at_et"]
        st.dataframe(unified[columns(unified,live_cols)].head(50), hide_index=True, use_container_width=True)
    else:
        st.info("No live opportunity data has been published yet.")

    st.subheader("Fresh Market Discoveries")
    st.markdown('<div class="section-note">Fresh discoveries now combine pre-market movers, current rotation leaders and fast momentum signals so this table continues updating after the opening bell.</div>', unsafe_allow_html=True)
    fresh_parts = []
    if not premarket.empty:
        pm = premarket.copy()
        pm["discovery_source"] = "PREMARKET"
        pm["current_price"] = pd.to_numeric(pm.get("premarket_price"), errors="coerce")
        pm["day_change_pct"] = pd.to_numeric(pm.get("premarket_gap_pct"), errors="coerce")
        pm["discovery_score"] = pd.to_numeric(pm.get("premarket_score"), errors="coerce").fillna(0)
        pm["last_update_et"] = pm.get("premarket_last_bar_et", "")
        fresh_parts.append(pm)
    if not rotation_leaders.empty:
        rl = rotation_leaders.copy()
        if "rotation_leader" in rl.columns:
            rl = rl[rl["rotation_leader"].astype(str).str.lower().isin(["true","1","yes"])]
        if not rl.empty:
            rl["discovery_source"] = "LIVE_ROTATION"
            rl["current_price"] = pd.to_numeric(rl.get("last"), errors="coerce")
            rl["discovery_score"] = (
                pd.to_numeric(rl.get("rotation_leader_score"), errors="coerce").fillna(0) * 0.55
                + pd.to_numeric(rl.get("theme_rotation_score"), errors="coerce").fillna(0) * 0.45
            ).clip(0,100)
            rl["last_update_et"] = rl.get("last_bar_et", "")
            fresh_parts.append(rl)
    if not momentum_signals.empty:
        ms = momentum_signals.copy()
        ms["discovery_source"] = "FAST_MOMENTUM"
        ms["current_price"] = pd.to_numeric(ms.get("price"), errors="coerce")
        ms["discovery_score"] = (
            pd.to_numeric(ms.get("theme_rotation_score"), errors="coerce").fillna(0) * 0.45
            + pd.to_numeric(ms.get("rel_vs_spy_pct"), errors="coerce").fillna(0).clip(lower=0,upper=10) * 4
            + pd.to_numeric(ms.get("intraday_rvol"), errors="coerce").fillna(0).clip(lower=0,upper=5) * 3
        )
        ms["discovery_score"] += ms.get("signal", pd.Series("", index=ms.index)).map({
            "MOMENTUM BUY": 30,
            "WATCH / NEAR ENTRY": 18,
            "EXTENDED / WAIT RETEST": 8,
        }).fillna(0)
        ms["discovery_score"] = ms["discovery_score"].clip(0,100)
        ms["last_update_et"] = ms.get("last_bar_et", "")
        fresh_parts.append(ms)

    if fresh_parts:
        fresh = pd.concat(fresh_parts, ignore_index=True, sort=False)
        fresh["ticker"] = fresh["ticker"].astype(str)
        fresh = fresh.sort_values("discovery_score", ascending=False)
        fresh = fresh.drop_duplicates(subset=["ticker"], keep="first")
        fresh_cols=["ticker","company_name","theme","discovery_source","signal","current_price","day_change_pct",
                    "move_30m_pct","rel_vs_spy_pct","intraday_rvol","discovery_score","last_update_et"]
        st.dataframe(fresh[columns(fresh,fresh_cols)].head(100), hide_index=True, use_container_width=True)
    else:
        st.info("No fresh market discoveries are published yet.")

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

        # Overlay current-session intelligence onto the slower full-scan ranking.
        broad["live_overlay_score"] = 0.0
        broad["live_overlay_state"] = ""
        broad["live_overlay_source"] = ""
        broad["current_price"] = pd.to_numeric(broad.get("price"), errors="coerce")

        if not rotation_leaders.empty:
            rl_map = rotation_leaders.copy()
            rl_map["ticker"] = rl_map["ticker"].astype(str)
            rl_map = rl_map.drop_duplicates("ticker", keep="first").set_index("ticker")
            broad["rotation_score_live"] = broad["ticker"].astype(str).map(pd.to_numeric(rl_map.get("theme_rotation_score"), errors="coerce") if "theme_rotation_score" in rl_map else pd.Series(dtype=float))
            broad["rotation_leader_score_live"] = broad["ticker"].astype(str).map(pd.to_numeric(rl_map.get("rotation_leader_score"), errors="coerce") if "rotation_leader_score" in rl_map else pd.Series(dtype=float))
            broad["day_change_pct_live"] = broad["ticker"].astype(str).map(pd.to_numeric(rl_map.get("day_change_pct"), errors="coerce") if "day_change_pct" in rl_map else pd.Series(dtype=float))
            broad["rel_vs_spy_live"] = broad["ticker"].astype(str).map(pd.to_numeric(rl_map.get("rel_vs_spy_pct"), errors="coerce") if "rel_vs_spy_pct" in rl_map else pd.Series(dtype=float))
            broad["current_price"] = broad["ticker"].astype(str).map(pd.to_numeric(rl_map.get("last"), errors="coerce") if "last" in rl_map else pd.Series(dtype=float)).combine_first(broad["current_price"])
            rot_overlay = (
                broad["rotation_score_live"].fillna(0) * 0.10
                + broad["rotation_leader_score_live"].fillna(0) * 0.10
                + broad["rel_vs_spy_live"].fillna(0).clip(lower=0,upper=10) * 1.0
            )
            broad["live_overlay_score"] += rot_overlay

        if not momentum_signals.empty:
            ms_map = momentum_signals.copy()
            ms_map["ticker"] = ms_map["ticker"].astype(str)
            ms_map = ms_map.drop_duplicates("ticker", keep="first").set_index("ticker")
            broad["momentum_signal"] = broad["ticker"].astype(str).map(ms_map.get("signal", pd.Series(dtype=object)))
            broad["momentum_rvol"] = broad["ticker"].astype(str).map(pd.to_numeric(ms_map.get("intraday_rvol"), errors="coerce") if "intraday_rvol" in ms_map else pd.Series(dtype=float))
            broad["current_price"] = broad["ticker"].astype(str).map(pd.to_numeric(ms_map.get("price"), errors="coerce") if "price" in ms_map else pd.Series(dtype=float)).combine_first(broad["current_price"])
            sig_bonus = broad["momentum_signal"].map({
                "MOMENTUM BUY": 25,
                "WATCH / NEAR ENTRY": 15,
                "EXTENDED / WAIT RETEST": 5,
            }).fillna(0)
            broad["live_overlay_score"] += sig_bonus + broad["momentum_rvol"].fillna(0).clip(lower=0,upper=5) * 2

        if not live.empty:
            lv_map = live.copy()
            lv_map["ticker"] = lv_map["ticker"].astype(str)
            lv_map = lv_map.drop_duplicates("ticker", keep="first").set_index("ticker")
            broad["live_state"] = broad["ticker"].astype(str).map(lv_map.get("monitor_state", pd.Series(dtype=object)))
            broad["live_confirmation_score_current"] = broad["ticker"].astype(str).map(pd.to_numeric(lv_map.get("live_confirmation_score"), errors="coerce") if "live_confirmation_score" in lv_map else pd.Series(dtype=float))
            broad["current_price"] = broad["ticker"].astype(str).map(pd.to_numeric(lv_map.get("live_price"), errors="coerce") if "live_price" in lv_map else pd.Series(dtype=float)).combine_first(broad["current_price"])
            state_bonus = broad["live_state"].map({"LIVE_CONFIRMED":25,"TRIGGERED":15,"ARMED":8}).fillna(0)
            broad["live_overlay_score"] += state_bonus + broad["live_confirmation_score_current"].fillna(0) * 0.10

        broad["current_opportunity_score"] = (
            broad["market_hunt_score"].fillna(0) * 0.70
            + broad["live_overlay_score"].clip(0,30)
        ).clip(0,100)
        broad = broad.sort_values(["current_opportunity_score","market_hunt_score"], ascending=[False,False])

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

        broad_cols = ["ticker","company_name","current_price","price","stage","current_opportunity_score","market_hunt_score","momentum_signal","live_state","day_change_pct_live","rel_vs_spy_live","momentum_rvol","technical_score","formation_score",
                      "rs20_vs_spy","rsi14","rsi_state","volume_vs_20ma","swing_volume_state",
                      "max_up_day_30d_pct","ten_pct_up_days_30d","explosive_move_30d","atr_pct","adr20_pct",
                      "avg_dollar_volume","catalyst_status","catalyst_score","entry_trigger","entry_model","stop",
                      "effective_target","effective_rr","runway_to_next_resistance_pct","pattern"]
        st.caption(f"Showing {min(len(broad_view), int(broad_limit))} of {len(broad_view):,} matching broad-market candidates")
        st.dataframe(broad_view.head(int(broad_limit))[columns(broad_view,broad_cols)], hide_index=True, use_container_width=True)
        st.download_button("Download broad opportunities", broad_view.to_csv(index=False),
                           "market_hunt_broad_opportunities.csv", "text/csv", use_container_width=True,
                           key="download_broad_opportunities")

    superstocks = add_live_table_context(data["trader_superstock"], live, momentum_signals, rotation_leaders)
    if not superstocks.empty:
        superstocks["_live_priority"] = superstocks["display_state"].map({"STRONG BUY":3,"TRIGGERED":2,"FORMING":1}).fillna(0)
        superstocks = superstocks.sort_values(["_live_priority","legendary_score"], ascending=[False,False]).drop(columns=["_live_priority"], errors="ignore")
    st.subheader("Superstock candidates")
    st.markdown('<div class="section-note">Explosive liquid leaders with at least one +10% day in the last 30 sessions, ranked for outsized-move potential. A+ is a research priority, not an automatic trade.</div>', unsafe_allow_html=True)
    if superstocks.empty:
        st.info("No current Superstock candidates yet. The next successful full scan will populate this list.")
    else:
        super_cols=["ticker","company_name","current_price","price","display_state","momentum_signal","live_state","day_change_pct_live","rel_vs_spy_live","live_intraday_rvol","superstock_grade","superstock_action","legendary_score","stage","theme",
                    "market_hunt_score","rs20_vs_spy","rsi14","rsi_state","max_up_day_30d_pct","ten_pct_up_days_30d",
                    "atr_pct","adr20_pct","effective_rr","runway_to_next_resistance_pct","catalyst_status","catalyst_score",
                    "entry_trigger","stop","effective_target","pattern"]
        st.dataframe(superstocks[columns(superstocks,super_cols)].head(25), hide_index=True, use_container_width=True)

    brown = add_live_table_context(data["trader_brownmoose"], live, momentum_signals, rotation_leaders)
    if not brown.empty:
        brown["_live_priority"] = brown["display_state"].map({"STRONG BUY":3,"TRIGGERED":2,"FORMING":1}).fillna(0)
        brown = brown.sort_values(["_live_priority","legendary_score"], ascending=[False,False]).drop(columns=["_live_priority"], errors="ignore")
    st.subheader("Brownmoose-style staged setups")
    st.markdown('<div class="section-note">Research approximation from observed subscriber examples: B1/B2 entries are mapped from EMA/support confluence, while T1/T2/T3 come from overhead resistance. No automatic orders are placed.</div>', unsafe_allow_html=True)
    if brown.empty:
        st.info("No current Brownmoose-style setup passes the confluence and R/R filters.")
    else:
        brown_cols=["ticker","company_name","current_price","price","display_state","momentum_signal","live_state","day_change_pct_live","rel_vs_spy_live","live_intraday_rvol","brownmoose_grade","brownmoose_state","brownmoose_confluence_score",
                    "brown_b1","brown_b1_source","brown_b2","brown_b2_source","brown_invalidation",
                    "brown_t1","brown_t2","brown_t3","brown_rr_to_t1","stage","theme","market_hunt_score",
                    "rs20_vs_spy","rsi14","atr_pct","adr20_pct","catalyst_status","pattern"]
        st.dataframe(brown[columns(brown,brown_cols)].head(25), hide_index=True, use_container_width=True)

    venu = add_live_table_context(data["trader_venu"], live, momentum_signals, rotation_leaders)
    if not venu.empty:
        venu["_live_priority"] = venu["display_state"].map({"STRONG BUY":3,"TRIGGERED":2,"FORMING":1}).fillna(0)
        venu = venu.sort_values(["_live_priority","legendary_score"], ascending=[False,False]).drop(columns=["_live_priority"], errors="ignore")
    st.subheader("Venu-style leaders")
    st.markdown('<div class="section-note">Research approximation with two modes: longer-horizon Position Leaders and tactical Rotation Swings. Theme leadership, catalyst/fundamental proxy, relative strength, trend alignment and pullback/breakout quality drive ranking.</div>', unsafe_allow_html=True)
    if venu.empty:
        st.info("No current Venu-style leader or rotation setup passes the filters.")
    else:
        venu_cols=["ticker","company_name","current_price","price","display_state","momentum_signal","live_state","day_change_pct_live","rel_vs_spy_live","live_intraday_rvol","venu_grade","venu_mode","venu_trend_stack","venu_theme_leadership",
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

    strict_recommendations = add_live_table_context(add_opportunity_context(data["recommendations"]), live, momentum_signals, rotation_leaders)
    momentum_recommendations = pd.DataFrame()
    if not momentum_signals.empty and "signal" in momentum_signals.columns:
        momentum_recommendations = momentum_signals[momentum_signals["signal"].astype(str).eq("MOMENTUM BUY")].copy()
        momentum_recommendations = add_live_table_context(momentum_recommendations, live, momentum_signals, rotation_leaders)
    recommendations = pd.concat([strict_recommendations, momentum_recommendations], ignore_index=True, sort=False) if (not strict_recommendations.empty or not momentum_recommendations.empty) else pd.DataFrame()
    if not recommendations.empty and "ticker" in recommendations.columns:
        recommendations = recommendations.drop_duplicates("ticker", keep="first")
    st.subheader("Live-confirmed recommendations / Strong Buy")
    st.markdown('<div class="section-note">Only stocks passing liquidity, volatility, catalyst, spread, runway, R/R and live VWAP/ORB/RVOL gates appear here.</div>', unsafe_allow_html=True)
    recommendation_cols=["ticker","company_name","current_price","live_price","display_state","stage","momentum_signal","live_state","day_change_pct_live","rel_vs_spy_live","theme","market_hunt_score",
                         "rsi14","rsi_state","volume_vs_20ma","swing_volume_state",
                         "live_above_vwap","volume_vs_9ma","opening_30m_rvol","opening_volume_spike_2x",
                         "catalyst_status","catalyst_score","intraday_rvol","bid_ask_spread_pct","entry_trigger","stop",
                         "effective_target","effective_rr","live_trade_action"]
    if recommendations.empty:
        st.info("No actionable recommendation currently passes every gate.")
    else:
        st.dataframe(recommendations[columns(recommendations,recommendation_cols)],hide_index=True,use_container_width=True)
        st.download_button("Download recommendations", recommendations.to_csv(index=False), "market_hunt_recommendations.csv", "text/csv", use_container_width=True)

    base_picks = data["watchlist"] if not data["watchlist"].empty else data["picks"]
    v4_live_picks = data["v4_live_snapshot"]
    picks_parts = [x for x in [base_picks, v4_live_picks] if x is not None and not x.empty]
    picks = pd.concat(picks_parts, ignore_index=True, sort=False) if picks_parts else pd.DataFrame()
    if not picks.empty and "ticker" in picks.columns:
        picks["ticker"] = picks["ticker"].astype(str)
        picks = picks.drop_duplicates("ticker", keep="last")
    picks = add_live_table_context(add_opportunity_context(picks), live, momentum_signals, rotation_leaders)
    st.subheader("Research watchlist")
    st.markdown('<div class="section-note">Deep-scan FORMING/DISCOVER setups are overlaid with current live prices. When live confirmation and momentum gates align, the display state upgrades to STRONG BUY without changing the underlying historical stage.</div>', unsafe_allow_html=True)
    if picks.empty: st.info("No watchlist results are available yet.")
    else:
        query = st.text_input("Find ticker or company", placeholder="AXTI, IOVA…")
        view = picks.copy()
        if query:
            mask = view.astype(str).apply(lambda col: col.str.contains(query,case=False,na=False)).any(axis=1)
            view = view[mask]
        f1, f2 = st.columns(2)
        with f1:
            stages = sorted(view["stage"].dropna().astype(str).unique()) if "stage" in view else []
            selected = st.multiselect("Historical stage", stages, default=stages)
        with f2:
            actions = sorted(view["display_state"].dropna().astype(str).unique()) if "display_state" in view else []
            selected_actions = st.multiselect("Live action", actions, default=actions)
        if selected and "stage" in view:
            view = view[view["stage"].astype(str).isin(selected)]
        if selected_actions and "display_state" in view:
            view = view[view["display_state"].astype(str).isin(selected_actions)]
        priority=["ticker","company_name","current_price","price","display_state","stage","momentum_signal","live_state","day_change_pct_live","rel_vs_spy_live","live_intraday_rvol","theme","theme_state","market_hunt_score","final_decision",
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
    legendary_live = data["live"]
    legendary_momentum = data["momentum_signals"]
    legendary_rotation = data["rotation_leaders"]
    st.markdown('<div class="section-note">Agent memberships come from the latest deep scan, but prices, momentum, live state, RVOL, sector rotation and ranking are overlaid from the current intraday session. This prevents stale full-scan ordering during market hours.</div>', unsafe_allow_html=True)

    consensus = add_live_legendary_context(data["legendary_consensus"], legendary_live, legendary_momentum, legendary_rotation)
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
        frame = add_live_legendary_context(data[key], legendary_live, legendary_momentum, legendary_rotation)
        with preview_cols[idx]:
            st.markdown(
                f'<div class="agent-card"><div class="name">{trader_name}</div>'
                f'<div class="setup">{setup_name}</div>'
                f'<div class="count">{len(frame)}</div>'
                f'<div class="setup">current matches</div></div>',
                unsafe_allow_html=True,
            )

    for trader_name, setup_name, key in trader_views:
        frame = add_live_legendary_context(data[key], legendary_live, legendary_momentum, legendary_rotation)
        with st.expander(f"{trader_name} · {setup_name}", expanded=False):
            if frame.empty:
                st.write("No current matches.")
            else:
                preferred = ["ticker","company_name","current_price","price","current_legendary_score","momentum_signal","live_state","day_change_pct_live","rel_vs_spy_live","live_intraday_rvol","theme_rotation_score_live","venu_grade","venu_mode","venu_trend_stack","venu_theme_leadership","venu_fundamental_proxy_score","brownmoose_grade","brownmoose_state","brownmoose_confluence_score","brown_b1","brown_b1_source","brown_b2","brown_b2_source","brown_invalidation","brown_t1","brown_t2","brown_t3","brown_rr_to_t1","superstock_grade","superstock_action","momentum_grade","setup_match","legendary_score","stage","theme",
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
    live, premarket, transitions, journal = data["live"], data["premarket"], data["transitions"], data["journal"]
    momentum_signals = data["momentum_signals"]
    rotation_leaders = data["rotation_leaders"]

    st.subheader("Live confirmed & momentum signals")
    if not momentum_signals.empty:
        ms_cols=["ticker","theme","signal","price","day_change_pct","move_30m_pct","rel_vs_spy_pct","theme_rotation_score",
                 "intraday_rvol","vwap","opening_range_high","entry","stop","risk_pct","target_5pct","target_8pct","last_bar_et"]
        st.dataframe(momentum_signals[columns(momentum_signals,ms_cols)].head(40),hide_index=True,use_container_width=True)
    else:
        st.info("Fast momentum signal output is not available yet.")

    if not rotation_leaders.empty:
        st.subheader("Rotation leaders being watched")
        rl_cols=["rotation_rank","ticker","theme","day_change_pct","move_30m_pct","rel_vs_spy_pct","theme_rotation_score",
                 "theme_rotation_state","rotation_leader_score","rotation_leader","last_bar_et"]
        st.dataframe(rotation_leaders[columns(rotation_leaders,rl_cols)].head(40),hide_index=True,use_container_width=True)

    st.subheader("Fresh market discoveries")
    if not premarket.empty:
        pm_cols=["premarket_rank","ticker","company_name","premarket_price","premarket_gap_pct","premarket_volume","premarket_dollar_volume","premarket_score","premarket_last_bar_et"]
        st.dataframe(premarket[columns(premarket,pm_cols)].head(100),hide_index=True,use_container_width=True)
    else:
        st.info("Fresh pre-market discovery output is not available yet.")

    st.subheader("Base-scan intraday confirmation")
    st.markdown('<div class="section-note">This table tracks the top base-scan candidates. Cybersecurity and other fast-rotation names are shown above even when they were not in the original top-30 shortlist. No broker orders are placed.</div>', unsafe_allow_html=True)
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
    st.subheader("Current V4 Live Intelligence")
    v4_live_meta = data["v4_live_meta"]
    v4_shortlist = data["v4_monitor_shortlist"]
    v4_snapshot = data["v4_live_snapshot"]
    v4_cycles = data["v4_worker_cycles"]
    v4_stamp = str(v4_live_meta.iloc[0].get("updated_at_utc","Waiting for V4 live cycle")) if not v4_live_meta.empty else "Waiting for V4 live cycle"
    st.caption(f"Intraday V4 refresh · {v4_stamp} UTC")

    if not v4_snapshot.empty:
        live_cols=["ticker","stage","live_price","entry_trigger","live_vwap","opening_range_high","intraday_rvol",
                   "live_confirmation_score","theme","catalyst_status","live_trade_action","checked_at_et"]
        st.dataframe(v4_snapshot[columns(v4_snapshot,live_cols)].head(40),hide_index=True,use_container_width=True)
    elif not v4_shortlist.empty:
        short_cols=["ticker","stage","market_hunt_score","technical_score","catalyst_score","theme","entry_trigger","stop","effective_target","effective_rr"]
        st.dataframe(v4_shortlist[columns(v4_shortlist,short_cols)].head(40),hide_index=True,use_container_width=True)
    else:
        st.info("V4 live worker has not published its first intraday snapshot yet.")

    if not v4_cycles.empty:
        with st.expander("V4 live worker cycle history"):
            st.dataframe(v4_cycles.tail(50).iloc[::-1],hide_index=True,use_container_width=True)

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
    daily_pick_log, daily_pick_summary = data["daily_pick_log"], data["daily_pick_summary"]
    st.subheader("Daily Top Conviction Paper Pick")
    st.markdown('<div class="section-note">Exactly one paper-only selection per U.S. trading day. Entry, stop and target are frozen when selected; the same row is tracked until TARGET HIT, STOP HIT or EXPIRED. No replacement is allowed after selection.</div>', unsafe_allow_html=True)
    if daily_pick_log.empty:
        st.info("No daily top-conviction paper pick has been recorded yet.")
    else:
        latest_pick = daily_pick_log.sort_values(["selection_date_et","selected_at_et"], ascending=[False,False]).iloc[0]
        p1,p2,p3,p4,p5 = st.columns(5)
        p1.metric("Ticker", str(latest_pick.get("ticker","—")))
        p2.metric("Status", str(latest_pick.get("status","OPEN")))
        p3.metric("Entry", f'{number(latest_pick.get("entry_price")):.2f}')
        p4.metric("Stop", f'{number(latest_pick.get("stop_price")):.2f}')
        p5.metric("Target", f'{number(latest_pick.get("target_price")):.2f}')
        st.caption(
            f'Selected {latest_pick.get("selected_at_et","—")} · source {latest_pick.get("source","—")} · '
            f'score {number(latest_pick.get("selection_score")):.1f} · '
            f'R/R {(number(latest_pick.get("target_price"))-number(latest_pick.get("entry_price"))) / max(number(latest_pick.get("entry_price"))-number(latest_pick.get("stop_price")), 0.0001):.2f}x'
        )
        pick_cols=["selection_date_et","selected_at_et","ticker","source","selection_score","theme","entry_price","stop_price",
                   "target_price","target_pct","risk_pct","status","outcome_at_et","exit_price","return_pct","r_multiple",
                   "mfe_pct","mae_pct","business_days_open","intraday_rvol","rel_vs_spy_pct","theme_rotation_score","reason"]
        st.dataframe(daily_pick_log[columns(daily_pick_log,pick_cols)], hide_index=True, use_container_width=True)
        if not daily_pick_summary.empty:
            s=daily_pick_summary.iloc[0]
            s1,s2,s3,s4,s5=st.columns(5)
            s1.metric("Daily picks", int(number(s.get("total_daily_picks"))))
            s2.metric("Closed", int(number(s.get("closed_picks"))))
            s3.metric("Target hits", int(number(s.get("target_hits"))))
            s4.metric("Stop hits", int(number(s.get("stop_hits"))))
            s5.metric("Win rate", f'{number(s.get("win_rate_pct")):.1f}%')
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
    st.subheader("V8.2 evidence maturity")
    s1,s2,s3,s4=st.columns(4)
    s1.metric("Evidence state", str(v82_scorecard.get("status", "COLLECTING")))
    s2.metric("Observations", int(number(v82_scorecard.get("observations"))))
    s3.metric("Mature samples", int(number(v82_scorecard.get("mature_samples"))))
    s4.metric("V9 progress", f'{number(v82_scorecard.get("progress_pct", {}).get("v9_220")):.1f}%')
    next_milestone = v82_scorecard.get("next_milestone", {})
    st.caption(
        f'Next milestone: {next_milestone.get("name", "MONITORING")} · '
        f'{int(number(next_milestone.get("remaining")))} mature samples remaining · '
        f'source: {v82_scorecard_source}'
    )
    if v82_scorecard.get("failed_checks"):
        st.error("Evidence scorecard failures: " + ", ".join(map(str, v82_scorecard["failed_checks"])))
    st.subheader("V9 readiness (manual review only)")
    r1,r2,r3=st.columns(3)
    r1.metric("Readiness state", str(v9_readiness.get("status", "BLOCKED_BY_V8_VALIDATION")))
    r2.metric("Mature evidence", int(number(v9_readiness.get("mature_evidence"))))
    r3.metric("Broker execution", "DISABLED" if not bool(v9_readiness.get("broker_execution_enabled", False)) else "ENABLED")
    if v9_readiness.get("failed_gates"):
        st.warning("V9 remains blocked by: " + ", ".join(map(str, v9_readiness["failed_gates"])))
    st.caption(f"No automatic activation; explicit manual approval is always required · source: {v9_readiness_source}")
    st.subheader("V9.1 bounded pilot rehearsal")
    p1,p2,p3=st.columns(3)
    p1.metric("Pilot state", str(v91_pilot.get("status", "BLOCKED_PAPER_REHEARSAL")))
    p2.metric("Review candidates", int(number(v91_pilot.get("review_candidates"))))
    p3.metric("Orders generated", int(number(v91_pilot.get("orders_generated"))))
    pilot_candidates = data["v91_pilot_candidates"]
    if not pilot_candidates.empty:
        st.dataframe(pilot_candidates, hide_index=True, use_container_width=True)
    st.caption(f"Paper-only, manual review, no broker integration · source: {v91_pilot_source}")
    tradable=data["tradable"]
    if not tradable.empty:
        passed=int(tradable["tradable"].sum()) if "tradable" in tradable else len(tradable)
        st.metric("Current tradability gate",f"{passed:,} symbols")
    st.warning("Research and decision support only. Data may be delayed or incomplete. Stops can gap and no setup guarantees a 5–10% move.")
