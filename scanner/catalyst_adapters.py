from __future__ import annotations

from abc import ABC,abstractmethod
from dataclasses import dataclass,field
from datetime import datetime
from typing import Any,Mapping,Sequence


@dataclass(frozen=True)
class NormalizedCatalystEvent:
    provider_event_id: str
    catalyst_type: str
    event_timestamp: datetime
    payload: Mapping[str,Any]=field(default_factory=dict)

    def __post_init__(self):
        if not str(self.provider_event_id).strip():
            raise ValueError("provider_event_id is required")
        if not str(self.catalyst_type).strip():
            raise ValueError("catalyst_type is required")
        if self.event_timestamp.tzinfo is None:
            raise ValueError("event_timestamp must be timezone-aware")


@dataclass(frozen=True)
class CatalystFetchResult:
    events: tuple[NormalizedCatalystEvent,...]
    rejected_count: int=0

    def __post_init__(self):
        if self.rejected_count < 0:
            raise ValueError("rejected_count must be nonnegative")


class CatalystProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def fetch_catalysts(self,ticker: str,*,anchor: datetime) -> CatalystFetchResult:
        """Raise on provider failure; use only the injected immutable knowledge anchor."""
        ...


class AlphaVantageCalendarAdapter(CatalystProviderAdapter):
    """Canonicalizes the repository's existing EARNINGS_CALENDAR CSV response."""

    provider_name="ALPHA_VANTAGE"

    def __init__(self, fetch_calendar):
        # Injection keeps HTTP/auth/quota behavior independently testable.
        self._fetch_calendar=fetch_calendar

    def fetch_catalysts(self,ticker: str,*,anchor: datetime) -> CatalystFetchResult:
        import csv
        from io import StringIO
        import pandas as pd
        body=str(self._fetch_calendar() or "").strip()
        if not body:
            raise RuntimeError("ALPHA_VANTAGE_EMPTY_RESPONSE")
        if body.startswith("{"):
            raise RuntimeError("ALPHA_VANTAGE_SOFT_ERROR_RESPONSE")
        try:
            rows=list(csv.DictReader(StringIO(body)))
        except Exception as exc:
            raise RuntimeError("ALPHA_VANTAGE_CALENDAR_PARSE_FAILED") from exc
        wanted=str(ticker).upper()
        events=[]
        rejected=0
        seen=set()
        for row in rows:
            symbol=str(row.get("symbol") or row.get("Symbol") or row.get("ticker") or row.get("Ticker") or "").upper()
            if symbol != wanted:
                continue
            raw_date=row.get("reportDate") or row.get("report_date") or row.get("date") or row.get("Date")
            fiscal=str(row.get("fiscalDateEnding") or "").strip()
            if not raw_date:
                rejected+=1
                continue
            try:
                ts=pd.Timestamp(raw_date)
                ts=ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            except Exception:
                rejected+=1
                continue
            # Stable identity represents the scheduled earnings fact; date changes
            # become payload revisions when fiscal identity is available.
            identity=f"earnings:{wanted}:{fiscal}" if fiscal else f"earnings:{wanted}:{ts.date().isoformat()}"
            if identity in seen:
                rejected+=1
                continue
            seen.add(identity)
            events.append(NormalizedCatalystEvent(
                provider_event_id=identity,catalyst_type="UPCOMING_EARNINGS",
                event_timestamp=ts.to_pydatetime(),payload={
                    "scheduled_for_utc":ts.isoformat(),
                    "fiscal_date_ending":fiscal,
                    "eps_estimate":row.get("estimate"),
                    "currency":row.get("currency"),
                    "report_time":row.get("timeOfTheDay"),
                }))
        return CatalystFetchResult(tuple(events),rejected)


class AlphaVantageCalendarBatch:
    """One fetched calendar, indexed once, then resolved locally per ticker."""

    provider_name="ALPHA_VANTAGE"

    def __init__(self,fetch_calendar):
        self._fetch_calendar=fetch_calendar

    def fetch_and_index(self,*,anchor: datetime,lookforward) -> dict[str,CatalystFetchResult]:
        import csv
        from io import StringIO
        import pandas as pd
        body=str(self._fetch_calendar() or "").strip()
        if not body:
            raise RuntimeError("ALPHA_VANTAGE_EMPTY_RESPONSE")
        if body.startswith("{"):
            raise RuntimeError("ALPHA_VANTAGE_SOFT_ERROR_RESPONSE")
        try:
            rows=list(csv.DictReader(StringIO(body)))
        except Exception as exc:
            raise RuntimeError("ALPHA_VANTAGE_CALENDAR_PARSE_FAILED") from exc
        anchor_ts=pd.Timestamp(anchor)
        if anchor_ts.tzinfo is None:
            raise RuntimeError("ALPHA_VANTAGE_ANCHOR_NAIVE")
        end=anchor_ts+pd.Timedelta(lookforward)
        buckets={}
        rejected={}
        seen={}
        observed=set()
        for row in rows:
            ticker=str(row.get("symbol") or row.get("Symbol") or row.get("ticker") or row.get("Ticker") or "").strip().upper().replace(".","-")
            if not ticker:
                continue
            observed.add(ticker)
            raw_date=row.get("reportDate") or row.get("report_date") or row.get("date") or row.get("Date")
            fiscal=str(row.get("fiscalDateEnding") or "").strip()
            try:
                ts=pd.Timestamp(raw_date)
                ts=ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            except Exception:
                rejected[ticker]=rejected.get(ticker,0)+1
                continue
            if not (anchor_ts <= ts <= end):
                continue
            identity=f"earnings:{ticker}:{fiscal}" if fiscal else f"earnings:{ticker}:{ts.date().isoformat()}"
            ids=seen.setdefault(ticker,set())
            if identity in ids:
                rejected[ticker]=rejected.get(ticker,0)+1
                continue
            ids.add(identity)
            buckets.setdefault(ticker,[]).append(NormalizedCatalystEvent(
                identity,"UPCOMING_EARNINGS",ts.to_pydatetime(),{
                    "scheduled_for_utc":ts.isoformat(),"fiscal_date_ending":fiscal,
                    "eps_estimate":row.get("estimate"),"currency":row.get("currency"),
                    "report_time":row.get("timeOfTheDay")}))
        return {ticker:CatalystFetchResult(tuple(buckets.get(ticker,())),rejected.get(ticker,0))
                for ticker in observed}
