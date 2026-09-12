from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
import pandas as pd
import yfinance as yf

from .config import EVENT_SCAN_LIMIT, EVENT_LOOKAHEAD_DAYS, EVENT_MAX_WORKERS, OUTPUT_DIR

def _to_utc(value):
    try:
        ts=pd.Timestamp(value)
        if pd.isna(ts):
            return None
        ts=ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts
    except Exception:
        return None

def _calendar_earnings(ticker: str):
    obj=yf.Ticker(ticker)
    now=pd.Timestamp.now(tz="UTC")
    dates=[]

    try:
        cal=obj.calendar
        if isinstance(cal,dict):
            raw=cal.get("Earnings Date") or cal.get("EarningsDate")
            if raw is not None:
                raw=raw if isinstance(raw,(list,tuple)) else [raw]
                for x in raw:
                    ts=_to_utc(x)
                    if ts is not None:
                        dates.append(ts)
    except Exception:
        pass

    # Always query the earnings-date history/calendar as a second source.
    # Yahoo's compact calendar can contain stale/past dates, so treating any
    # calendar response as authoritative can incorrectly suppress future events.
    try:
        ed=obj.get_earnings_dates(limit=12)
        if isinstance(ed,pd.DataFrame) and len(ed):
            for idx in ed.index:
                ts=_to_utc(idx)
                if ts is not None:
                    dates.append(ts)
    except Exception:
        pass

    # De-duplicate after combining both Yahoo sources.
    unique={}
    for ts in dates:
        unique[ts.isoformat()]=ts
    dates=list(unique.values())

    future=[]
    for ts in dates:
        delta=(ts-now).total_seconds()/86400
        if -0.5<=delta<=EVENT_LOOKAHEAD_DAYS:
            future.append((delta,ts))

    if not future:
        return None

    delta,ts=min(future,key=lambda x:x[0])
    if delta<=3:
        priority="HIGH"
    elif delta<=5:
        priority="MEDIUM"
    else:
        priority="WATCH"

    return {
        "ticker":ticker,
        "event_type":"EARNINGS",
        "event_date_utc":ts.isoformat(),
        "days_to_event":round(delta,2),
        "event_priority":priority,
        "event_source":"Yahoo Finance",
    }

def build_event_watchlist(tradable_df: pd.DataFrame):
    if tradable_df is None or tradable_df.empty:
        out=pd.DataFrame()
        out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
        return out

    u=tradable_df.copy()
    if "tradable" in u.columns:
        u=u[u["tradable"].astype(bool)]
    if "avg_dollar_volume20" in u.columns:
        u=u.sort_values("avg_dollar_volume20",ascending=False)

    u=u.head(EVENT_SCAN_LIMIT).copy()
    name_map=dict(zip(u["ticker"].astype(str),u.get("name",pd.Series([""]*len(u))).astype(str)))
    adv_map=dict(zip(u["ticker"].astype(str),pd.to_numeric(u.get("avg_dollar_volume20",0),errors="coerce").fillna(0)))

    rows=[]
    with ThreadPoolExecutor(max_workers=EVENT_MAX_WORKERS) as ex:
        futures={ex.submit(_calendar_earnings,str(t)):str(t) for t in u["ticker"].astype(str)}
        for fut in as_completed(futures):
            ticker=futures[fut]
            try:
                row=fut.result()
                if row:
                    row["company_name"]=name_map.get(ticker,"")
                    row["avg_dollar_volume20"]=round(float(adv_map.get(ticker,0)),0)
                    rows.append(row)
            except Exception:
                continue

    out=pd.DataFrame(rows)
    if not out.empty:
        order={"HIGH":3,"MEDIUM":2,"WATCH":1}
        out["_priority"]=out["event_priority"].map(order).fillna(0)
        out=out.sort_values(["_priority","days_to_event","avg_dollar_volume20"],ascending=[False,True,False])
        out=out.drop(columns=["_priority"]).reset_index(drop=True)
    out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
    return out

def merge_technical_context(events: pd.DataFrame, candidates: pd.DataFrame):
    if events is None or events.empty:
        return events
    out=events.copy()
    if candidates is None or candidates.empty:
        return out

    cols=[
        "ticker","price","stage","technical_stage","pattern","technical_score","risk_score",
        "entry_trigger","entry_model","stop","effective_target","effective_rr",
        "runway_to_next_resistance_pct","market_regime_state","market_regime_score",
    ]
    keep=[c for c in cols if c in candidates.columns]
    if "ticker" not in keep:
        return out
    tech=candidates[keep].drop_duplicates("ticker")
    out=out.merge(tech,on="ticker",how="left")
    out["pre_event_setup_state"]=out.get("stage",pd.Series(index=out.index,dtype=object)).fillna("NO TECHNICAL SETUP")
    out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
    return out
