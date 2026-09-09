import pandas as pd
from .data import download_history
from .indicators import add_indicators
from .scoring import score_stock, risk_score, decision

def analyze_stock(ticker: str):
    df = download_history(ticker, "1y", "1d")
    if len(df) < 220:
        return None

    d = add_indicators(df)
    last = d.iloc[-1]

    if pd.isna(last["ATR14"]) or pd.isna(last["RSI14"]) or pd.isna(last["SMA200"]):
        return None

    price = float(last["Close"])
    avg_dollar_volume = float((d["Close"] * d["Volume"]).tail(20).mean())

    opp = score_stock(last)
    risk = risk_score(last)
    atr = float(last["ATR14"])
    stop = max(0.01, price - 1.5 * atr)

    return {
        "ticker": ticker,
        "price": round(price, 2),
        "opportunity_score": opp,
        "risk_score": risk,
        "decision": decision(opp, risk),
        "rsi14": round(float(last["RSI14"]), 1),
        "rvol": round(float(last["RVOL"]), 2),
        "atr_pct": round((atr / price) * 100, 2),
        "avg_dollar_volume": round(avg_dollar_volume, 0),
        "entry": round(price, 2),
        "stop": round(stop, 2),
        "target_10": round(price * 1.10, 2),
        "target_15": round(price * 1.15, 2),
    }
