from __future__ import annotations

import math
import pandas as pd

def _finite(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan

def _stop_from_anchor(entry, anchor, atr, basis):
    structural_stop=anchor-0.18*atr
    min_risk=0.75*atr
    stop=min(structural_stop,entry-min_risk)
    max_risk=entry*0.06
    stop=max(stop,entry-max_risk)
    stop=max(0.01,stop)
    risk=entry-stop
    return {
        "stop":stop,
        "risk":risk,
        "risk_pct":risk/entry*100 if entry>0 else math.nan,
        "stop_basis":basis,
        "stop_anchor":anchor,
        "stop_anchor_distance_pct":(entry-anchor)/entry*100 if entry>0 else math.nan,
    }

def _valid_anchors(entry, atr, ema20, ema50, daily_support, swing5, swing10):
    anchors=[]
    for name,value,priority in [
        ("SWING5",swing5,5),
        ("EMA20",ema20,4),
        ("SWING10",swing10,3),
        ("DAILY_SUPPORT",daily_support,2),
        ("EMA50",ema50,1),
    ]:
        if math.isfinite(value) and 0<value<entry:
            distance_pct=(entry/value-1)*100
            if distance_pct<=7.0:
                anchors.append((name,value,priority,distance_pct))
    return sorted(anchors,key=lambda x:(x[3],-x[2]))

def build_entry_stop_candidates(d: pd.DataFrame, levels: dict, technical_stage: str):
    last=d.iloc[-1]
    price=_finite(last["Close"])
    atr=_finite(last["ATR14"])
    ema20=_finite(last["EMA20"])
    ema50=_finite(last["EMA50"])
    daily_res=_finite(levels.get("daily_resistance"))
    daily_support=_finite(levels.get("daily_support"))

    if not (math.isfinite(price) and math.isfinite(atr) and atr>0):
        return []

    prior=d.iloc[:-1].copy()
    swing5=_finite(prior["Low"].tail(5).min()) if len(prior)>=5 else math.nan
    swing10=_finite(prior["Low"].tail(10).min()) if len(prior)>=10 else math.nan

    candidates=[]

    # 1) Breakout/market plan.
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

    anchors=_valid_anchors(entry,atr,ema20,ema50,daily_support,swing5,swing10)
    if anchors:
        basis,anchor,_,_=anchors[0]
    else:
        basis,anchor="ATR_FALLBACK",entry-1.5*atr

    stop_info=_stop_from_anchor(entry,anchor,atr,basis)
    candidates.append({
        "entry":entry,
        "entry_model":entry_model,
        "entry_condition":"BREAKOUT",
        "entry_buffer_pct":entry_buffer_pct,
        "retest_reference":math.nan,
        "retest_distance_pct":math.nan,
        **stop_info,
        "swing5_low":swing5,
        "swing10_low":swing10,
    })

    # 2) Pullback/retest plan. Only valid in an established bullish structure.
    # We do not invent a lower entry: the candidate must be close enough to a
    # real support reference that a 1-3 session retest is plausible.
    if technical_stage in {"ARMED","FORMING"} and price>ema20>ema50:
        refs=[]
        for name,value,priority in [
            ("EMA20_RETEST",ema20,4),
            ("SWING5_RETEST",swing5,3),
            ("DAILY_SUPPORT_RETEST",daily_support,2),
        ]:
            if math.isfinite(value) and value>0 and value<price:
                dist=(price/value-1)*100
                if 0.5<=dist<=4.0:
                    refs.append((name,value,priority,dist))

        if refs:
            refs=sorted(refs,key=lambda x:(x[3],-x[2]))
            ref_name,ref,_,dist=refs[0]

            # Buy only after price trades into the retest zone and reclaims a
            # small buffer above the reference. This is not a blind limit order.
            retest_entry=ref+max(0.12*atr,0.0015*ref)
            if retest_entry<price:
                lower_anchors=_valid_anchors(
                    retest_entry,atr,ema20,ema50,daily_support,swing5,swing10
                )
                # Stop must be below a structure beneath the retest entry. If
                # the retest reference itself is the nearest anchor, use it.
                usable=[a for a in lower_anchors if a[1] <= ref+0.05*atr]
                if usable:
                    basis,anchor,_,_=usable[0]
                else:
                    basis,anchor=ref_name,ref

                stop_info=_stop_from_anchor(retest_entry,anchor,atr,basis)
                candidates.append({
                    "entry":retest_entry,
                    "entry_model":"PULLBACK_RETEST",
                    "entry_condition":"TOUCH_AND_RECLAIM",
                    "entry_buffer_pct":(retest_entry/ref-1)*100,
                    "retest_reference":ref,
                    "retest_distance_pct":dist,
                    **stop_info,
                    "swing5_low":swing5,
                    "swing10_low":swing10,
                })

    return candidates

def build_entry_stop_plan(d: pd.DataFrame, levels: dict, technical_stage: str):
    candidates=build_entry_stop_candidates(d,levels,technical_stage)
    return candidates[0] if candidates else None
