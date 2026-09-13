from __future__ import annotations

import math
import pandas as pd

from .config import (
    MIN_PRICE,
    MIN_AVG_SHARE_VOLUME,
    MIN_AVG_DOLLAR_VOLUME,
    MIN_MEDIAN_DOLLAR_VOLUME,
    MIN_ADR20_PCT,
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
    dollar_volume=(
        pd.to_numeric(window["Close"],errors="coerce") *
        pd.to_numeric(window["Volume"],errors="coerce")
    ).fillna(0)
    avg_dollar_volume=float(dollar_volume.mean())
    median_dollar_volume=float(dollar_volume.median())
    close=pd.to_numeric(window["Close"],errors="coerce").replace(0,float("nan"))
    daily_range_pct=(
        (pd.to_numeric(window["High"],errors="coerce") -
         pd.to_numeric(window["Low"],errors="coerce")) / close * 100
    )
    adr20_pct=float(daily_range_pct.replace([float("inf"),-float("inf")],float("nan")).dropna().mean())
    close_all=pd.to_numeric(d["Close"],errors="coerce").dropna()
    ret20_pct=float((close_all.iloc[-1]/close_all.iloc[-21]-1)*100) if len(close_all)>=21 and close_all.iloc[-21] else 0.0
    daily_returns_30=close_all.pct_change().tail(30)*100
    max_up_day_30d_pct=float(daily_returns_30.max()) if not daily_returns_30.dropna().empty else 0.0

    metrics=[price,avg_share_volume,avg_dollar_volume,median_dollar_volume,adr20_pct,ret20_pct,max_up_day_30d_pct]
    if not all(math.isfinite(x) for x in metrics):
        return None

    eligible=(
        price >= MIN_PRICE
        and avg_share_volume >= MIN_AVG_SHARE_VOLUME
        and avg_dollar_volume >= MIN_AVG_DOLLAR_VOLUME
        and median_dollar_volume >= MIN_MEDIAN_DOLLAR_VOLUME
        and adr20_pct >= MIN_ADR20_PCT
    )

    rejection_reasons=[]
    if price < MIN_PRICE:
        rejection_reasons.append("PRICE")
    if avg_share_volume < MIN_AVG_SHARE_VOLUME:
        rejection_reasons.append("SHARE_VOLUME")
    if avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
        rejection_reasons.append("AVG_DOLLAR_VOLUME")
    if median_dollar_volume < MIN_MEDIAN_DOLLAR_VOLUME:
        rejection_reasons.append("MEDIAN_DOLLAR_VOLUME")
    if adr20_pct < MIN_ADR20_PCT:
        rejection_reasons.append("LOW_DAILY_RANGE")

    return {
        "ticker":ticker,
        "price":round(price,2),
        "avg_share_volume20":round(avg_share_volume,0),
        "avg_dollar_volume20":round(avg_dollar_volume,0),
        "median_dollar_volume20":round(median_dollar_volume,0),
        "adr20_pct":round(adr20_pct,2),
        "ret20_pct":round(ret20_pct,2),
        "max_up_day_30d_pct":round(max_up_day_30d_pct,2),
        "tradable":bool(eligible),
        "rejection_reason":"|".join(rejection_reasons),
    }

def build_tradable_rows(histories: dict[str,pd.DataFrame]):
    rows=[]
    for ticker,hist in histories.items():
        row=evaluate_prefilter(ticker,hist)
        if row is not None:
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=[
            "ticker","price","avg_share_volume20","avg_dollar_volume20",
            "median_dollar_volume20","adr20_pct","ret20_pct","max_up_day_30d_pct","tradable","rejection_reason"
        ])
    return pd.DataFrame(rows)
