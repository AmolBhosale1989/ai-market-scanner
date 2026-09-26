from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime,timedelta
from enum import Enum
from typing import Mapping

import pandas as pd

from .catalyst_warehouse import catalyst_context,latest_catalyst_checks


class CatalystState(str,Enum):
    AVAILABLE="AVAILABLE"
    NO_EVENT="NO_EVENT"
    UNAVAILABLE="UNAVAILABLE"
    STALE="STALE"


@dataclass(frozen=True)
class ProviderRequirement:
    provider: str
    max_check_age: timedelta


@dataclass(frozen=True)
class CatalystResult:
    status: CatalystState
    ticker: str
    events: pd.DataFrame | None=None
    reason: str=""
    provider_states: Mapping[str,str] | None=None


def resolve_catalyst_state(*,ticker: str,as_of: datetime,start_time: datetime,
                           requirements: tuple[ProviderRequirement,...]) -> CatalystResult:
    symbol=str(ticker).upper()
    anchor=pd.Timestamp(as_of)
    if anchor.tzinfo is None:
        raise RuntimeError("CATALYST_STATE_ANCHOR_NAIVE")
    states={}
    frames=[]
    for req in requirements:
        try:
            checks=latest_catalyst_checks(tickers=(symbol,),as_of=anchor.to_pydatetime())
            check=checks[checks["provider"].astype(str).eq(req.provider)]
        except Exception as exc:
            return CatalystResult(CatalystState.UNAVAILABLE,symbol,reason=f"[{req.provider}] CHECK_READ_FAILED:{type(exc).__name__}",provider_states=states)
        if check.empty:
            return CatalystResult(CatalystState.UNAVAILABLE,symbol,reason=f"[{req.provider}] NO_SUCCESSFUL_CHECK",provider_states=states)
        row=check.sort_values("checked_at").iloc[-1]
        if int(row.get("rejected_count",0) or 0)>0 or str(row["result_status"])=="PROVIDER_PAYLOAD_REJECTED":
            states[req.provider]="UNAVAILABLE"
            return CatalystResult(CatalystState.UNAVAILABLE,symbol,
                reason=f"[{req.provider}] PROVIDER_PAYLOAD_REJECTED:{int(row.get('rejected_count',0) or 0)}",
                provider_states=states)
        age=anchor-pd.Timestamp(row["checked_at"])
        if age>pd.Timedelta(req.max_check_age):
            states[req.provider]="STALE"
            return CatalystResult(CatalystState.STALE,symbol,reason=f"[{req.provider}] CHECK_AGE={age}",provider_states=states)
        try:
            events=catalyst_context(tickers=(symbol,),as_of=anchor.to_pydatetime(),
                                    start_time=start_time,end_time=anchor.to_pydatetime())
            events=events[events["provider"].astype(str).eq(req.provider)] if not events.empty else events
        except Exception as exc:
            return CatalystResult(CatalystState.UNAVAILABLE,symbol,reason=f"[{req.provider}] EVENT_READ_FAILED:{type(exc).__name__}",provider_states=states)
        outcome=str(row["result_status"])
        verified={str(x) for x in (row.get("verified_event_ids") or [])}
        if outcome=="NO_EVENT":
            if int(row.get("event_count",0) or 0)!=0 or verified:
                states[req.provider]="UNAVAILABLE"
                return CatalystResult(CatalystState.UNAVAILABLE,symbol,
                    reason=f"[{req.provider}] CHECK_EVENT_MISMATCH:NO_EVENT_MANIFEST",provider_states=states)
            # Historical PIT events may legitimately remain strategy-relevant
            # even though this particular successful fetch returned no new event.
            states[req.provider]="NO_EVENT"
            if not events.empty:
                frames.append(events)
            continue
        pit_ids=set(events["provider_event_id"].astype(str)) if not events.empty else set()
        missing=verified-pit_ids
        if outcome!="EVENTS" or not verified or int(row.get("event_count",0) or 0)!=len(verified) or missing:
            states[req.provider]="UNAVAILABLE"
            detail=",".join(sorted(missing)[:5]) if missing else "MANIFEST_INVALID"
            return CatalystResult(CatalystState.UNAVAILABLE,symbol,
                reason=f"[{req.provider}] CHECK_EVENT_MISMATCH:{detail}",provider_states=states)
        states[req.provider]="AVAILABLE"
        frames.append(events)
    if frames:
        return CatalystResult(CatalystState.AVAILABLE,symbol,events=pd.concat(frames,ignore_index=True),
                              provider_states=states)
    return CatalystResult(CatalystState.NO_EVENT,symbol,reason="ALL_REQUIRED_PROVIDERS_FRESH_NO_EVENT",
                          provider_states=states)
