from .warehouse import history as download_history
from .indicators import add_indicators

def sector_score(etf: str):
    df = download_history(etf, "6mo", "1d")
    if len(df) < 30:
        return None
    d = add_indicators(df)
    last = d.iloc[-1]
    ret5 = (last["Close"] / d.iloc[-6]["Close"] - 1) * 100
    ret20 = (last["Close"] / d.iloc[-21]["Close"] - 1) * 100
    score = 50 + ret5 * 4 + ret20 * 1.5
    if last["Close"] > last["EMA20"]: score += 10
    if last["EMA20"] > last["EMA50"]: score += 10
    return max(0, min(100, round(score, 1)))
