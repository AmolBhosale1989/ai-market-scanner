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

def _retest_quality(d: pd.DataFrame, ref: float, atr: float):
    last=d.iloc[-1]
    price=_finite(last["Close"])
    ema20=_finite(last["EMA20"])
    rsi=_finite(last.get("RSI14"))
    ret5=_finite(last.get("RET5"))
    range10=_finite(last.get("RANGE10_PCT"))
    vol5vs20=_finite(last.get("VOL5_VS20"),1.0)

    ema20_5=_finite(d["EMA20"].iloc[-6]) if len(d)>=6 else math.nan
    ema20_slope5=((ema20/ema20_5)-1)*100 if math.isfinite(ema20_5) and ema20_5>0 else math.nan
    atr_pct=atr/price*100 if price>0 else math.nan

    last_open=_finite(last["Open"])
    last_high=_finite(last["High"])
    last_low=_finite(last["Low"])
    close_location=(price-last_low)/(last_high-last_low) if last_high>last_low else 0.5
    bullish_close=price>last_open and close_location>=0.55

    prior=d.iloc[:-1]
    recent3=_finite(prior["Low"].tail(3).min()) if len(prior)>=3 else math.nan
    prev7=_finite(prior["Low"].tail(10).head(7).min()) if len(prior)>=10 else math.nan
    higher_low=(
        math.isfinite(recent3) and math.isfinite(prev7)
        and recent3>=prev7*1.002
    )

    touches=0
    if math.isfinite(ref):
        lows=pd.to_numeric(prior["Low"].tail(10),errors="coerce").dropna()
        touches=int(((lows-ref).abs()<=0.55*atr).sum())

    checks={
        "ema20_rising": math.isfinite(ema20_slope5) and ema20_slope5>=0.20,
        "volume_contracting": math.isfinite(vol5vs20) and vol5vs20<=1.00,
        "constructive_ret5": math.isfinite(ret5) and -6.0<=ret5<=1.5,
        "tight_range": math.isfinite(range10) and math.isfinite(atr_pct) and range10<=max(7.5,atr_pct*2.2),
        "higher_low": bool(higher_low),
        "bullish_close": bool(bullish_close),
        "rsi_healthy": math.isfinite(rsi) and 48<=rsi<=68,
        "support_respected": touches>=2,
    }

    # Trend and contraction are mandatory. Then require at least four additional
    # confirmations so retests are selective rather than mechanically frequent.
    mandatory=checks["ema20_rising"] and checks["volume_contracting"]
    optional_names=[
        "constructive_ret5","tight_range","higher_low",
        "bullish_close","rsi_healthy","support_respected",
    ]
    optional_score=sum(1 for k in optional_names if checks[k])
    quality_score=(2 if mandatory else 0)+optional_score
    qualified=mandatory and optional_score>=4

    reasons=[k for k,v in checks.items() if v]
    return {
        "qualified":qualified,
        "quality_score":quality_score,
        "optional_score":optional_score,
        "ema20_slope5_pct":ema20_slope5,
        "vol5_vs20":vol5vs20,
        "support_touch_count":touches,
        "higher_low":bool(higher_low),
        "bullish_close":bool(bullish_close),
        "close_location":close_location,
        "reasons":"|".join(reasons),
    }

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
        "retest_quality_score":0,
        "retest_quality_reasons":"",
        "ema20_slope5_pct":math.nan,
        "support_touch_count":0,
        "higher_low":False,
        "bullish_close":False,
        **stop_info,
        "swing5_low":swing5,
        "swing10_low":swing10,
    })

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
            quality=_retest_quality(d,ref,atr)

            if quality["qualified"]:
                retest_entry=ref+max(0.12*atr,0.0015*ref)
                if retest_entry<price:
                    lower_anchors=_valid_anchors(
                        retest_entry,atr,ema20,ema50,daily_support,swing5,swing10
                    )
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
                        "retest_quality_score":quality["quality_score"],
                        "retest_quality_reasons":quality["reasons"],
                        "ema20_slope5_pct":quality["ema20_slope5_pct"],
                        "support_touch_count":quality["support_touch_count"],
                        "higher_low":quality["higher_low"],
                        "bullish_close":quality["bullish_close"],
                        **stop_info,
                        "swing5_low":swing5,
                        "swing10_low":swing10,
                    })

    return candidates

def build_entry_stop_plan(d: pd.DataFrame, levels: dict, technical_stage: str):
    candidates=build_entry_stop_candidates(d,levels,technical_stage)
    return candidates[0] if candidates else None
