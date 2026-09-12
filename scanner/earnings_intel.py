from __future__ import annotations

import math
import os
from io import StringIO

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from .config import EARNINGS_HISTORY_QUARTERS, EARNINGS_INTEL_LIMIT


def _safe_float(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan


def _alpha_request(function: str, symbol: str):
    key=os.getenv("ALPHA_VANTAGE_API_KEY","").strip()
    if not key:
        return {}
    try:
        r=requests.get(
            "https://www.alphavantage.co/query",
            params={"function":function,"symbol":symbol,"apikey":key},
            timeout=25,
        )
        r.raise_for_status()
        data=r.json()
        if not isinstance(data,dict):
            return {}
        if any(k in data for k in ["Information","Note","Error Message"]):
            return {}
        return data
    except Exception:
        return {}


def _earnings_history(symbol: str):
    data=_alpha_request("EARNINGS",symbol)
    q=data.get("quarterlyEarnings") if isinstance(data,dict) else None
    if not isinstance(q,list) or not q:
        return pd.DataFrame()
    df=pd.DataFrame(q).copy()
    for c in ["reportedEPS","estimatedEPS","surprise","surprisePercentage"]:
        if c in df.columns:
            df[c]=pd.to_numeric(df[c],errors="coerce")
    if "reportedDate" in df.columns:
        df["reportedDate"]=pd.to_datetime(df["reportedDate"],errors="coerce")
    return df.sort_values("reportedDate",ascending=False).head(EARNINGS_HISTORY_QUARTERS)


def _reaction_stats(symbol: str, earnings_df: pd.DataFrame):
    if earnings_df is None or earnings_df.empty or "reportedDate" not in earnings_df.columns:
        return {}
    try:
        hist=yf.download(symbol,period="3y",interval="1d",auto_adjust=True,progress=False,threads=False,timeout=20)
    except Exception:
        return {}
    if hist is None or hist.empty:
        return {}
    if isinstance(hist.columns,pd.MultiIndex):
        hist.columns=[c[0] for c in hist.columns]
    if "Close" not in hist.columns:
        return {}
    idx=pd.DatetimeIndex(hist.index)
    if idx.tz is not None:
        idx=idx.tz_convert(None)
    hist=hist.copy()
    hist.index=idx.normalize()

    reactions=[]
    for _,r in earnings_df.iterrows():
        dt=r.get("reportedDate")
        if pd.isna(dt):
            continue
        d=pd.Timestamp(dt).normalize()
        pos=hist.index.searchsorted(d,side="left")
        # approximate next-session close-to-close earnings reaction
        if pos<=0 or pos>=len(hist):
            continue
        prev_close=_safe_float(hist["Close"].iloc[pos-1])
        next_close=_safe_float(hist["Close"].iloc[pos])
        if prev_close>0 and math.isfinite(next_close):
            reactions.append((next_close/prev_close-1)*100)

    if not reactions:
        return {}
    a=np.array(reactions,dtype=float)
    return {
        "prior_earnings_reaction_avg_pct":round(float(np.mean(a)),2),
        "prior_earnings_reaction_median_pct":round(float(np.median(a)),2),
        "prior_earnings_reaction_abs_avg_pct":round(float(np.mean(np.abs(a))),2),
        "prior_earnings_positive_reaction_rate":round(float(np.mean(a>0))*100,1),
        "prior_earnings_reaction_samples":int(len(a)),
    }


def _beat_miss_stats(earnings_df: pd.DataFrame):
    if earnings_df is None or earnings_df.empty:
        return {}
    surprise=pd.to_numeric(earnings_df.get("surprisePercentage"),errors="coerce").dropna()
    reported=pd.to_numeric(earnings_df.get("reportedEPS"),errors="coerce")
    estimated=pd.to_numeric(earnings_df.get("estimatedEPS"),errors="coerce")
    if surprise.empty and reported.notna().sum()==0:
        return {}

    if surprise.empty:
        denom=estimated.abs().replace(0,np.nan)
        surprise=((reported-estimated)/denom*100).dropna()

    beats=(surprise>0)
    misses=(surprise<0)
    streak=0
    direction=0
    for v in surprise.tolist():
        cur=1 if v>0 else (-1 if v<0 else 0)
        if cur==0:
            break
        if direction==0:
            direction=cur
        if cur!=direction:
            break
        streak+=1

    return {
        "earnings_history_samples":int(len(surprise)),
        "beat_rate_pct":round(float(beats.mean()*100),1) if len(surprise) else math.nan,
        "miss_rate_pct":round(float(misses.mean()*100),1) if len(surprise) else math.nan,
        "median_surprise_pct":round(float(surprise.median()),2) if len(surprise) else math.nan,
        "latest_surprise_pct":round(float(surprise.iloc[0]),2) if len(surprise) else math.nan,
        "surprise_streak":int(streak*direction),
    }


def _compression_stats(symbol: str):
    try:
        h=yf.download(symbol,period="6mo",interval="1d",auto_adjust=True,progress=False,threads=False,timeout=20)
    except Exception:
        return {}
    if h is None or len(h)<25:
        return {}
    if isinstance(h.columns,pd.MultiIndex):
        h.columns=[c[0] for c in h.columns]
    needed={"High","Low","Close","Volume"}
    if not needed.issubset(h.columns):
        return {}

    h=h.dropna(subset=["Close"]).copy()
    close=pd.to_numeric(h["Close"],errors="coerce")
    high=pd.to_numeric(h["High"],errors="coerce")
    low=pd.to_numeric(h["Low"],errors="coerce")
    vol=pd.to_numeric(h["Volume"],errors="coerce")

    last=_safe_float(close.iloc[-1])
    range10=((high.tail(10).max()-low.tail(10).min())/last*100) if last>0 else math.nan
    range20=((high.tail(20).max()-low.tail(20).min())/last*100) if last>0 else math.nan
    vol5=vol.tail(5).mean()
    vol20=vol.tail(20).mean()
    vol_ratio=(vol5/vol20) if vol20 and math.isfinite(vol20) else math.nan
    ret5=(last/_safe_float(close.iloc[-6])-1)*100 if len(close)>=6 else math.nan
    resistance=_safe_float(high.tail(20).max())
    runway=((resistance/last)-1)*100 if last>0 and resistance>=last else 0.0

    score=0
    if math.isfinite(range10) and range10<=8: score+=25
    if math.isfinite(range10) and range10<=5: score+=15
    if math.isfinite(vol_ratio) and vol_ratio<=0.9: score+=25
    if math.isfinite(vol_ratio) and vol_ratio<=0.7: score+=10
    if math.isfinite(ret5) and -4<=ret5<=6: score+=15
    if math.isfinite(runway) and 1<=runway<=8: score+=10
    score=min(100,score)

    return {
        "pre_event_range10_pct":round(float(range10),2) if math.isfinite(range10) else math.nan,
        "pre_event_range20_pct":round(float(range20),2) if math.isfinite(range20) else math.nan,
        "pre_event_vol5_vs20":round(float(vol_ratio),2) if math.isfinite(vol_ratio) else math.nan,
        "pre_event_ret5_pct":round(float(ret5),2) if math.isfinite(ret5) else math.nan,
        "pre_event_compression_score":int(score),
    }


def _options_implied_move(symbol: str, event_date):
    try:
        t=yf.Ticker(symbol)
        expiries=list(t.options or [])
    except Exception:
        return {"options_implied_move_pct":math.nan,"options_expiry":"","options_data_status":"UNAVAILABLE"}
    if not expiries:
        return {"options_implied_move_pct":math.nan,"options_expiry":"","options_data_status":"NO_OPTIONS"}

    ed=pd.Timestamp(event_date)
    candidates=[]
    for x in expiries:
        try:
            d=pd.Timestamp(x)
            if d>=ed:
                candidates.append(d)
        except Exception:
            continue
    if not candidates:
        return {"options_implied_move_pct":math.nan,"options_expiry":"","options_data_status":"NO_POST_EVENT_EXPIRY"}
    exp=min(candidates)
    try:
        chain=t.option_chain(exp.strftime("%Y-%m-%d"))
        spot=_safe_float(t.history(period="5d",auto_adjust=True)["Close"].iloc[-1])
        if not math.isfinite(spot) or spot<=0:
            return {"options_implied_move_pct":math.nan,"options_expiry":exp.strftime("%Y-%m-%d"),"options_data_status":"NO_SPOT"}
        calls=chain.calls.copy(); puts=chain.puts.copy()
        if calls.empty or puts.empty:
            return {"options_implied_move_pct":math.nan,"options_expiry":exp.strftime("%Y-%m-%d"),"options_data_status":"EMPTY_CHAIN"}
        strike=float(calls.iloc[(calls["strike"]-spot).abs().argsort()[:1]]["strike"].iloc[0])
        c=calls.iloc[(calls["strike"]-strike).abs().argsort()[:1]].iloc[0]
        p=puts.iloc[(puts["strike"]-strike).abs().argsort()[:1]].iloc[0]
        def px(r):
            bid=_safe_float(r.get("bid")); ask=_safe_float(r.get("ask")); last=_safe_float(r.get("lastPrice"))
            if math.isfinite(bid) and math.isfinite(ask) and ask>=bid and (bid>0 or ask>0):
                return (bid+ask)/2
            return last
        cp=px(c); pp=px(p)
        if not (math.isfinite(cp) and math.isfinite(pp)):
            raise ValueError("missing option prices")
        move=(cp+pp)/spot*100
        return {
            "options_implied_move_pct":round(float(move),2),
            "options_expiry":exp.strftime("%Y-%m-%d"),
            "options_atm_strike":round(strike,2),
            "options_data_status":"OK",
        }
    except Exception:
        return {"options_implied_move_pct":math.nan,"options_expiry":exp.strftime("%Y-%m-%d"),"options_data_status":"UNAVAILABLE"}


def _guidance_context(symbol: str):
    try:
        t=yf.Ticker(symbol)
        try:
            news=t.get_news(count=15)
        except TypeError:
            news=t.news
    except Exception:
        return {}

    pos_terms=["raises guidance","raised guidance","guidance raised","outlook raised","raises outlook","reaffirms guidance"]
    neg_terms=["cuts guidance","cut guidance","guidance cut","lowers guidance","lowered outlook","withdraws guidance"]
    revision_up=["price target raised","estimate raised","estimates raised","upgrade"]
    revision_down=["price target cut","estimate cut","estimates cut","downgrade"]

    pos=neg=up=down=0
    for item in news or []:
        src=item.get("content") if isinstance(item,dict) and isinstance(item.get("content"),dict) else item
        if not isinstance(src,dict):
            continue
        title=str(src.get("title") or "").lower()
        pos+=sum(1 for x in pos_terms if x in title)
        neg+=sum(1 for x in neg_terms if x in title)
        up+=sum(1 for x in revision_up if x in title)
        down+=sum(1 for x in revision_down if x in title)
    guidance_score=max(-100,min(100,(pos-neg)*30+(up-down)*15))
    return {
        "guidance_positive_mentions":pos,
        "guidance_negative_mentions":neg,
        "revision_up_mentions":up,
        "revision_down_mentions":down,
        "guidance_revision_score":int(guidance_score),
    }


def enrich_earnings_intelligence(events: pd.DataFrame, limit: int=EARNINGS_INTEL_LIMIT):
    if events is None or events.empty:
        return events
    out=events.copy()
    earnings=out[out.get("event_type","").eq("EARNINGS")].copy()
    if earnings.empty:
        return out
    earnings=earnings.sort_values(["event_priority","days_to_event"],ascending=[True,True]).head(limit)

    for idx,row in earnings.iterrows():
        symbol=str(row.get("ticker",""))
        hist=_earnings_history(symbol)
        metrics={}
        metrics.update(_beat_miss_stats(hist))
        metrics.update(_reaction_stats(symbol,hist))
        metrics.update(_compression_stats(symbol))
        metrics.update(_guidance_context(symbol))
        metrics.update(_options_implied_move(symbol,row.get("event_date_utc")))

        # Composite pre-earnings intelligence score. It is a ranking aid, not a
        # directional probability or trade signal.
        score=50.0
        beat=_safe_float(metrics.get("beat_rate_pct"))
        med=_safe_float(metrics.get("median_surprise_pct"))
        reaction=_safe_float(metrics.get("prior_earnings_positive_reaction_rate"))
        compression=_safe_float(metrics.get("pre_event_compression_score"))
        guide=_safe_float(metrics.get("guidance_revision_score"))
        if math.isfinite(beat): score+=(beat-50)*0.20
        if math.isfinite(med): score+=max(-10,min(10,med*0.35))
        if math.isfinite(reaction): score+=(reaction-50)*0.12
        if math.isfinite(compression): score+=(compression-50)*0.15
        if math.isfinite(guide): score+=guide*0.12
        score=max(0,min(100,score))
        metrics["pre_earnings_intel_score"]=round(score,1)

        for k,v in metrics.items():
            out.at[idx,k]=v

    return out
