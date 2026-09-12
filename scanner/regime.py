from __future__ import annotations

import math
import pandas as pd
from .indicators import add_indicators

def _ret(d: pd.DataFrame, n: int):
    if d is None or len(d)<=n:
        return math.nan
    return float((d["Close"].iloc[-1]/d["Close"].iloc[-1-n]-1)*100)

def evaluate_regime(df: pd.DataFrame):
    if df is None or len(df)<60:
        return {"regime_score":50.0,"regime_state":"NEUTRAL","regime_reason":"insufficient history"}

    d=add_indicators(df.copy())
    last=d.iloc[-1]
    close=float(last["Close"])
    ema20=float(last["EMA20"])
    ema50=float(last["EMA50"])
    r5=_ret(d,5)
    r20=_ret(d,20)
    ema20_prev=float(d["EMA20"].iloc[-6]) if len(d)>=6 else ema20
    slope20=(ema20/ema20_prev-1)*100 if ema20_prev else 0.0

    score=50.0
    reasons=[]
    if close>ema20:
        score+=12; reasons.append("price>EMA20")
    else:
        score-=12; reasons.append("price<EMA20")
    if ema20>ema50:
        score+=12; reasons.append("EMA20>EMA50")
    else:
        score-=12; reasons.append("EMA20<EMA50")
    if slope20>0.15:
        score+=10; reasons.append("EMA20 rising")
    elif slope20<0:
        score-=10; reasons.append("EMA20 falling")
    if math.isfinite(r5):
        if r5>0: score+=5
        elif r5<-2: score-=5
    if math.isfinite(r20):
        if r20>2: score+=8
        elif r20<0: score-=8

    score=max(0,min(100,score))
    if score>=70:
        state="STRONG"
    elif score>=45:
        state="NEUTRAL"
    else:
        state="WEAK"

    return {
        "regime_score":round(score,1),
        "regime_state":state,
        "regime_reason":"; ".join(reasons),
    }
