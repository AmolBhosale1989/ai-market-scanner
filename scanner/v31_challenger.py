from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math

import numpy as np
import pandas as pd

from .warehouse import history as warehouse_history
from .control_plane import append_state, read_dataset, read_state, write_dataset

ET=ZoneInfo("America/New_York")
LOG_COLUMNS=[
    "selection_date_et","selected_at_et","ticker","source","selection_score","theme",
    "entry_price","stop_price","target_price","target_pct","risk_pct","rr_to_target",
    "market_regime_state","market_hunt_score","live_confirmation_score","catalyst_score",
    "order_flow_score","order_flow_strategy_score","buy_pressure_pct","volume_imbalance_proxy",
    "theme_rotation_score","rel_vs_spy_pct","intraday_rvol","day_change_pct","reason",
    "status","outcome_at_et","exit_price","return_pct","r_multiple","max_price_seen","min_price_seen",
    "mfe_pct","mae_pct","business_days_open","target_hit","stop_hit","expired"
]

def _num(v, default=np.nan):
    try: return float(v)
    except (TypeError,ValueError): return default

def _read(name):
    return read_dataset(name,required=False)

def _now_et():
    return datetime.now(timezone.utc).astimezone(ET)

def _business_days_open(a,b):
    try: return max(0,int(np.busday_count(np.datetime64(a),np.datetime64(b))))
    except Exception: return 0

def _monitor_row(row, now_et):
    if str(row.get("status","OPEN"))!="OPEN":
        return row
    ticker=str(row.get("ticker",""))
    entry=_num(row.get("entry_price")); stop=_num(row.get("stop_price")); target=_num(row.get("target_price"))
    selected=pd.to_datetime(row.get("selected_at_et"),errors="coerce",utc=True)
    days=_business_days_open(str(row.get("selection_date_et")),now_et.date().isoformat())
    row["business_days_open"]=days
    if not np.isfinite(entry) or entry<=0:
        return row
    hist=warehouse_history(ticker,period="10d",interval="5m",max_age_minutes=10)
    if hist is None or hist.empty:
        return row
    if isinstance(hist.columns,pd.MultiIndex):
        hist.columns=hist.columns.get_level_values(0)
    idx=pd.DatetimeIndex(hist.index)
    if idx.tz is None: idx=idx.tz_localize("UTC")
    else: idx=idx.tz_convert("UTC")
    hist=hist.copy(); hist.index=idx
    if pd.notna(selected):
        hist=hist[hist.index>=selected]
    if hist.empty or not {"High","Low","Close"}.issubset(hist.columns):
        return row
    highs=pd.to_numeric(hist["High"],errors="coerce"); lows=pd.to_numeric(hist["Low"],errors="coerce")
    max_seen=float(highs.max()) if highs.notna().any() else entry
    min_seen=float(lows.min()) if lows.notna().any() else entry
    row["max_price_seen"]=round(max_seen,4); row["min_price_seen"]=round(min_seen,4)
    row["mfe_pct"]=round((max_seen/entry-1)*100,2); row["mae_pct"]=round((min_seen/entry-1)*100,2)
    target_hit=np.isfinite(target) and bool((highs>=target).any())
    stop_hit=np.isfinite(stop) and bool((lows<=stop).any())
    status="OPEN"; exit_price=np.nan; outcome=""
    if target_hit or stop_hit:
        tt=hist.index[highs>=target][0] if target_hit else None
        ts=hist.index[lows<=stop][0] if stop_hit else None
        if stop_hit and (not target_hit or ts<=tt):
            status="STOP_HIT"; exit_price=stop; outcome=str(ts)
        else:
            status="TARGET_HIT"; exit_price=target; outcome=str(tt)
    elif days>=7:
        status="EXPIRED"; exit_price=_num(pd.to_numeric(hist["Close"],errors="coerce").dropna().iloc[-1],entry); outcome=now_et.isoformat()
    if status!="OPEN":
        row["status"]=status; row["outcome_at_et"]=outcome; row["exit_price"]=round(exit_price,4)
        row["return_pct"]=round((exit_price/entry-1)*100,2)
        risk=entry-stop
        row["r_multiple"]=round((exit_price-entry)/risk,2) if np.isfinite(risk) and risk>0 else np.nan
        row["target_hit"]=status=="TARGET_HIT"; row["stop_hit"]=status=="STOP_HIT"; row["expired"]=status=="EXPIRED"
    return row

def _pick_candidate(momentum, order_flow, live):
    if momentum.empty or order_flow.empty or live.empty:
        return None,"MISSING_LIVE_INPUTS"
    m=momentum.copy(); o=order_flow.copy(); l=live.copy()
    for f in (m,o,l):
        if "ticker" in f.columns: f["ticker"]=f["ticker"].astype(str)
    m=m[m.get("signal",pd.Series("",index=m.index)).astype(str).eq("MOMENTUM BUY")].copy()
    if m.empty:
        return None,"NO_MOMENTUM_BUY"
    keep_o=[c for c in ["ticker","order_flow_strategy_signal","order_flow_strategy_score","order_flow_score","buy_pressure_pct","volume_imbalance_proxy","strategy_rr_to_t2"] if c in o.columns]
    keep_l=[c for c in ["ticker","market_regime_state","market_hunt_score","live_confirmation_score","catalyst_score","negative_catalyst_risk","effective_target","effective_rr","runway_to_next_resistance_pct","live_trade_action"] if c in l.columns]
    x=m.merge(o[keep_o].drop_duplicates("ticker"),on="ticker",how="left",suffixes=("","_of"))
    x=x.merge(l[keep_l].drop_duplicates("ticker"),on="ticker",how="left",suffixes=("","_live"))

    numeric=["price","stop","day_change_pct","rel_vs_spy_pct","intraday_rvol","theme_rotation_score",
             "order_flow_strategy_score","order_flow_score","buy_pressure_pct","volume_imbalance_proxy",
             "strategy_rr_to_t2","market_hunt_score","live_confirmation_score","catalyst_score",
             "effective_target","effective_rr","runway_to_next_resistance_pct"]
    for col in numeric:
        if col not in x.columns: x[col]=np.nan
        x[col]=pd.to_numeric(x[col],errors="coerce")

    neg=x.get("negative_catalyst_risk",pd.Series(False,index=x.index)).fillna(False).astype(bool)
    regime=x.get("market_regime_state",pd.Series("",index=x.index)).fillna("").astype(str)
    flow_signal=x.get("order_flow_strategy_signal",pd.Series("",index=x.index)).fillna("").astype(str)

    risk=((x["price"]-x["stop"])/x["price"]*100)
    target=x["effective_target"].where(x["effective_target"].gt(x["price"]),x["price"]*1.05)
    target=target.clip(upper=x["price"]*1.08)
    target_pct=(target/x["price"]-1)*100
    rr=(target-x["price"])/(x["price"]-x["stop"])

    gate=(
        regime.ne("WEAK")
        & ~neg
        & x["day_change_pct"].between(1.5,5.5,inclusive="both")
        & x["rel_vs_spy_pct"].ge(1.5)
        & x["intraday_rvol"].ge(1.5)
        & x["live_confirmation_score"].ge(70)
        & x["catalyst_score"].ge(30)
        & x["order_flow_score"].ge(70)
        & x["buy_pressure_pct"].ge(60)
        & x["volume_imbalance_proxy"].ge(5)
        & (flow_signal.eq("ORDER FLOW BUY") | x["order_flow_strategy_score"].ge(72))
        & risk.between(0.8,3.0,inclusive="both")
        & target_pct.ge(4.0)
        & rr.ge(2.0)
        & x["runway_to_next_resistance_pct"].fillna(0).ge(4.0)
    )
    q=x[gate].copy()
    if q.empty:
        return None,"NO_CANDIDATE_PASSED_ALL_V31_GATES"

    q["risk_pct_calc"]=risk[gate]
    q["target_calc"]=target[gate]
    q["target_pct_calc"]=target_pct[gate]
    q["rr_calc"]=rr[gate]
    q["selection_score"]=(
        q["order_flow_score"]*0.22
        + q["buy_pressure_pct"]*0.10
        + q["live_confirmation_score"]*0.18
        + q["catalyst_score"]*0.12
        + q["theme_rotation_score"].clip(0,100)*0.12
        + q["rel_vs_spy_pct"].clip(0,8)*2.0
        + q["intraday_rvol"].clip(0,4)*4.0
    ).clip(0,100)
    q=q.sort_values(["selection_score","order_flow_score","rel_vs_spy_pct"],ascending=[False,False,False])
    r=q.iloc[0]
    return {
        "ticker":str(r["ticker"]),"source":"V3.1_CHALLENGER","selection_score":round(_num(r["selection_score"]),2),
        "theme":str(r.get("theme","")),"entry_price":_num(r["price"]),"stop_price":_num(r["stop"]),
        "target_price":_num(r["target_calc"]),"target_pct":_num(r["target_pct_calc"]),"risk_pct":_num(r["risk_pct_calc"]),
        "rr_to_target":_num(r["rr_calc"]),"market_regime_state":str(r.get("market_regime_state","")),
        "market_hunt_score":_num(r.get("market_hunt_score")),"live_confirmation_score":_num(r.get("live_confirmation_score")),
        "catalyst_score":_num(r.get("catalyst_score")),"order_flow_score":_num(r.get("order_flow_score")),
        "order_flow_strategy_score":_num(r.get("order_flow_strategy_score")),"buy_pressure_pct":_num(r.get("buy_pressure_pct")),
        "volume_imbalance_proxy":_num(r.get("volume_imbalance_proxy")),"theme_rotation_score":_num(r.get("theme_rotation_score")),
        "rel_vs_spy_pct":_num(r.get("rel_vs_spy_pct")),"intraday_rvol":_num(r.get("intraday_rvol")),
        "day_change_pct":_num(r.get("day_change_pct")),
        "reason":"V3.1: regime + early momentum + catalyst + live confirmation + order flow + runway + RR"
    },"SELECTED"

def run():
    now=_now_et(); today=now.date().isoformat()
    log_payload=read_state("v31","challenger_log",default=[])
    log=pd.DataFrame(log_payload) if log_payload else pd.DataFrame()
    if log.empty: log=pd.DataFrame(columns=LOG_COLUMNS)
    if not log.empty:
        log=pd.DataFrame([_monitor_row(r.copy(),now) for _,r in log.iterrows()])

    decision_payload=read_state("v31","challenger_decisions",default=[])
    decisions=pd.DataFrame(decision_payload) if decision_payload else pd.DataFrame()
    already=not decisions.empty and "selection_date_et" in decisions.columns and decisions["selection_date_et"].astype(str).eq(today).any()
    within=(now.hour,now.minute)>=(9,45) and (now.hour,now.minute)<=(14,30)
    if not already and now.weekday()<5 and within:
        candidate,decision=_pick_candidate(_read("momentum_signals"),_read("order_flow_strategy"),_read("intraday_live"))
        drow={"selection_date_et":today,"decision_at_et":now.isoformat(),"decision":decision,"ticker":candidate["ticker"] if candidate else "","selection_score":candidate["selection_score"] if candidate else np.nan}
        decisions=pd.concat([decisions,pd.DataFrame([drow])],ignore_index=True)
        if candidate:
            row={**candidate,"selection_date_et":today,"selected_at_et":now.isoformat(),"status":"OPEN",
                 "outcome_at_et":"","exit_price":np.nan,"return_pct":np.nan,"r_multiple":np.nan,
                 "max_price_seen":candidate["entry_price"],"min_price_seen":candidate["entry_price"],
                 "mfe_pct":0.0,"mae_pct":0.0,"business_days_open":0,"target_hit":False,"stop_hit":False,"expired":False}
            log=pd.concat([log,pd.DataFrame([row])],ignore_index=True)

    for col in LOG_COLUMNS:
        if col not in log.columns: log[col]=np.nan
    log=log[LOG_COLUMNS]
    append_state("v31","challenger_log",log.to_dict("records"))
    append_state("v31","challenger_decisions",decisions.to_dict("records"))
    write_dataset("v31_challenger_log",log)
    write_dataset("v31_challenger_decisions",decisions,entity_key=None)

    closed=log[log["status"].astype(str).isin(["TARGET_HIT","STOP_HIT","EXPIRED"])] if not log.empty else pd.DataFrame()
    summary=pd.DataFrame([{
        "strategy":"V3.1_CHALLENGER","total_trade_selections":len(log),
        "no_trade_days":int(decisions["decision"].astype(str).ne("SELECTED").sum()) if not decisions.empty and "decision" in decisions else 0,
        "open_trades":int(log["status"].astype(str).eq("OPEN").sum()) if not log.empty else 0,
        "closed_trades":len(closed),
        "target_hits":int(closed["status"].astype(str).eq("TARGET_HIT").sum()) if not closed.empty else 0,
        "stop_hits":int(closed["status"].astype(str).eq("STOP_HIT").sum()) if not closed.empty else 0,
        "win_rate_pct":round(float(closed["status"].astype(str).eq("TARGET_HIT").mean()*100),1) if len(closed) else np.nan,
        "avg_return_pct":round(float(pd.to_numeric(closed["return_pct"],errors="coerce").mean()),2) if len(closed) else np.nan,
        "avg_r_multiple":round(float(pd.to_numeric(closed["r_multiple"],errors="coerce").mean()),2) if len(closed) else np.nan,
        "updated_at_et":now.isoformat(),
        "mode":"SHADOW_FORWARD_ONLY_NO_PRODUCTION_OVERRIDE"
    }])
    write_dataset("v31_challenger_summary",summary,entity_key=None)
    return log

if __name__=="__main__":
    out=run()
    print(out.tail(10).to_string(index=False) if not out.empty else "No V3.1 challenger trade selected.")
