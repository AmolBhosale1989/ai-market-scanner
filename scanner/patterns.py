import math
import pandas as pd

def _pct(a, b):
    return ((a / b) - 1) * 100 if b else 0.0

def _safe(v, default=0.0):
    try:
        return float(v) if pd.notna(v) and math.isfinite(float(v)) else default
    except Exception:
        return default

def timeframe_levels(d: pd.DataFrame):
    last = d.iloc[-1]
    daily_support = _safe(d["Low"].tail(20).iloc[:-1].min(), last["Low"])
    daily_resistance = _safe(d["High"].tail(20).iloc[:-1].max(), last["High"])
    weekly = d[["Open","High","Low","Close","Volume"]].resample("W-FRI").agg(
        {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
    monthly = d[["Open","High","Low","Close","Volume"]].resample("ME").agg(
        {"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
    weekly_support = _safe(weekly["Low"].tail(13).iloc[:-1].min() if len(weekly)>1 else daily_support, daily_support)
    weekly_resistance = _safe(weekly["High"].tail(13).iloc[:-1].max() if len(weekly)>1 else daily_resistance, daily_resistance)
    monthly_support = _safe(monthly["Low"].tail(7).iloc[:-1].min() if len(monthly)>1 else weekly_support, weekly_support)
    monthly_resistance = _safe(monthly["High"].tail(7).iloc[:-1].max() if len(monthly)>1 else weekly_resistance, weekly_resistance)
    return {
        "daily_support":daily_support,"daily_resistance":daily_resistance,
        "weekly_support":weekly_support,"weekly_resistance":weekly_resistance,
        "monthly_support":monthly_support,"monthly_resistance":monthly_resistance,
    }

def detect_forming_setup(d: pd.DataFrame):
    last=d.iloc[-1]
    price=_safe(last["Close"]); atr=_safe(last["ATR14"])
    if price<=0 or atr<=0:
        return {"formation_score":0,"stage":"REJECT","pattern":"insufficient data",
                "distance_to_20d_high_pct":0,"distance_to_ema50_pct":0,"distance_to_ema200_pct":0}
    ema20,ema50,ema200=map(_safe,(last["EMA20"],last["EMA50"],last["EMA200"]))
    high20=_safe(last["HIGH20_PREV"],price)
    range10=_safe(last["RANGE10_PCT"])
    rvol=_safe(last["RVOL"])
    vol5vs20=_safe(last["VOL5_VS20"],1)
    ret20,ret5=_safe(last["RET20"]),_safe(last["RET5"])
    score=0; tags=[]
    if price>ema20>ema50:
        score+=18; tags.append("20/50 uptrend")
    elif price>ema50 and ema20>=ema50*0.99:
        score+=10; tags.append("above EMA50")
    if ema50>_safe(d["EMA50"].iloc[-11]):
        score+=8; tags.append("rising EMA50")
    dist50=abs(_pct(price,ema50))
    if dist50<=3:
        score+=14; tags.append("near EMA50 support")
    elif dist50<=5:
        score+=8
    dist200=_pct(ema200,price)
    if -2<=dist200<=6:
        score+=10; tags.append("near EMA200 pivot")
    dist_res=_pct(high20,price)
    if 0<=dist_res<=4:
        score+=16; tags.append("compressed below 20d resistance")
    elif 4<dist_res<=7:
        score+=8
    atr_pct=atr/price*100
    if range10<=max(8.0,atr_pct*2.5):
        score+=10; tags.append("tight range")
    if ret20>=8 and -8<=ret5<=2:
        score+=14; tags.append("bull flag/pullback")
    if vol5vs20<0.9:
        score+=6; tags.append("volume contraction")
    if 1.15<=rvol<=2.5:
        score+=8; tags.append("volume expanding")
    score=max(0,min(100,round(score,1)))
    if price>high20 and rvol>=1.3:
        stage="CONFIRMED"; tags.append("breakout")
    elif score>=72 and dist_res<=3:
        stage="ARMED"
    elif score>=55:
        stage="FORMING"
    elif score>=40:
        stage="DISCOVER"
    else:
        stage="REJECT"
    return {
        "formation_score":score,"stage":stage,
        "pattern":", ".join(tags[:5]) if tags else "none",
        "distance_to_20d_high_pct":round(dist_res,2),
        "distance_to_ema50_pct":round(_pct(price,ema50),2),
        "distance_to_ema200_pct":round(_pct(price,ema200),2),
    }
