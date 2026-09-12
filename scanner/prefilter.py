from __future__ import annotations

import math
import pandas as pd

from .config import (
    MIN_PRICE,
    MIN_AVG_SHARE_VOLUME,
    MIN_AVG_DOLLAR_VOLUME,
    PREFILTER_MIN_BARS,
    PREFILTER_AVG_WINDOW,
)

def evaluate_prefilter(ticker: str, hist: pd.DataFrame):
    if hist is None or hist.empty or len(hist) < PREFILTER_MIN_BARS:
        return None

    d=hist.dropna(subset=["Close","Volume"]).copy()
    if len(d) < PREFILTER_MIN_BARS:
        return None

    price=float(d["Close"].iloc[-1])
    window=d.tail(PREFILTER_AVG_WINDOW)
    avg_share_volume=float(pd.to_numeric(window["Volume"],errors="coerce").fillna(0).mean())
    avg_dollar_volume=float(
        (pd.to_numeric(window["Close"],errors="coerce") *
         pd.to_numeric(window["Volume"],errors="coerce")).fillna(0).mean()
    )

    if not all(math.isfinite(x) for x in [price,avg_share_volume,avg_dollar_volume]):
        return None

    eligible=(
        price >= MIN_PRICE
        and avg_share_volume >= MIN_AVG_SHARE_VOLUME
        and avg_dollar_volume >= MIN_AVG_DOLLAR_VOLUME
    )

    return {
        "ticker":ticker,
        "price":round(price,2),
        "avg_share_volume20":round(avg_share_volume,0),
        "avg_dollar_volume20":round(avg_dollar_volume,0),
        "tradable":bool(eligible),
    }

def build_tradable_rows(histories: dict[str,pd.DataFrame]):
    rows=[]
    for ticker,hist in histories.items():
        row=evaluate_prefilter(ticker,hist)
        if row is not None:
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=[
            "ticker","price","avg_share_volume20","avg_dollar_volume20","tradable"
        ])
    return pd.DataFrame(rows)
