from __future__ import annotations

import math
import pandas as pd

def _finite(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan

def build_entry_stop_plan(d: pd.DataFrame, levels: dict, technical_stage: str):
    last=d.iloc[-1]
    price=_finite(last["Close"])
    atr=_finite(last["ATR14"])
    ema20=_finite(last["EMA20"])
    ema50=_finite(last["EMA50"])
    daily_res=_finite(levels.get("daily_resistance"))
    daily_support=_finite(levels.get("daily_support"))

    if not (math.isfinite(price) and math.isfinite(atr) and atr>0):
        return None

    prior=d.iloc[:-1].copy()
    swing5=_finite(prior["Low"].tail(5).min()) if len(prior)>=5 else math.nan
    swing10=_finite(prior["Low"].tail(10).min()) if len(prior)>=10 else math.nan

    if technical_stage in {"CONFIRMED","EXTENDED"}:
        entry=price
        entry_model="CONFIRMED_MARKET"
        entry_buffer_pct=0.0
    else:
        resistance=daily_res if math.isfinite(daily_res) and daily_res>0 else price
        buffer=max(0.001*resistance,0.08*atr)
        entry=max(price,resistance+buffer)
        entry_model="RESISTANCE_BREAKOUT"
        entry_buffer_pct=(entry/resistance-1)*100 if resistance>0 else 0.0

    anchors=[]
    for name,value,priority in [
        ("SWING5",swing5,4),
        ("EMA20",ema20,3),
        ("SWING10",swing10,2),
        ("DAILY_SUPPORT",daily_support,1),
        ("EMA50",ema50,0),
    ]:
        if math.isfinite(value) and 0<value<entry:
            distance_pct=(entry/value-1)*100
            if distance_pct<=7.0:
                anchors.append((name,value,priority,distance_pct))

    if anchors:
        anchors=sorted(anchors,key=lambda x:(x[3],-x[2]))
        stop_basis,anchor,_,_=anchors[0]
    else:
        stop_basis="ATR_FALLBACK"
        anchor=entry-1.5*atr

    structural_stop=anchor-0.18*atr
    min_risk=0.75*atr
    stop=min(structural_stop,entry-min_risk)

    max_risk=entry*0.06
    stop=max(stop,entry-max_risk)
    stop=max(0.01,stop)

    risk=entry-stop
    risk_pct=risk/entry*100 if entry>0 else math.nan
    anchor_distance_pct=(entry-anchor)/entry*100 if entry>0 else math.nan

    return {
        "entry":entry,
        "stop":stop,
        "risk":risk,
        "risk_pct":risk_pct,
        "entry_model":entry_model,
        "entry_buffer_pct":entry_buffer_pct,
        "stop_basis":stop_basis,
        "stop_anchor":anchor,
        "stop_anchor_distance_pct":anchor_distance_pct,
        "swing5_low":swing5,
        "swing10_low":swing10,
    }
