import math
import pandas as pd
from .indicators import add_indicators
from .patterns import detect_forming_setup, timeframe_levels

def _f(v, default=0.0):
    try:
        return float(v) if pd.notna(v) else default
    except Exception:
        return default

def analyze_dataframe(ticker: str, df: pd.DataFrame, benchmark_return20: float = 0.0):
    if df is None or len(df) < 220:
        return None
    d = add_indicators(df)
    last = d.iloc[-1]
    required = ["ATR14", "RSI14", "SMA200", "EMA50", "EMA200", "RVOL", "RET20"]
    if any(pd.isna(last[x]) for x in required):
        return None

    price = _f(last["Close"])
    atr = _f(last["ATR14"])
    avg_dollar_volume = _f((d["Close"] * d["Volume"]).tail(20).mean())
    if price <= 0 or atr <= 0:
        return None

    levels = timeframe_levels(d)
    formation = detect_forming_setup(d)
    rs20 = _f(last["RET20"]) - benchmark_return20

    breakout_trigger = max(price, levels["daily_resistance"] * 1.002)
    entry = price if formation["stage"] in {"CONFIRMED", "EXTENDED"} else breakout_trigger

    ema50 = _f(last["EMA50"])
    technical_supports = [x for x in [levels["daily_support"], ema50] if 0 < x < entry]
    nearest_support = max(technical_supports) if technical_supports else entry - 1.5 * atr
    stop = min(entry - 0.8 * atr, nearest_support - 0.25 * atr)
    stop = max(0.01, stop)
    risk = max(0.01, entry - stop)

    target5 = entry * 1.05
    target8 = entry * 1.08
    target10 = entry * 1.10
    rr8 = (target8 - entry) / risk

    higher_res = [x for x in [levels["weekly_resistance"], levels["monthly_resistance"]] if x > entry]
    next_res = min(higher_res) if higher_res else math.nan
    runway_pct = ((next_res / entry) - 1) * 100 if math.isfinite(next_res) else math.nan

    technical_score = formation["formation_score"]
    if rs20 >= 10: technical_score += 12
    elif rs20 >= 5: technical_score += 8
    elif rs20 >= 0: technical_score += 4
    rsi = _f(last["RSI14"])
    if 50 <= rsi <= 70: technical_score += 6
    atr_pct = atr / price * 100
    if 2 <= atr_pct <= 8: technical_score += 6
    if _f(last["RVOL"]) > 3.5: technical_score -= 8
    if formation["stage"] == "EXTENDED": technical_score -= 20
    technical_score = max(0, min(100, round(technical_score, 1)))

    risk_score = 0
    if rsi > 75: risk_score += 20
    if atr_pct > 9: risk_score += 20
    if price < ema50: risk_score += 20
    if formation["extension_above_20d_high_pct"] > 5: risk_score += 30
    if rr8 < 2: risk_score += 20
    risk_score = min(100, risk_score)

    if formation["stage"] == "CONFIRMED" and rr8 >= 2 and risk_score <= 40:
        decision = "BUY / CONFIRMED"
    elif formation["stage"] == "ARMED" and rr8 >= 2:
        decision = "WAIT FOR TRIGGER"
    elif formation["stage"] in {"FORMING", "DISCOVER"}:
        decision = "WATCHLIST"
    else:
        decision = "NO TRADE"

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "stage": formation["stage"],
        "pattern": formation["pattern"],
        "formation_score": formation["formation_score"],
        "technical_score": technical_score,
        "risk_score": risk_score,
        "decision": decision,
        "rs20_vs_spy": round(rs20, 2),
        "rsi14": round(rsi, 1),
        "rvol": round(_f(last["RVOL"]), 2),
        "atr_pct": round(atr_pct, 2),
        "avg_dollar_volume": round(avg_dollar_volume, 0),
        "ema20": round(_f(last["EMA20"]), 2),
        "ema50": round(ema50, 2),
        "ema200": round(_f(last["EMA200"]), 2),
        "daily_support": round(levels["daily_support"], 2),
        "daily_resistance": round(levels["daily_resistance"], 2),
        "weekly_support": round(levels["weekly_support"], 2),
        "weekly_resistance": round(levels["weekly_resistance"], 2),
        "monthly_support": round(levels["monthly_support"], 2),
        "monthly_resistance": round(levels["monthly_resistance"], 2),
        "entry_trigger": round(entry, 2),
        "stop": round(stop, 2),
        "target_5": round(target5, 2),
        "target_8": round(target8, 2),
        "target_10": round(target10, 2),
        "rr_to_8pct": round(rr8, 2),
        "next_higher_resistance": round(next_res, 2) if math.isfinite(next_res) else math.nan,
        "runway_to_next_resistance_pct": round(runway_pct, 2) if math.isfinite(runway_pct) else math.nan,
        "distance_to_20d_high_pct": formation["distance_to_20d_high_pct"],
        "extension_above_20d_high_pct": formation["extension_above_20d_high_pct"],
    }
