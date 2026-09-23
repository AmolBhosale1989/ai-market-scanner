"""Reconstruct only the latest completed daily candle from complete observed bars."""
import pandas as pd
import pandas_market_calendars as mcal

from .ohlcv_quality import invalid_rows


def repair_daily(daily, intraday, now=None, interval="5m"):
    if interval not in {"5m","30m","60m"}:
        raise ValueError("Unsupported daily reconstruction interval")
    out=daily.copy()
    now=pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    schedule=mcal.get_calendar("NYSE").schedule(
        start_date=(now-pd.Timedelta(days=10)).date(),end_date=now.date())
    completed=schedule[pd.to_datetime(schedule.market_close,utc=True)<now]
    if completed.empty or intraday.empty:
        return out
    session=completed.iloc[-1]
    day=pd.Timestamp(completed.index[-1]).date()
    expected=pd.date_range(session.market_open,session.market_close,freq=interval.replace("m","min"),inclusive="left")
    bars=intraday.copy()
    bars.index=pd.to_datetime(bars.index,utc=True)
    bars=bars.loc[(bars.index>=expected[0]) & (bars.index<session.market_close)].sort_index()
    # Every expected bar, including the opening and closing bar, must exist once.
    if not bars.index.equals(expected):
        return out
    names={c:c.lower() for c in ("Open","High","Low","Close","Volume")}
    if not set(names).issubset(bars.columns) or invalid_rows(bars.rename(columns=names)).any():
        return out
    bad=invalid_rows(out.rename(columns=names))
    for idx in out.index[bad]:
        if pd.Timestamp(idx).date()!=day:
            continue
        out.loc[idx,["Open","High","Low","Close","Volume"]]=[
            bars.Open.iloc[0],bars.High.max(),bars.Low.min(),bars.Close.iloc[-1],bars.Volume.sum()]
        out.loc[idx,"daily_bar_source"]=f"COMPLETE_REGULAR_SESSION_{interval.upper()}"
        out.loc[idx,"source_timeframe"]=interval
        out.loc[idx,"source_bar_count"]=len(bars)
        out.loc[idx,"source_first_bar_utc"]=expected[0].isoformat()
        out.loc[idx,"source_last_bar_utc"]=expected[-1].isoformat()
    return out
