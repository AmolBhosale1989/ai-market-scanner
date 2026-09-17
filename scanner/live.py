from __future__ import annotations

from datetime import datetime, time as dtime
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf

from .order_flow import bar_order_flow_proxy

from .config import (
    LIVE_ENRICH_LIMIT,
    LIVE_INTERVAL,
    LIVE_PERIOD,
    LIVE_MIN_INTRADAY_RVOL,
    OPENING_RANGE_MINUTES,
    MIN_RUNWAY_PCT,
    MIN_EFFECTIVE_RR,
    CATALYST_ACTIVE_SCORE,
    MAX_BID_ASK_SPREAD_PCT,
)

NY = ZoneInfo("America/New_York")
NYSE = mcal.get_calendar("NYSE")

def _empty_live(status="NOT CHECKED"):
    return {
        "live_status": status,
        "live_session_date": "",
        "live_bar_at_et": "",
        "live_bar_high": math.nan,
        "live_bar_low": math.nan,
        "live_price": math.nan,
        "session_high": math.nan,
        "session_low": math.nan,
        "live_vwap": math.nan,
        "live_above_vwap": False,
        "opening_range_high": math.nan,
        "opening_range_low": math.nan,
        "live_above_or_high": False,
        "intraday_rvol": math.nan,
        "volume_vs_9ma": math.nan,
        "opening_30m_rvol": math.nan,
        "opening_volume_spike_2x": False,
        "quote_bid": math.nan,
        "quote_ask": math.nan,
        "bid_ask_spread_pct": math.nan,
        "spread_gate_passed": False,
        "spread_gate_source": "NONE",
        "live_trigger_reached": False,
        "live_retest_touched": False,
        "live_confirmation_score": 0,
        "premarket_price": math.nan,
        "premarket_gap_pct": math.nan,
        "premarket_volume": math.nan,
        "premarket_status": "NOT CHECKED",
        "live_trade_action": "NO LIVE SIGNAL",
        "order_flow_mode": "BAR_PROXY",
        "order_flow_score": 0.0,
        "order_flow_state": "NO DATA",
        "buy_pressure_pct": math.nan,
        "sell_pressure_pct": math.nan,
        "signed_volume_proxy": math.nan,
        "volume_imbalance_proxy": math.nan,
        "close_location_value": math.nan,
        "volume_impulse": math.nan,
        "vwap_pressure": math.nan,
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

def _market_session(now_et: datetime):
    day=now_et.date()
    try:
        sched=NYSE.schedule(start_date=day,end_date=day)
    except Exception:
        sched=pd.DataFrame()
    if sched.empty:
        return "MARKET CLOSED",None,None
    row=sched.iloc[0]
    market_open=pd.Timestamp(row["market_open"]).tz_convert(NY).to_pydatetime()
    market_close=pd.Timestamp(row["market_close"]).tz_convert(NY).to_pydatetime()
    if now_et < market_open:
        return "PREMARKET",market_open,market_close
    if now_et <= market_close:
        return "LIVE",market_open,market_close
    return "MARKET CLOSED",market_open,market_close

def _market_state(now_et: datetime):
    return _market_session(now_et)[0]

def _premarket_frame(d: pd.DataFrame, session_date):
    x=d[d.index.date==session_date].copy()
    if x.empty:
        return x
    return x.between_time("04:00","09:29")

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

def _volume_confirmation(d: pd.DataFrame, session_date, current_session: pd.DataFrame):
    if current_session.empty:
        return math.nan, math.nan, False
    vols=pd.to_numeric(current_session["Volume"],errors="coerce").fillna(0)
    volume_vs_9ma=math.nan
    if len(vols)>=10:
        baseline=float(vols.iloc[-10:-1].mean())
        latest=float(vols.iloc[-1])
        if baseline>0:
            volume_vs_9ma=latest/baseline
    opening=current_session.between_time("09:30","09:59")
    opening_volume=float(pd.to_numeric(opening["Volume"],errors="coerce").fillna(0).sum()) if not opening.empty else 0.0
    prior_dates=sorted({x for x in d.index.date if x < session_date}, reverse=True)[:3]
    comps=[]
    for dt in prior_dates:
        prior=_session_frame(d,dt).between_time("09:30","09:59")
        if prior.empty:
            continue
        vol=float(pd.to_numeric(prior["Volume"],errors="coerce").fillna(0).sum())
        if vol>0:
            comps.append(vol)
    opening_rvol=math.nan
    if comps and opening_volume>0:
        baseline=float(np.mean(comps))
        if baseline>0:
            opening_rvol=opening_volume/baseline
    opening_spike=math.isfinite(opening_rvol) and opening_rvol>=2.0
    return volume_vs_9ma, opening_rvol, opening_spike

def _quote_spread(ticker: str, reference_price: float = math.nan):
    try:
        info=yf.Ticker(ticker).get_info()
    except Exception:
        return math.nan,math.nan,math.nan
    try:
        bid=float(info.get("bid",math.nan)); ask=float(info.get("ask",math.nan))
    except (TypeError,ValueError):
        return math.nan,math.nan,math.nan
    if not (math.isfinite(bid) and math.isfinite(ask) and bid>0 and ask>=bid):
        return math.nan,math.nan,math.nan
    midpoint=(bid+ask)/2
    spread_pct=(ask-bid)/midpoint*100 if midpoint>0 else math.nan
    if math.isfinite(reference_price) and reference_price>0 and abs(midpoint/reference_price-1)>0.03:
        return bid,ask,math.nan
    if math.isfinite(spread_pct) and spread_pct>5.0:
        return bid,ask,math.nan
    return bid,ask,spread_pct

def analyze_live_candidate(ticker: str, entry_trigger: float, stage: str, catalyst_score: float,
                           rr_to_8pct: float, runway_pct: float, negative_catalyst_risk: bool,
                           entry_condition: str = "BREAKOUT", technical_score: float = 0.0,
                           formation_score: float = 0.0, avg_dollar_volume: float = 0.0):
    result=_empty_live(); now_et=datetime.now(NY); state=_market_state(now_et)
    try:
        raw=yf.download(ticker,period=LIVE_PERIOD,interval=LIVE_INTERVAL,auto_adjust=True,progress=False,threads=False,prepost=True,timeout=20)
    except TypeError:
        raw=yf.download(ticker,period=LIVE_PERIOD,interval=LIVE_INTERVAL,auto_adjust=True,progress=False,threads=False,prepost=True)
    except Exception:
        result["live_status"]="ERROR"; return result
    d=_normalize_intraday(raw)
    if d.empty:
        result["live_status"]="NO INTRADAY DATA"; return result
    latest_date=d.index[-1].date(); latest_session=_session_frame(d,latest_date); premarket=_premarket_frame(d,now_et.date())
    premarket_price=math.nan; premarket_gap=math.nan; premarket_volume=math.nan; premarket_status="NO PREMARKET DATA"
    if not premarket.empty:
        premarket_price=float(premarket["Close"].iloc[-1]); premarket_volume=float(pd.to_numeric(premarket["Volume"],errors="coerce").fillna(0).sum())
        prior_sessions=sorted({x for x in d.index.date if x < now_et.date()},reverse=True)
        if prior_sessions:
            prior=_session_frame(d,prior_sessions[0])
            if not prior.empty:
                prior_close=float(prior["Close"].iloc[-1])
                if prior_close>0: premarket_gap=(premarket_price/prior_close-1)*100
        premarket_status="AVAILABLE"
    if latest_session.empty:
        result.update({"live_status":"PREMARKET" if state=="PREMARKET" and not premarket.empty else "NO REGULAR SESSION DATA","premarket_price":round(premarket_price,2) if math.isfinite(premarket_price) else math.nan,"premarket_gap_pct":round(premarket_gap,2) if math.isfinite(premarket_gap) else math.nan,"premarket_volume":round(premarket_volume,0) if math.isfinite(premarket_volume) else math.nan,"premarket_status":premarket_status,"live_trade_action":"WAIT / PREMARKET" if state=="PREMARKET" else "NO LIVE SIGNAL"})
        return result
    is_current_session=(latest_date==now_et.date())
    status="STALE SESSION" if not is_current_session else ("LIVE" if state=="LIVE" else ("PREMARKET / PRIOR SESSION" if state=="PREMARKET" else "MARKET CLOSED"))
    price=float(latest_session["Close"].iloc[-1]); session_high=float(latest_session["High"].max()); session_low=float(latest_session["Low"].min()); vwap=_session_vwap(latest_session)
    or_high,or_low,or_complete=_opening_range(latest_session); rvol=_intraday_rvol(d,latest_date,latest_session); volume_vs_9ma,opening_30m_rvol,opening_volume_spike_2x=_volume_confirmation(d,latest_date,latest_session); order_flow=bar_order_flow_proxy(latest_session)
    bid,ask,spread_pct=_quote_spread(ticker,price) if state=="LIVE" else (math.nan,math.nan,math.nan)
    quote_spread_ok=math.isfinite(spread_pct) and spread_pct<=MAX_BID_ASK_SPREAD_PCT; liquidity_proxy_ok=(not math.isfinite(spread_pct)) and math.isfinite(avg_dollar_volume) and avg_dollar_volume>=100_000_000; spread_ok=bool(quote_spread_ok or liquidity_proxy_ok); spread_source="QUOTE" if quote_spread_ok else ("LIQUIDITY_PROXY" if liquidity_proxy_ok else "NONE")
    above_vwap=math.isfinite(vwap) and price>vwap; above_or=or_complete and math.isfinite(or_high) and price>or_high; retest_touched=bool(entry_condition=="TOUCH_AND_RECLAIM" and math.isfinite(entry_trigger) and float(latest_session["Low"].min())<=entry_trigger)
    trigger_reached=(retest_touched and price>=entry_trigger) if entry_condition=="TOUCH_AND_RECLAIM" else (math.isfinite(entry_trigger) and price>=entry_trigger)
    score=(25 if above_vwap else 0)+(25 if ((retest_touched if entry_condition=="TOUCH_AND_RECLAIM" else above_or)) else 0)+(25 if trigger_reached else 0)+(20 if math.isfinite(rvol) and rvol>=LIVE_MIN_INTRADAY_RVOL else 0)+(5 if math.isfinite(volume_vs_9ma) and volume_vs_9ma>=2.0 else 0)+(5 if opening_volume_spike_2x else 0)
    technical_ok=stage in {"ARMED","CONFIRMED"} or (stage=="FORMING" and technical_score>=80 and formation_score>=60); catalyst_ok=(not negative_catalyst_risk) and catalyst_score>=CATALYST_ACTIVE_SCORE; rr_ok=math.isfinite(rr_to_8pct) and rr_to_8pct>=1.30; rr_strong=math.isfinite(rr_to_8pct) and rr_to_8pct>=2.00; runway_ok=math.isfinite(runway_pct) and runway_pct>=MIN_RUNWAY_PCT
    live_conditions=above_vwap and (retest_touched if entry_condition=="TOUCH_AND_RECLAIM" else above_or) and trigger_reached and math.isfinite(rvol) and rvol>=LIVE_MIN_INTRADAY_RVOL and spread_ok
    if status!="LIVE": live_action="WAIT / MARKET NOT LIVE"
    elif stage=="DISCOVER": live_action="WATCH / DISCOVERY"
    elif technical_ok and catalyst_ok and rr_ok and runway_ok and live_conditions: live_action="BUY / LIVE CONFIRMED" if rr_strong else "BUY / LIVE CONFIRMED (RR FLOOR)"
    elif technical_ok and live_conditions: live_action="WATCH / LIVE TECHNICAL"
    else: live_action="NO LIVE SIGNAL"
    result.update({"live_status":status,"live_session_date":str(latest_date),"live_bar_at_et":str(latest_session.index[-1]),"live_bar_high":round(float(latest_session["High"].iloc[-1]),2),"live_bar_low":round(float(latest_session["Low"].iloc[-1]),2),"live_price":round(price,2),"session_high":round(session_high,2),"session_low":round(session_low,2),"live_vwap":round(vwap,2) if math.isfinite(vwap) else math.nan,"live_above_vwap":bool(above_vwap),"opening_range_high":round(or_high,2) if math.isfinite(or_high) else math.nan,"opening_range_low":round(or_low,2) if math.isfinite(or_low) else math.nan,"live_above_or_high":bool(above_or),"intraday_rvol":round(rvol,2) if math.isfinite(rvol) else math.nan,"volume_vs_9ma":round(volume_vs_9ma,2) if math.isfinite(volume_vs_9ma) else math.nan,"opening_30m_rvol":round(opening_30m_rvol,2) if math.isfinite(opening_30m_rvol) else math.nan,"opening_volume_spike_2x":bool(opening_volume_spike_2x),"quote_bid":round(bid,4) if math.isfinite(bid) else math.nan,"quote_ask":round(ask,4) if math.isfinite(ask) else math.nan,"bid_ask_spread_pct":round(spread_pct,4) if math.isfinite(spread_pct) else math.nan,"spread_gate_passed":bool(spread_ok),"spread_gate_source":spread_source,"live_trigger_reached":bool(trigger_reached),"live_retest_touched":bool(retest_touched),"live_confirmation_score":int(score),"premarket_price":round(premarket_price,2) if math.isfinite(premarket_price) else math.nan,"premarket_gap_pct":round(premarket_gap,2) if math.isfinite(premarket_gap) else math.nan,"premarket_volume":round(premarket_volume,0) if math.isfinite(premarket_volume) else math.nan,"premarket_status":premarket_status,**order_flow,"live_trade_action":live_action})
    return result

def enrich_live_candidates(df: pd.DataFrame, limit: int = LIVE_ENRICH_LIMIT):
    if df.empty:
        return df
    out=df.copy(); defaults=_empty_live()
    for col,value in defaults.items(): out[col]=value
    eligible=out[out["stage"].isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])].copy()
    eligible["live_stage_priority"]=eligible["stage"].map({"CONFIRMED":4,"ARMED":3,"FORMING":2,"DISCOVER":1}).fillna(0)
    if "market_hunt_score" not in eligible.columns:
        raise RuntimeError("V3_INPUT_SCHEMA_FAILED: market_hunt_score")
    eligible=eligible.sort_values(["live_stage_priority","market_hunt_score"],ascending=[False,False]).head(limit)
    for idx,row in eligible.iterrows():
        try:
            live=analyze_live_candidate(ticker=str(row["ticker"]),entry_trigger=float(row.get("entry_trigger",math.nan)),stage=str(row.get("stage","")),catalyst_score=float(pd.to_numeric(pd.Series([row.get("catalyst_score",0)]),errors="coerce").fillna(0).iloc[0]),rr_to_8pct=float(pd.to_numeric(pd.Series([row.get("effective_rr",row.get("rr_to_8pct",math.nan))]),errors="coerce").iloc[0]),runway_pct=float(pd.to_numeric(pd.Series([row.get("runway_to_next_resistance_pct",math.nan)]),errors="coerce").iloc[0]),negative_catalyst_risk=bool(row.get("negative_catalyst_risk",False)),entry_condition=str(row.get("entry_condition","BREAKOUT")),technical_score=float(pd.to_numeric(pd.Series([row.get("technical_score",0)]),errors="coerce").fillna(0).iloc[0]),formation_score=float(pd.to_numeric(pd.Series([row.get("formation_score",0)]),errors="coerce").fillna(0).iloc[0]),avg_dollar_volume=float(pd.to_numeric(pd.Series([row.get("avg_dollar_volume",0)]),errors="coerce").fillna(0).iloc[0]))
            for k,v in live.items(): out.at[idx,k]=v
        except Exception as e:
            out.at[idx,"live_status"]="ERROR"; out.at[idx,"live_trade_action"]=f"ERROR / {type(e).__name__}"
    return out
