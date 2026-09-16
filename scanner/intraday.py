from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .config import OUTPUT_DIR, LIVE_ENRICH_LIMIT
from .live import enrich_live_candidates
from .performance import build_performance_reports, build_empirical_calibration
from .product_feed import build_product_feed

NY = ZoneInfo("America/New_York")
STATE_DIR = Path(".state")
STATE_FILE = STATE_DIR / "market_hunt_state.json"
TRANSITIONS_FILE = STATE_DIR / "state_transitions.csv"
JOURNAL_FILE = STATE_DIR / "paper_journal.csv"
ALERT_STATES = {"TRIGGERED", "LIVE_CONFIRMED", "FAILED_BREAKOUT", "INVALIDATED", "TARGET_HIT"}

def _truthy(v):
    if isinstance(v,bool):
        return v
    return str(v).strip().lower() in {"1","true","yes","y"}

def _num(v):
    x=pd.to_numeric(pd.Series([v]),errors="coerce").iloc[0]
    return float(x) if pd.notna(x) else math.nan

def _write_recommendations(live: pd.DataFrame):
    if live is None or live.empty:
        out=pd.DataFrame(columns=[
            "ticker","company_name","stage","live_price","entry_trigger","stop",
            "effective_target","effective_rr","catalyst_status","catalyst_score",
            "intraday_rvol","bid_ask_spread_pct","live_trade_action","monitor_state",
        ])
    else:
        action=live.get("live_trade_action",pd.Series("",index=live.index)).astype(str)
        state=live.get("monitor_state",pd.Series("",index=live.index)).astype(str)
        out=live[action.str.startswith("BUY / LIVE CONFIRMED") & state.eq("LIVE_CONFIRMED")].copy()
        sort_col="market_hunt_score" if "market_hunt_score" in out.columns else None
        if sort_col:
            out=out.sort_values(sort_col,ascending=False)
    out.to_csv(OUTPUT_DIR/"recommended_trades.csv",index=False)
    return out


def _load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}

def _derive_state(row, previous):
    if str(row.get("live_status",""))!="LIVE":
        return previous or ("ARMED" if str(row.get("stage","")) in {"ARMED","CONFIRMED"} else str(row.get("stage","WATCH")))

    price=_num(row.get("live_price"))
    stop=_num(row.get("stop"))
    target=_num(row.get("effective_target"))
    negative=_truthy(row.get("negative_catalyst_risk",False))
    trigger=_truthy(row.get("live_trigger_reached",False))
    above_vwap=_truthy(row.get("live_above_vwap",False))
    rvol=_num(row.get("intraday_rvol"))
    action=str(row.get("live_trade_action",""))

    if negative or (math.isfinite(price) and math.isfinite(stop) and price<=stop):
        return "INVALIDATED"
    if previous in {"TRIGGERED","LIVE_CONFIRMED"} and math.isfinite(price) and math.isfinite(target) and price>=target:
        return "TARGET_HIT"
    if action.startswith("BUY / LIVE CONFIRMED"):
        return "LIVE_CONFIRMED"
    if previous in {"TRIGGERED","LIVE_CONFIRMED"}:
        if (not trigger) or (not above_vwap) or (math.isfinite(rvol) and rvol<0.8):
            return "FAILED_BREAKOUT"
    if trigger:
        return "TRIGGERED"
    return "ARMED"

def _signal_id(row):
    ticker=str(row.get("ticker",""))
    entry=_num(row.get("entry_trigger"))
    stop=_num(row.get("stop"))
    return f"{ticker}|{entry:.4f}|{stop:.4f}" if math.isfinite(entry) and math.isfinite(stop) else ticker

def _update_paper_journal(live: pd.DataFrame, now: str):
    cols=[
        "signal_id","ticker","first_seen_et","last_seen_et","stage","entry_trigger","stop",
        "effective_target","effective_rr","entry_model","entry_condition","market_regime_state",
        "theme","theme_score","catalyst_status","catalyst_score","technical_score","formation_score",
        "final_score","market_hunt_score","live_confirmation_score","pattern","paper_state","last_price",
        "triggered_at_et","live_confirmed_at_et","closed_at_et","outcome","return_pct","r_multiple",
        "max_price_seen","min_price_seen","mfe_pct","mae_pct","hit_5pct","hit_8pct","hit_10pct"
    ]
    if JOURNAL_FILE.exists():
        try:
            journal=pd.read_csv(JOURNAL_FILE)
        except Exception:
            journal=pd.DataFrame(columns=cols)
    else:
        journal=pd.DataFrame(columns=cols)

    for _,row in live.iterrows():
        sid=_signal_id(row)
        ticker=str(row.get("ticker",""))
        new_state=str(row.get("monitor_state",""))
        price=_num(row.get("live_price"))
        entry=_num(row.get("entry_trigger"))
        stop=_num(row.get("stop"))
        target=_num(row.get("effective_target"))
        rr=_num(row.get("effective_rr"))

        mask=journal["signal_id"].astype(str).eq(sid) if "signal_id" in journal.columns else pd.Series(False,index=journal.index)
        if mask.any():
            idx=journal[mask].index[-1]
        else:
            new_row={
                "signal_id":sid,"ticker":ticker,"first_seen_et":now,"last_seen_et":now,
                "stage":row.get("stage",""),"entry_trigger":entry,"stop":stop,
                "effective_target":target,"effective_rr":rr,"entry_model":row.get("entry_model",""),
                "entry_condition":row.get("entry_condition",""),"market_regime_state":row.get("market_regime_state",""),
                "theme":row.get("theme",""),"theme_score":row.get("theme_score",math.nan),
                "catalyst_status":row.get("catalyst_status",""),"catalyst_score":row.get("catalyst_score",math.nan),
                "technical_score":row.get("technical_score",math.nan),"formation_score":row.get("formation_score",math.nan),
                "final_score":row.get("final_score",math.nan),"market_hunt_score":row.get("market_hunt_score",math.nan),
                "live_confirmation_score":row.get("live_confirmation_score",math.nan),"pattern":row.get("pattern",""),
                "paper_state":new_state,"last_price":price,"triggered_at_et":"",
                "live_confirmed_at_et":"","closed_at_et":"","outcome":"","return_pct":math.nan,"r_multiple":math.nan,
                "max_price_seen":math.nan,"min_price_seen":math.nan,"mfe_pct":math.nan,"mae_pct":math.nan,
                "hit_5pct":False,"hit_8pct":False,"hit_10pct":False,
            }
            journal=pd.concat([journal,pd.DataFrame([new_row])],ignore_index=True)
            idx=journal.index[-1]

        journal.at[idx,"last_seen_et"]=now
        journal.at[idx,"stage"]=row.get("stage","")
        journal.at[idx,"paper_state"]=new_state
        journal.at[idx,"last_price"]=price
        journal.at[idx,"market_regime_state"]=row.get("market_regime_state","")
        journal.at[idx,"theme"]=row.get("theme","")
        journal.at[idx,"theme_score"]=row.get("theme_score",journal.at[idx,"theme_score"] if "theme_score" in journal.columns else math.nan)
        journal.at[idx,"catalyst_status"]=row.get("catalyst_status","")
        journal.at[idx,"catalyst_score"]=row.get("catalyst_score",journal.at[idx,"catalyst_score"] if "catalyst_score" in journal.columns else math.nan)
        journal.at[idx,"technical_score"]=row.get("technical_score",journal.at[idx,"technical_score"] if "technical_score" in journal.columns else math.nan)
        journal.at[idx,"formation_score"]=row.get("formation_score",journal.at[idx,"formation_score"] if "formation_score" in journal.columns else math.nan)
        journal.at[idx,"final_score"]=row.get("final_score",journal.at[idx,"final_score"] if "final_score" in journal.columns else math.nan)
        journal.at[idx,"market_hunt_score"]=row.get("market_hunt_score",journal.at[idx,"market_hunt_score"] if "market_hunt_score" in journal.columns else math.nan)
        journal.at[idx,"live_confirmation_score"]=row.get("live_confirmation_score",journal.at[idx,"live_confirmation_score"] if "live_confirmation_score" in journal.columns else math.nan)
        journal.at[idx,"pattern"]=row.get("pattern",journal.at[idx,"pattern"] if "pattern" in journal.columns else "")

        session_high=_num(row.get("session_high"))
        session_low=_num(row.get("session_low"))
        prior_max=_num(journal.at[idx,"max_price_seen"]) if "max_price_seen" in journal.columns else math.nan
        prior_min=_num(journal.at[idx,"min_price_seen"]) if "min_price_seen" in journal.columns else math.nan
        if math.isfinite(session_high):
            max_seen=max(session_high,prior_max) if math.isfinite(prior_max) else session_high
            journal.at[idx,"max_price_seen"]=round(max_seen,2)
        else:
            max_seen=prior_max
        if math.isfinite(session_low):
            min_seen=min(session_low,prior_min) if math.isfinite(prior_min) else session_low
            journal.at[idx,"min_price_seen"]=round(min_seen,2)
        else:
            min_seen=prior_min

        if math.isfinite(entry) and entry>0:
            if math.isfinite(max_seen):
                mfe=(max_seen/entry-1)*100
                journal.at[idx,"mfe_pct"]=round(mfe,2)
                journal.at[idx,"hit_5pct"]=bool(_truthy(journal.at[idx,"hit_5pct"]) or mfe>=5)
                journal.at[idx,"hit_8pct"]=bool(_truthy(journal.at[idx,"hit_8pct"]) or mfe>=8)
                journal.at[idx,"hit_10pct"]=bool(_truthy(journal.at[idx,"hit_10pct"]) or mfe>=10)
            if math.isfinite(min_seen):
                journal.at[idx,"mae_pct"]=round((min_seen/entry-1)*100,2)

        triggered_existing=journal.at[idx,"triggered_at_et"]
        confirmed_existing=journal.at[idx,"live_confirmed_at_et"]
        closed_existing=journal.at[idx,"closed_at_et"]
        if new_state=="TRIGGERED" and (pd.isna(triggered_existing) or not str(triggered_existing).strip()):
            journal.at[idx,"triggered_at_et"]=now
        if new_state=="LIVE_CONFIRMED" and (pd.isna(confirmed_existing) or not str(confirmed_existing).strip()):
            journal.at[idx,"live_confirmed_at_et"]=now
        ever_triggered = not (pd.isna(journal.at[idx,"triggered_at_et"]) or not str(journal.at[idx,"triggered_at_et"]).strip())
        ever_confirmed = not (pd.isna(journal.at[idx,"live_confirmed_at_et"]) or not str(journal.at[idx,"live_confirmed_at_et"]).strip())
        entered_trade = bool(ever_triggered or ever_confirmed or new_state in {"TARGET_HIT","FAILED_BREAKOUT"})

        if new_state in {"TARGET_HIT","FAILED_BREAKOUT","INVALIDATED"}:
            if pd.isna(closed_existing) or not str(closed_existing).strip():
                journal.at[idx,"closed_at_et"]=now

            if new_state=="INVALIDATED" and not entered_trade:
                journal.at[idx,"outcome"]="PRE_ENTRY_INVALIDATED"
                journal.at[idx,"return_pct"]=math.nan
                journal.at[idx,"r_multiple"]=math.nan
            else:
                journal.at[idx,"outcome"]=new_state
                if math.isfinite(price) and math.isfinite(entry) and entry>0:
                    journal.at[idx,"return_pct"]=round((price/entry-1)*100,2)
                    risk=entry-stop if math.isfinite(stop) else math.nan
                    if math.isfinite(risk) and risk>0:
                        journal.at[idx,"r_multiple"]=round((price-entry)/risk,2)

    STATE_DIR.mkdir(exist_ok=True)
    journal.to_csv(JOURNAL_FILE,index=False)
    journal.to_csv(OUTPUT_DIR/"paper_journal.csv",index=False)
    return journal

def _write_monitor_health(live: pd.DataFrame, now: str, alert_count: int, telegram_configured: bool, telegram_sent: bool):
    rows=len(live) if live is not None else 0
    live_rows=int(live.get("live_status",pd.Series(dtype=object)).eq("LIVE").sum()) if rows else 0
    actionable=int(live.get("monitor_state",pd.Series(dtype=object)).isin(ALERT_STATES).sum()) if rows else 0
    status="OK" if rows>0 else "NO_ACTIVE_CANDIDATES"
    pd.DataFrame([{
        "status":status,
        "checked_at_et":now,
        "monitored_candidates":rows,
        "live_rows":live_rows,
        "actionable_states":actionable,
        "alerts_generated":int(alert_count),
        "telegram_configured":bool(telegram_configured),
        "telegram_sent":bool(telegram_sent),
    }]).to_csv(OUTPUT_DIR/"monitor_health.csv",index=False)


def _send_telegram(messages):
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat or not messages:
        return False
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id":chat,"text":"\n\n".join(messages)},
            timeout=15,
        )
        return r.ok
    except Exception:
        return False

def run(input_file=None, limit=LIVE_ENRICH_LIMIT):
    source=Path(input_file) if input_file else OUTPUT_DIR/"latest_scan.csv"
    if not source.exists():
        print(f"No base shortlist found at {source}. Nothing to monitor.")
        now=datetime.now(NY).isoformat(timespec="seconds")
        build_performance_reports()
        build_empirical_calibration()
        _write_monitor_health(pd.DataFrame(),now,0,bool(os.getenv("TELEGRAM_BOT_TOKEN","").strip() and os.getenv("TELEGRAM_CHAT_ID","").strip()),False)
        _write_recommendations(pd.DataFrame())
        build_product_feed()
        return pd.DataFrame()

    base=pd.read_csv(source)
    # Always monitor the strongest current candidates, not only ARMED/CONFIRMED.
    # BUY permission remains restricted downstream to fully qualified setups,
    # but FORMING/DISCOVER names must still receive live VWAP/ORB/RVOL updates.
    watch=base[base["stage"].isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])].copy()
    if watch.empty:
        print("No active candidates to monitor.")
        now=datetime.now(NY).isoformat(timespec="seconds")
        empty=pd.DataFrame()
        empty.to_csv(OUTPUT_DIR/"intraday_live.csv",index=False)
        build_performance_reports()
        build_empirical_calibration()
        _write_monitor_health(empty,now,0,bool(os.getenv("TELEGRAM_BOT_TOKEN","").strip() and os.getenv("TELEGRAM_CHAT_ID","").strip()),False)
        _write_recommendations(empty)
        build_product_feed()
        return empty

    sort_col="market_hunt_score" if "market_hunt_score" in watch.columns else "final_score"
    watch["live_stage_priority"]=watch["stage"].map({"CONFIRMED":4,"ARMED":3,"FORMING":2,"DISCOVER":1}).fillna(0)
    watch=watch.sort_values(["live_stage_priority",sort_col],ascending=[False,False]).head(limit).copy()
    live=enrich_live_candidates(watch,limit=limit)

    prior=_load_state()
    now=datetime.now(NY).isoformat(timespec="seconds")
    state_out=dict(prior)
    transitions=[]
    alerts=[]

    for idx,row in live.iterrows():
        ticker=str(row["ticker"])
        old=str(prior.get(ticker,{}).get("state",""))
        new=_derive_state(row,old)
        live.at[idx,"previous_state"]=old or "NEW"
        live.at[idx,"monitor_state"]=new
        live.at[idx,"state_changed"]=bool(old!=new)
        live.at[idx,"checked_at_et"]=now

        state_out[ticker]={
            "state":new,
            "checked_at_et":now,
            "session_date":str(row.get("live_session_date","")),
            "live_price":None if pd.isna(row.get("live_price")) else float(row.get("live_price")),
            "entry_trigger":None if pd.isna(row.get("entry_trigger")) else float(row.get("entry_trigger")),
            "stop":None if pd.isna(row.get("stop")) else float(row.get("stop")),
        }

        if old!=new and (old or new in ALERT_STATES):
            event={
                "checked_at_et":now,
                "ticker":ticker,
                "from_state":old or "NEW",
                "to_state":new,
                "live_price":row.get("live_price"),
                "entry_trigger":row.get("entry_trigger"),
                "stop":row.get("stop"),
                "intraday_rvol":row.get("intraday_rvol"),
                "live_vwap":row.get("live_vwap"),
                "opening_range_high":row.get("opening_range_high"),
                "theme":row.get("theme",""),
                "catalyst_status":row.get("catalyst_status",""),
            }
            transitions.append(event)
            if new in ALERT_STATES:
                alerts.append(
                    f"{ticker}: {old or 'NEW'} -> {new}\n"
                    f"Price {row.get('live_price')} | Trigger {row.get('entry_trigger')} | "
                    f"Stop {row.get('stop')} | RVOL {row.get('intraday_rvol')} | "
                    f"Theme {row.get('theme','')}"
                )

    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state_out,indent=2,sort_keys=True))

    if transitions:
        tdf=pd.DataFrame(transitions)
        if TRANSITIONS_FILE.exists():
            tdf=pd.concat([pd.read_csv(TRANSITIONS_FILE),tdf],ignore_index=True)
        tdf.to_csv(TRANSITIONS_FILE,index=False)
        tdf.to_csv(OUTPUT_DIR/"state_transitions.csv",index=False)
    elif TRANSITIONS_FILE.exists():
        pd.read_csv(TRANSITIONS_FILE).to_csv(OUTPUT_DIR/"state_transitions.csv",index=False)

    live.to_csv(OUTPUT_DIR/"intraday_live.csv",index=False)
    recommendations=_write_recommendations(live)
    journal=_update_paper_journal(live,now)
    build_performance_reports(journal)
    build_empirical_calibration(journal)
    alert_text="\n\n".join(alerts)
    (OUTPUT_DIR/"live_alerts.txt").write_text(alert_text)
    telegram_configured=bool(os.getenv("TELEGRAM_BOT_TOKEN","").strip() and os.getenv("TELEGRAM_CHAT_ID","").strip())
    sent=_send_telegram(alerts)
    _write_monitor_health(live,now,len(alerts),telegram_configured,sent)
    build_product_feed()

    print("\nINTRADAY MARKET HUNT MONITOR")
    cols=["ticker","stage","previous_state","monitor_state","live_price","entry_trigger",
          "live_vwap","opening_range_high","intraday_rvol","live_confirmation_score","live_trade_action"]
    print(live[[c for c in cols if c in live.columns]].to_string(index=False))
    print(f"State transitions this run: {len(transitions)}")
    print(f"Live-confirmed recommendations: {len(recommendations)}")
    if alerts:
        print("\nALERTS")
        print(alert_text)
        print(f"Telegram sent: {sent}")
    return live

if __name__=="__main__":
    p=argparse.ArgumentParser(description="Market Hunt intraday state monitor")
    p.add_argument("--input",default=None)
    p.add_argument("--limit",type=int,default=LIVE_ENRICH_LIMIT)
    args=p.parse_args()
    run(input_file=args.input,limit=args.limit)
