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

NY = ZoneInfo("America/New_York")
STATE_DIR = Path(".state")
STATE_FILE = STATE_DIR / "market_hunt_state.json"
TRANSITIONS_FILE = STATE_DIR / "state_transitions.csv"
ALERT_STATES = {"TRIGGERED", "LIVE_CONFIRMED", "FAILED_BREAKOUT", "INVALIDATED"}

def _truthy(v):
    if isinstance(v,bool):
        return v
    return str(v).strip().lower() in {"1","true","yes","y"}

def _num(v):
    x=pd.to_numeric(pd.Series([v]),errors="coerce").iloc[0]
    return float(x) if pd.notna(x) else math.nan

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
    negative=_truthy(row.get("negative_catalyst_risk",False))
    trigger=_truthy(row.get("live_trigger_reached",False))
    above_vwap=_truthy(row.get("live_above_vwap",False))
    rvol=_num(row.get("intraday_rvol"))
    action=str(row.get("live_trade_action",""))

    if negative or (math.isfinite(price) and math.isfinite(stop) and price<=stop):
        return "INVALIDATED"
    if action.startswith("BUY / LIVE CONFIRMED"):
        return "LIVE_CONFIRMED"
    if previous in {"TRIGGERED","LIVE_CONFIRMED"}:
        if (not trigger) or (not above_vwap) or (math.isfinite(rvol) and rvol<0.8):
            return "FAILED_BREAKOUT"
    if trigger:
        return "TRIGGERED"
    return "ARMED"

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
        return pd.DataFrame()

    base=pd.read_csv(source)
    watch=base[base["stage"].isin(["ARMED","CONFIRMED"])].copy()
    if watch.empty:
        print("No ARMED/CONFIRMED candidates to monitor.")
        return watch

    sort_col="market_hunt_score" if "market_hunt_score" in watch.columns else "final_score"
    watch=watch.sort_values(sort_col,ascending=False).head(limit).copy()
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
        live.at[idx,"state_changed"]=bool(old and old!=new)
        live.at[idx,"checked_at_et"]=now

        state_out[ticker]={
            "state":new,
            "checked_at_et":now,
            "session_date":str(row.get("live_session_date","")),
            "live_price":None if pd.isna(row.get("live_price")) else float(row.get("live_price")),
            "entry_trigger":None if pd.isna(row.get("entry_trigger")) else float(row.get("entry_trigger")),
            "stop":None if pd.isna(row.get("stop")) else float(row.get("stop")),
        }

        if old and old!=new:
            event={
                "checked_at_et":now,
                "ticker":ticker,
                "from_state":old,
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
                    f"{ticker}: {old} -> {new}\n"
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
    alert_text="\n\n".join(alerts)
    (OUTPUT_DIR/"live_alerts.txt").write_text(alert_text)
    sent=_send_telegram(alerts)

    print("\nINTRADAY MARKET HUNT MONITOR")
    cols=["ticker","stage","previous_state","monitor_state","live_price","entry_trigger",
          "live_vwap","opening_range_high","intraday_rvol","live_confirmation_score","live_trade_action"]
    print(live[[c for c in cols if c in live.columns]].to_string(index=False))
    print(f"State transitions this run: {len(transitions)}")
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
