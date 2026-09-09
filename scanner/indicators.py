import pandas as pd
import numpy as np

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = -delta.clip(upper=0).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev = close.shift(1)
    tr = pd.concat([(high-low), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def macd(close: pd.Series):
    fast = ema(close, 12)
    slow = ema(close, 26)
    line = fast - slow
    signal = ema(line, 9)
    return line, signal

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["EMA9"] = ema(d["Close"], 9)
    d["EMA20"] = ema(d["Close"], 20)
    d["EMA50"] = ema(d["Close"], 50)
    d["SMA200"] = d["Close"].rolling(200).mean()
    d["RSI14"] = rsi(d["Close"], 14)
    d["ATR14"] = atr(d, 14)
    d["AVG_VOL20"] = d["Volume"].rolling(20).mean()
    d["RVOL"] = d["Volume"] / d["AVG_VOL20"]
    d["MACD"], d["MACD_SIGNAL"] = macd(d["Close"])
    return d
