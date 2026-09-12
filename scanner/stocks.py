import math
import pandas as pd
from .config import MIN_RUNWAY_PCT, MIN_EFFECTIVE_RR, MIN_HISTORY_DAYS
from .entry_model import build_entry_stop_candidates
from .indicators import add_indicators
from .patterns import detect_forming_setup, timeframe_levels

def _f(v, default=0.0):
    try:
        return float(v) if pd.notna(v) else default
    except Exception:
        return default

def _plan_metrics(plan, levels):
    entry=float(plan["entry"])
    stop=float(plan["stop"])
    risk=max(0.01,entry-stop)

    target5=entry*1.05
    target8=entry*1.08
    target10=entry*1.10
    rr8=(target8-entry)/risk

    higher_res=[
        x for x in [levels["weekly_resistance"],levels["monthly_resistance"]]
        if math.isfinite(_f(x,math.nan)) and x>entry
    ]
    next_res=min(higher_res) if higher_res else math.nan
    runway_pct=((next_res/entry)-1)*100 if math.isfinite(next_res) else math.nan
    runway_ok=math.isfinite(runway_pct) and runway_pct>=MIN_RUNWAY_PCT

    effective_target=min(target8,next_res) if math.isfinite(next_res) else target8
    effective_target_pct=((effective_target/entry)-1)*100
    effective_rr=max(0.0,(effective_target-entry)/risk)

    return {
        **plan,
        "target5":target5,
        "target8":target8,
        "target10":target10,
        "rr8":rr8,
        "next_res":next_res,
        "runway_pct":runway_pct,
        "runway_ok":runway_ok,
        "effective_target":effective_target,
        "effective_target_pct":effective_target_pct,
        "effective_rr":effective_rr,
    }

def _choose_plan(plans, levels, market_regime_state="NEUTRAL"):
    evaluated=[_plan_metrics(p,levels) for p in plans]
    if not evaluated:
        return None

    breakout=next((x for x in evaluated if x["entry_condition"]=="BREAKOUT"),evaluated[0])
    retests=[x for x in evaluated if x["entry_condition"]=="TOUCH_AND_RECLAIM"]
    if market_regime_state=="WEAK":
        retests=[]

    # Retest is preferred only when it materially improves asymmetry without
    # becoming an unrealistically tight or distant order.
    eligible=[
        x for x in retests
        if x["risk_pct"]<=4.5
        and x["risk_pct"]>=0.6
        and math.isfinite(x["effective_rr"])
        and x["effective_rr"]>=breakout["effective_rr"]+0.35
        and x["retest_distance_pct"]<=4.0
    ]
    if eligible:
        return max(eligible,key=lambda x:x["effective_rr"])
    return breakout

def analyze_dataframe(ticker: str, df: pd.DataFrame, benchmark_return20: float = 0.0, market_regime: dict | None = None):
    if df is None or len(df)<MIN_HISTORY_DAYS:
        return None

    d=add_indicators(df)
    last=d.iloc[-1]
    required=["ATR14","RSI14","SMA200","EMA50","EMA200","RVOL","RET20"]
    if any(pd.isna(last[x]) for x in required):
        return None

    price=_f(last["Close"])
    atr=_f(last["ATR14"])
    avg_dollar_volume=_f((d["Close"]*d["Volume"]).tail(20).mean())
    if price<=0 or atr<=0:
        return None

    levels=timeframe_levels(d)
    formation=detect_forming_setup(d)
    technical_stage=formation["stage"]
    rs20=_f(last["RET20"])-benchmark_return20

    market_regime=market_regime or {"regime_score":50.0,"regime_state":"NEUTRAL","regime_reason":"not provided"}
    market_regime_state=str(market_regime.get("regime_state","NEUTRAL"))
    market_regime_score=_f(market_regime.get("regime_score",50.0),50.0)

    candidates=build_entry_stop_candidates(d,levels,technical_stage)
    plan=_choose_plan(candidates,levels,market_regime_state=market_regime_state)
    if not plan:
        return None

    entry=float(plan["entry"])
    stop=float(plan["stop"])
    effective_rr=float(plan["effective_rr"])
    runway_pct=float(plan["runway_pct"]) if math.isfinite(plan["runway_pct"]) else math.nan
    runway_ok=bool(plan["runway_ok"])
    effective_rr_ok=effective_rr>=MIN_EFFECTIVE_RR

    stage=technical_stage
    runway_blocked=technical_stage in {"ARMED","CONFIRMED"} and not runway_ok
    rr_blocked=technical_stage in {"ARMED","CONFIRMED"} and not effective_rr_ok
    pattern=formation["pattern"]

    if runway_blocked:
        stage="FORMING"
        reason="runway unknown" if not math.isfinite(runway_pct) else f"runway {runway_pct:.1f}% < {MIN_RUNWAY_PCT:.1f}%"
        pattern=f"{pattern}, {reason}" if pattern else reason
    elif rr_blocked:
        stage="FORMING"
        reason=f"effective R/R {effective_rr:.2f} < {MIN_EFFECTIVE_RR:.2f}"
        pattern=f"{pattern}, {reason}" if pattern else reason

    technical_score=formation["formation_score"]
    if rs20>=10: technical_score+=12
    elif rs20>=5: technical_score+=8
    elif rs20>=0: technical_score+=4

    ema50=_f(last["EMA50"])
    rsi=_f(last["RSI14"])
    if 50<=rsi<=70: technical_score+=6
    atr_pct=atr/price*100
    if 2<=atr_pct<=8: technical_score+=6
    if _f(last["RVOL"])>3.5: technical_score-=8
    if technical_stage=="EXTENDED": technical_score-=20
    if runway_blocked: technical_score-=12
    if rr_blocked: technical_score-=12
    if plan["entry_model"]=="PULLBACK_RETEST": technical_score+=3
    technical_score=max(0,min(100,round(technical_score,1)))

    risk_score=0
    if rsi>75: risk_score+=20
    if atr_pct>9: risk_score+=20
    if price<ema50: risk_score+=20
    if formation["extension_above_20d_high_pct"]>5: risk_score+=30
    if effective_rr<MIN_EFFECTIVE_RR: risk_score+=20
    if plan["risk_pct"]>5: risk_score+=15
    if runway_blocked: risk_score+=20
    risk_score=min(100,risk_score)

    if stage=="CONFIRMED" and effective_rr_ok and risk_score<=40 and runway_ok:
        decision="BUY / CONFIRMED"
    elif stage=="ARMED" and effective_rr_ok and runway_ok:
        decision="WAIT FOR RETEST" if plan["entry_condition"]=="TOUCH_AND_RECLAIM" else "WAIT FOR TRIGGER"
    elif stage in {"FORMING","DISCOVER"}:
        decision="WATCHLIST"
    else:
        decision="NO TRADE"

    return {
        "ticker":ticker,
        "price":round(price,2),
        "stage":stage,
        "technical_stage":technical_stage,
        "pattern":pattern,
        "formation_score":formation["formation_score"],
        "technical_score":technical_score,
        "risk_score":risk_score,
        "decision":decision,
        "market_regime_state":market_regime_state,
        "market_regime_score":round(market_regime_score,1),
        "market_regime_reason":str(market_regime.get("regime_reason","")),
        "rs20_vs_spy":round(rs20,2),
        "rsi14":round(rsi,1),
        "rvol":round(_f(last["RVOL"]),2),
        "atr_pct":round(atr_pct,2),
        "avg_dollar_volume":round(avg_dollar_volume,0),
        "ema20":round(_f(last["EMA20"]),2),
        "ema50":round(ema50,2),
        "ema200":round(_f(last["EMA200"]),2),
        "daily_support":round(levels["daily_support"],2),
        "daily_resistance":round(levels["daily_resistance"],2),
        "weekly_support":round(levels["weekly_support"],2),
        "weekly_resistance":round(levels["weekly_resistance"],2),
        "monthly_support":round(levels["monthly_support"],2),
        "monthly_resistance":round(levels["monthly_resistance"],2),
        "entry_trigger":round(entry,2),
        "entry_model":plan["entry_model"],
        "entry_condition":plan["entry_condition"],
        "entry_buffer_pct":round(plan["entry_buffer_pct"],2),
        "retest_reference":round(plan["retest_reference"],2) if math.isfinite(plan["retest_reference"]) else math.nan,
        "retest_distance_pct":round(plan["retest_distance_pct"],2) if math.isfinite(plan["retest_distance_pct"]) else math.nan,
        "retest_quality_score":int(plan.get("retest_quality_score",0)),
        "retest_quality_reasons":plan.get("retest_quality_reasons",""),
        "ema20_slope5_pct":round(plan.get("ema20_slope5_pct",math.nan),2) if math.isfinite(plan.get("ema20_slope5_pct",math.nan)) else math.nan,
        "support_touch_count":int(plan.get("support_touch_count",0)),
        "higher_low":bool(plan.get("higher_low",False)),
        "bullish_close":bool(plan.get("bullish_close",False)),
        "stop":round(stop,2),
        "stop_basis":plan["stop_basis"],
        "stop_anchor":round(plan["stop_anchor"],2),
        "stop_anchor_distance_pct":round(plan["stop_anchor_distance_pct"],2),
        "risk_pct":round(plan["risk_pct"],2),
        "swing5_low":round(plan["swing5_low"],2) if math.isfinite(plan["swing5_low"]) else math.nan,
        "swing10_low":round(plan["swing10_low"],2) if math.isfinite(plan["swing10_low"]) else math.nan,
        "target_5":round(plan["target5"],2),
        "target_8":round(plan["target8"],2),
        "target_10":round(plan["target10"],2),
        "rr_to_8pct":round(plan["rr8"],2),
        "next_higher_resistance":round(plan["next_res"],2) if math.isfinite(plan["next_res"]) else math.nan,
        "runway_to_next_resistance_pct":round(runway_pct,2) if math.isfinite(runway_pct) else math.nan,
        "runway_ok":runway_ok,
        "min_runway_required_pct":MIN_RUNWAY_PCT,
        "effective_target":round(plan["effective_target"],2),
        "effective_target_pct":round(plan["effective_target_pct"],2),
        "effective_rr":round(effective_rr,2),
        "effective_rr_ok":effective_rr_ok,
        "min_effective_rr_required":MIN_EFFECTIVE_RR,
        "distance_to_20d_high_pct":formation["distance_to_20d_high_pct"],
        "extension_above_20d_high_pct":formation["extension_above_20d_high_pct"],
    }
