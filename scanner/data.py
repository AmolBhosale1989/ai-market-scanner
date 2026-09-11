import time
from typing import Iterable
import pandas as pd
import yfinance as yf

def download_history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False, threads=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df.dropna(how="all").copy()

def download_batch(tickers: Iterable[str], period: str = "1y", interval: str = "1d", retries: int = 2):
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return {}
    err = None
    for attempt in range(retries + 1):
        try:
            raw = yf.download(
                tickers=tickers, period=period, interval=interval,
                auto_adjust=True, progress=False, group_by="ticker", threads=True,
            )
            if raw.empty:
                return {}
            out = {}
            if len(tickers) == 1:
                df = raw.copy()
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[-1] for c in df.columns]
                out[tickers[0]] = df.dropna(how="all")
                return out
            for t in tickers:
                try:
                    df = raw[t].copy().dropna(how="all")
                    if not df.empty:
                        out[t] = df
                except Exception:
                    continue
            return out
        except Exception as e:
            err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    if err:
        print(f"Batch download failed: {err}")
    return {}
