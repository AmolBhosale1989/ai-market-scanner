"""Intraday metrics use observed exchange timestamps, never row-count clocks."""
from __future__ import annotations

import math

import pandas as pd


def close_return_30m(today: pd.DataFrame) -> float:
    """Compare observed closes exactly 30 minutes apart in the same session.

    Five-minute bars label their start, so their close times are also exactly
    30 minutes apart when their start times are. No nearest-bar substitution,
    forward fill, previous-session price or shortened opening window is used.
    Missing endpoints mean the indicator is not ready, not zero movement.
    """
    if today.empty or "Close" not in today:
        return math.nan
    frame = today.sort_index()
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None or index.hasnans or index.has_duplicates:
        return math.nan
    end = index[-1]
    start = end - pd.Timedelta(minutes=30)
    if start.date() != end.date() or start not in index:
        return math.nan
    try:
        first, last = float(frame.loc[start, "Close"]), float(frame.iloc[-1]["Close"])
    except (ValueError, TypeError):
        return math.nan
    if not all(math.isfinite(x) and x > 0 for x in (first, last)):
        return math.nan
    return (last / first - 1) * 100


def same_clock_rvol(d: pd.DataFrame, today: pd.DataFrame, session_date, *, sessions: int) -> float:
    """Compare cumulative volume through the same NY clock time on each day.

    Missing trade buckets do not move the comparison cutoff earlier or later.
    Only observed volume is summed; absent observations are not manufactured.
    """
    if today.empty or "Volume" not in today or "Volume" not in d:
        return math.nan
    current = today.sort_index().tz_convert("America/New_York")
    history = d.sort_index().tz_convert("America/New_York")
    cutoff = current.index[-1].time()
    cur = float(pd.to_numeric(current["Volume"], errors="coerce").sum(min_count=1))
    prior_dates = sorted({x for x in history.index.date if x < session_date}, reverse=True)[:sessions]
    comps = []
    for date in prior_dates:
        prior = history[history.index.date == date].between_time("09:30", cutoff)
        volume = float(pd.to_numeric(prior["Volume"], errors="coerce").sum(min_count=1))
        if math.isfinite(volume) and volume > 0:
            comps.append(volume)
    if not comps or not math.isfinite(cur) or cur <= 0:
        return math.nan
    return cur / (sum(comps) / len(comps))
