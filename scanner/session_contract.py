from __future__ import annotations

from datetime import date

import pandas as pd
import pandas_market_calendars as mcal


def latest_frame_session(frame: pd.DataFrame, fallback: date | None = None) -> date:
    """Return the newest market-data session represented by a warehouse frame."""
    if frame is not None and not frame.empty:
        index = pd.DatetimeIndex(frame.index)
        if index.tz is None:
            index = index.tz_localize("UTC")
        return max(index.tz_convert("America/New_York").date)
    if fallback is not None:
        return fallback
    raise RuntimeError("MARKET_SESSION_UNAVAILABLE: warehouse frame has no session")


def expected_market_data_session(now_utc: pd.Timestamp | None = None) -> date:
    """Return the NYSE session production data should currently represent."""
    now = now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC")
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date=(now - pd.Timedelta(days=10)).date(),
        end_date=(now + pd.Timedelta(days=1)).date(),
    )
    eligible = schedule[pd.to_datetime(schedule["market_open"], utc=True) <= now]
    if eligible.empty:
        raise RuntimeError("MARKET_SESSION_UNAVAILABLE: cannot resolve expected NYSE session")
    return pd.Timestamp(eligible.index[-1]).date()
