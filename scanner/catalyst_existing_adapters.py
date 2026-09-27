from __future__ import annotations

from datetime import datetime,timezone
from typing import Iterable

import pandas as pd

from .catalyst_adapters import CatalystFetchResult,NormalizedCatalystEvent
from .v4.catalysts import SecFilingAdapter,YahooNewsCatalystAdapter


def _normalized(events,provider: str) -> CatalystFetchResult:
    output=[]
    rejected=0
    seen=set()
    for event in events:
        try:
            event_id=str(event.event_id).strip()
            payload={**dict(event.payload),"_canonical_ticker":str(event.ticker).upper()}
            ts=pd.Timestamp(payload.get("source_timestamp_utc") or event.observed_at_utc)
            if not event_id or ts.tzinfo is None:
                raise ValueError("identity/time missing")
            catalyst_type=str(payload.get("catalyst_type") or "CATALYST")
            if event_id in seen:
                raise ValueError("duplicate event")
            seen.add(event_id)
            output.append(NormalizedCatalystEvent(event_id,catalyst_type,ts.tz_convert("UTC").to_pydatetime(),payload))
        except Exception:
            rejected+=1
    return CatalystFetchResult(tuple(output),rejected)


class ExistingYahooNewsAdapter:
    provider_name="YAHOO_NEWS"
    def __init__(self,delegate=None,max_workers=8,max_tickers=10000):
        self.delegate=delegate or YahooNewsCatalystAdapter(max_workers=max_workers,max_tickers=max_tickers)

    def fetch_catalysts(self,ticker: str,*,anchor: datetime) -> CatalystFetchResult:
        frame=pd.DataFrame([{"ticker":str(ticker).upper(),"company_name":""}])
        events,health=self.delegate.poll(frame,now=anchor)
        if int(health.get("errors",0) or 0)>0:
            raise RuntimeError("YAHOO_NEWS_PROVIDER_FAILED")
        return _normalized(events,self.provider_name)

    def fetch_batch(self,tickers,*,anchor: datetime):
        frame=pd.DataFrame([{"ticker":str(t).upper(),"company_name":""} for t in tickers])
        events,health=self.delegate.poll(frame,now=anchor)
        normalized=_normalized(events,self.provider_name)
        return normalized,health


class ExistingSecEdgarAdapter:
    provider_name="SEC_EDGAR"
    def __init__(self,delegate=None,max_workers=5):
        self.delegate=delegate or SecFilingAdapter(max_workers=max_workers)

    def fetch_catalysts(self,ticker: str,*,anchor: datetime) -> CatalystFetchResult:
        frame=pd.DataFrame([{"ticker":str(ticker).upper()}])
        events,health=self.delegate.poll(frame,now=anchor)
        if int(health.get("unresolved",0) or 0)>0 or int(health.get("errors",0) or 0)>0:
            raise RuntimeError(f"SEC_EDGAR_PROVIDER_FAILED ticker={str(ticker).upper()} health={health!r}")
        return _normalized(events,self.provider_name)

    def fetch_batch(self,tickers,*,anchor: datetime):
        frame=pd.DataFrame([{"ticker":str(t).upper()} for t in tickers])
        events,health=self.delegate.poll(frame,now=anchor)
        normalized=_normalized(events,self.provider_name)
        return normalized,health
