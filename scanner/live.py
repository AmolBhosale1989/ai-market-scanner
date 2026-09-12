from __future__ import annotations

from datetime import datetime, time as dtime
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from .config import (
    LIVE_ENRICH_LIMIT,
    LIVE_INTERVAL,
    LIVE_PERIOD,
    LIVE_MIN_INTRADAY_RVOL,
    OPENING_RANGE_MINUTES,
    MIN_RUNWAY_PCT,
    MIN_EFFECTIVE_RR,
)

NY = ZoneInfo("America/New_York")

def _empty_live(status="NOT CHECKED"):
    return {
        "live_status": status,
        "live_session_date": "",
        "live_price": math.nan,
        "live_vwap": math.nan,
        "live_above_vwap": False,
        "opening_range_high": math.nan,
        "opening_range_low": math.nan,
        "live_above_or_high": False,
        "intraday_rvol": math.nan,
        "live_trigger_reached": False,
        "live_confirmation_score": 0,
        "live_trade_action": "NO LIVE SIGNAL",
    }

def _normalize_intraday(df: pd.DataFrame):
    if df is None or df.empty:
        return pd.DataFrame()
    d=df.copy()
    if isinstance(d.columns,pd.MultiIndex):
        d.columns=[c[0] for c in d.columns]
    needed={"Open","High","Low","Close","Volume"}
    if not needed.issubset(d.columns):
        return pd.DataFrame()

    idx=pd.DatetimeIndex(d.index)
    if idx.tz is None:
        idx=idx.tz_localize("UTC")
    idx=idx.tz_convert(NY)
    d.index=idx
    d=d.dropna(subset=["Close"]).sort_index()
    return d

def _market_state(now_et: datetime):
    if now_et.weekday() >= 5:
        return "MARKET CLOSED"
    t=now_et.time()
    if t < dtime(9,30):
        return "PREMARKET"
    if t <= dtime(16,0):
        return "LIVE"
    return "MARKET CLOSED"

def _session_frame(d: pd.DataFrame, session_date):
    x=d[d.index.date==session_date].copy()
    if x.empty:
        return x
    return x.between_time("09:30","16:00")

def _opening_range(session: pd.DataFrame):
    if session.empty:
        return math.nan, math.nan, False
    start=session.index[0].replace(hour=9,minute=30,second=0,microsecond=0)
    end=start + pd.Timedelta(minutes=OPENING_RANGE_MINUTES)
    orb=session[(session.index>=start) & (session.index<end)]
    complete=session.index[-1] >= end
    if orb.empty:
        return math.nan, math.nan, complete
    return float(orb["High"].max()), float(orb["Low"].min()), complete

def _session_vwap(session: pd.DataFrame):
    if session.empty:
        return math.nan
    volume=pd.to_numeric(session["Volume"],errors="coerce").fillna(0)
    typical=(session["High"]+session["Low"]+session["Close"])/3
    denom=float(volume.sum())
    if denom<=0:
        return math.nan
    return float((typical*volume).sum()/denom)

def _intraday_rvol(d: pd.DataFrame, session_date, current_session: pd.DataFrame):
    if current_session.empty:
        return math.nan

    bar_count=len(current_session)
    current_volume=float(pd.to_numeric(current_session["Volume"],errors="coerce").fillna(0).sum())
    if current_volume<=0:
        return math.nan

    prior_dates=sorted({x for x in d.index.date if x < session_date}, reverse=True)[:3]
    comps=[]
    for dt in prior_dates:
        s=_session_frame(d,dt)
        if s.empty:
            continue
        s=s.iloc[:bar_count]
        if s.empty:
            continue
        vol=float(pd.to_numeric(s["Volume"],errors="coerce").fillna(0).sum())
        if vol>0:
            comps.append(vol)

    if not comps:
        return math.nan
    baseline=float(np.mean(comps))
    return current_volume/baseline if baseline>0 else math.nan

def analyze_live_candidate(ticker: str, entry_trigger: float, stage: str, catalyst_score: float,
                           rr_to_8pct: float, runway_pct: float, negative_catalyst_risk: bool):
    result=_empty_live()
    now_et=datetime.now(NY)
    state=_market_state(now_et)

    try:
        raw=yf.download(
            ticker,
            period=LIVE_PERIOD,
            interval=LIVE_INTERVAL,
            auto_adjust=True,
            progress=False,
            threads=False,
            prepost=False,
            timeout=20,
        )
    except TypeError:
        raw=yf.download(
            ticker,
            period=LIVE_PERIOD,
            interval=LIVE_INTERVAL,
            auto_adjust=True,
            progress=False,
            threads=False,
            prepost=False,
        )
    except Exception:
        result["live_status"]="ERROR"
        return result

    d=_normalize_intraday(raw)
    if d.empty:
        result["live_status"]="NO INTRADAY DATA"
        return result

    latest_date=d.index[-1].date()
    latest_session=_session_frame(d,latest_date)
    if latest_session.empty:
        result["live_status"]="NO REGULAR SESSION DATA"
        return result

    is_current_session=(latest_date==now_et.date())
    if not is_current_session:
        status="STALE SESSION"
    elif state=="LIVE":
        status="LIVE"
    elif state=="PREMARKET":
        status="PREMARKET / PRIOR SESSION"
    else:
        status="MARKET CLOSED"

    price=float(latest_session["Close"].iloc[-1])
    vwap=_session_vwap(latest_session)
    or_high,or_low,or_complete=_opening_range(latest_session)
    rvol=_intraday_rvol(d,latest_date,latest_session)

    above_vwap=math.isfinite(vwap) and price>vwap
    above_or=or_complete and math.isfinite(or_high) and price>or_high
    trigger_reached=math.isfinite(entry_trigger) and price>=entry_trigger

    score=0
    if above_vwap:
        score+=25
    if above_or:
        score+=25
    if trigger_reached:
        score+=25
    if math.isfinite(rvol) and rvol>=LIVE_MIN_INTRADAY_RVOL:
        score+=25

    live_action="NO LIVE SIGNAL"
    technical_ok=stage in {"ARMED","CONFIRMED"}
    catalyst_bonus=(not negative_catalyst_risk) and catalyst_score>=30
    rr_ok=math.isfinite(rr_to_8pct) and rr_to_8pct>=MIN_EFFECTIVE_RR
    runway_ok=math.isfinite(runway_pct) and runway_pct>=MIN_RUNWAY_PCT
    live_conditions=above_vwap and above_or and trigger_reached and math.isfinite(rvol) and rvol>=LIVE_MIN_INTRADAY_RVOL

    if status!="LIVE":
        live_action="WAIT / MARKET NOT LIVE"
    elif not or_complete:
        live_action="WAIT / OPENING RANGE FORMING"
    elif negative_catalyst_risk:
        live_action="NO TRADE / NEGATIVE CATALYST"
    elif technical_ok and rr_ok and runway_ok and live_conditions:
        live_action="BUY / LIVE CONFIRMED + CATALYST" if catalyst_bonus else "BUY / LIVE CONFIRMED"
    elif technical_ok:
        live_action="WAIT / LIVE CONFIRMATION"
    else:
        live_action="WATCH / NOT ARMED"

    result.update({
        "live_status":status,
        "live_session_date":str(latest_date),
        "live_price":round(price,2),
        "live_vwap":round(vwap,2) if math.isfinite(vwap) else math.nan,
        "live_above_vwap":bool(above_vwap),
        "opening_range_high":round(or_high,2) if math.isfinite(or_high) else math.nan,
        "opening_range_low":round(or_low,2) if math.isfinite(or_low) else math.nan,
        "live_above_or_high":bool(above_or),
        "intraday_rvol":round(rvol,2) if math.isfinite(rvol) else math.nan,
        "live_trigger_reached":bool(trigger_reached),
        "live_confirmation_score":int(score),
        "live_trade_action":live_action,
    })
    return result

def enrich_live_candidates(df: pd.DataFrame, limit: int = LIVE_ENRICH_LIMIT):
    if df.empty:
        return df

    out=df.copy()
    defaults=_empty_live()
    for col,value in defaults.items():
        out[col]=value

    # Advanced setups first. FORMING names are not promoted to live BUY.
    eligible=out[out["stage"].isin(["CONFIRMED","ARMED","FORMING"])].copy()
    eligible["live_stage_priority"]=eligible["stage"].map({"CONFIRMED":3,"ARMED":2,"FORMING":1}).fillna(0)
    eligible=eligible.sort_values(
        ["live_stage_priority","final_score"],ascending=[False,False]
    ).head(limit)

    for idx,row in eligible.iterrows():
        try:
            live=analyze_live_candidate(
                ticker=str(row["ticker"]),
                entry_trigger=float(row.get("entry_trigger",math.nan)),
                stage=str(row.get("stage","")),
                catalyst_score=float(pd.to_numeric(pd.Series([row.get("catalyst_score",0)]),errors="coerce").fillna(0).iloc[0]),
                rr_to_8pct=float(pd.to_numeric(pd.Series([row.get("effective_rr",row.get("rr_to_8pct",math.nan))]),errors="coerce").iloc[0]),
                runway_pct=float(pd.to_numeric(pd.Series([row.get("runway_to_next_resistance_pct",math.nan)]),errors="coerce").iloc[0]),
                negative_catalyst_risk=bool(row.get("negative_catalyst_risk",False)),
            )
            for k,v in live.items():
                out.at[idx,k]=v
        except Exception as e:
            out.at[idx,"live_status"]="ERROR"
            out.at[idx,"live_trade_action"]=f"ERROR / {type(e).__name__}"
    return out
