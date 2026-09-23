"""Stable daily-session boundary; publication freshness always uses real time."""
import os
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal


@lru_cache(maxsize=16)
def _run_started_at(run_id: str) -> pd.Timestamp:
    from .bitemporal_warehouse import _connect
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT started_at FROM pipeline_run WHERE pipeline_run_id=%s", (run_id,))
        row = cur.fetchone()
    if not row or not row[0]:
        raise RuntimeError("MARKET_CUTOFF_UNAVAILABLE: production run missing")
    return pd.Timestamp(row[0])


def daily_clock() -> pd.Timestamp:
    run_id = os.getenv("PRODUCTION_RUN_ID", "").strip()
    return _run_started_at(run_id) if run_id else pd.Timestamp.now(tz="UTC")


def completed_daily_session(at=None):
    at = pd.Timestamp(at if at is not None else daily_clock())
    if at.tzinfo is None:
        raise RuntimeError("MARKET_CUTOFF_INVALID: timezone required")
    at = at.tz_convert("UTC")
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date=(at - pd.Timedelta(days=10)).date(), end_date=at.date())
    completed = schedule[pd.to_datetime(schedule["market_close"], utc=True) < at]
    if completed.empty:
        raise RuntimeError("MARKET_CUTOFF_UNAVAILABLE: no completed session")
    return pd.Timestamp(completed.index[-1]).date()


def event_cutoff(timeframe, as_of):
    if timeframe != "1d":
        return as_of
    # Daily timestamps label a session date in UTC, not a NYSE bar-end time.
    end = pd.Timestamp(completed_daily_session(min(daily_clock(), pd.Timestamp(as_of))), tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return min(pd.Timestamp(as_of), end)
