from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import pandas as pd

from .catalyst_warehouse import catalyst_context, latest_catalyst_checks


class CatalystState(str,Enum):
    AVAILABLE="AVAILABLE"
    NO_EVENT="NO_EVENT"
    UNAVAILABLE="UNAVAILABLE"
    STALE="STALE"


@dataclass(frozen=True)
class CatalystResult:
    status: CatalystState
    ticker: str
    events: pd.DataFrame | None = None
    reason: str = ""
    checked_at: datetime | None = None


def catalyst_state(*, ticker: str, as_of: datetime, start_time: datetime,
                   max_check_age: timedelta) -> CatalystResult:
    symbol=str(ticker).upper()
    anchor=pd.Timestamp(as_of)
    if anchor.tzinfo is None:
        raise RuntimeError("CATALYST_STATE_ANCHOR_NAIVE")
    try:
        checks=latest_catalyst_checks(tickers=(symbol,),as_of=anchor.to_pydatetime())
    except Exception as exc:
        return CatalystResult(CatalystState.UNAVAILABLE,symbol,reason=f"CHECK_READ_FAILED:{type(exc).__name__}")
    if checks.empty:
        return CatalystResult(CatalystState.UNAVAILABLE,symbol,reason="NO_SUCCESSFUL_CHECK")
    latest=checks.sort_values("checked_at").iloc[-1]
    checked=pd.Timestamp(latest["checked_at"])
    age=anchor-checked
    if age > pd.Timedelta(max_check_age):
        return CatalystResult(CatalystState.STALE,symbol,reason=f"CHECK_AGE={age}",checked_at=checked.to_pydatetime())
    try:
        events=catalyst_context(tickers=(symbol,),as_of=anchor.to_pydatetime(),
                                start_time=start_time,end_time=anchor.to_pydatetime())
    except Exception as exc:
        return CatalystResult(CatalystState.UNAVAILABLE,symbol,reason=f"EVENT_READ_FAILED:{type(exc).__name__}",
                              checked_at=checked.to_pydatetime())
    if events.empty:
        if str(latest["result_status"])!="NO_EVENT":
            return CatalystResult(CatalystState.UNAVAILABLE,symbol,reason="CHECK_EVENT_MISMATCH",
                                  checked_at=checked.to_pydatetime())
        return CatalystResult(CatalystState.NO_EVENT,symbol,reason="FRESH_SUCCESSFUL_EMPTY_CHECK",
                              checked_at=checked.to_pydatetime())
    if str(latest["result_status"])!="EVENTS":
        return CatalystResult(CatalystState.UNAVAILABLE,symbol,events=events,reason="CHECK_EVENT_MISMATCH",
                              checked_at=checked.to_pydatetime())
    return CatalystResult(CatalystState.AVAILABLE,symbol,events=events,checked_at=checked.to_pydatetime())
