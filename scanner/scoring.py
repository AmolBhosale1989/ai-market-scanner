def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))

def score_stock(row):
    score = 0
    if row["Close"] > row["EMA20"]: score += 10
    if row["EMA20"] > row["EMA50"]: score += 10
    if row["Close"] > row["SMA200"]: score += 10

    rsi = row["RSI14"]
    if 55 <= rsi <= 70:
        score += 20
    elif 50 <= rsi < 55 or 70 < rsi <= 75:
        score += 12
    elif 40 <= rsi < 50:
        score += 5

    if row["MACD"] > row["MACD_SIGNAL"]:
        score += 15

    rvol = row["RVOL"]
    if rvol >= 2:
        score += 20
    elif rvol >= 1.5:
        score += 15
    elif rvol >= 1.2:
        score += 10
    elif rvol >= 1:
        score += 5

    atr_pct = (row["ATR14"] / row["Close"]) * 100 if row["Close"] else 0
    if 3 <= atr_pct <= 8:
        score += 15
    elif 2 <= atr_pct < 3 or 8 < atr_pct <= 10:
        score += 10
    elif 1 <= atr_pct < 2:
        score += 5

    return clamp(score)

def risk_score(row):
    risk = 0
    rsi = row["RSI14"]
    rvol = row["RVOL"]
    atr_pct = (row["ATR14"] / row["Close"]) * 100 if row["Close"] else 0

    if rsi > 75: risk += 25
    if atr_pct > 8: risk += 25
    if rvol > 3: risk += 15
    if row["Close"] < row["EMA20"]: risk += 20
    if row["Close"] < row["SMA200"]: risk += 15

    return clamp(risk)

def decision(opportunity, risk):
    if opportunity >= 80 and risk <= 35:
        return "BUY / CONFIRMED"
    if opportunity >= 70 and risk <= 50:
        return "WAIT FOR ENTRY"
    if opportunity >= 60:
        return "WATCHLIST"
    return "NO TRADE"
