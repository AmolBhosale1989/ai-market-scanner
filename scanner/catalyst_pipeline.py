from __future__ import annotations

import os
from datetime import timedelta

import requests

from .catalyst_adapters import AlphaVantageCalendarBatch
from .catalyst_existing_adapters import ExistingSecEdgarAdapter,ExistingYahooNewsAdapter
from .catalyst_ingestion import ingest_alpha_vantage_batch,ingest_ticker
from .consumer_snapshot import consumer_anchor
from .control_plane import read_dataset


REQUIRED_PROVIDERS=("YAHOO_NEWS","SEC_EDGAR","ALPHA_VANTAGE")
PROVIDER_MAX_AGE={"YAHOO_NEWS":timedelta(minutes=15),"SEC_EDGAR":timedelta(minutes=15),
                  "ALPHA_VANTAGE":timedelta(hours=24)}


def _alpha_calendar_fetch():
    key=os.getenv("ALPHA_VANTAGE_API_KEY","").strip()
    if not key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY_MISSING")
    response=requests.get("https://www.alphavantage.co/query",params={
        "function":"EARNINGS_CALENDAR","horizon":"3month","apikey":key},timeout=20)
    response.raise_for_status()
    return response.text


def run() -> dict:
    universe=read_dataset("live_universe")
    if universe.empty or "ticker" not in universe.columns:
        raise RuntimeError("CATALYST_LIVE_UNIVERSE_EMPTY")
    tickers=list(dict.fromkeys(universe["ticker"].dropna().astype(str).str.upper()))
    anchor=consumer_anchor()
    if anchor is None:
        raise RuntimeError("CATALYST_CONTEXT_ANCHOR_REQUIRED")

    yahoo=ExistingYahooNewsAdapter()
    sec=ExistingSecEdgarAdapter()
    failures=[]
    for ticker in tickers:
        for adapter in (yahoo,sec):
            try:
                ingest_ticker(adapter,ticker,anchor=anchor)
            except Exception as exc:
                detail=f"{adapter.provider_name}:{ticker}:{type(exc).__name__}:{exc}"
                failures.append(detail)
                print("CATALYST_PROVIDER_FAILURE "+detail,flush=True)

    try:
        ingest_alpha_vantage_batch(AlphaVantageCalendarBatch(_alpha_calendar_fetch),tickers,
                                   anchor=anchor,lookforward=timedelta(days=14))
    except Exception as exc:
        detail=f"ALPHA_VANTAGE:BATCH:{type(exc).__name__}:{exc}"
        failures.append(detail)
        print("CATALYST_PROVIDER_FAILURE "+detail,flush=True)

    try:
        verify_coverage(tickers,anchor=anchor)
    except RuntimeError as exc:
        if failures:
            print(f"CATALYST_PROVIDER_FAILURES count={len(failures)} sample={failures[:10]!r}",flush=True)
        raise
    result={"tickers":len(tickers),"providers":len(REQUIRED_PROVIDERS),
            "required_checks":len(tickers)*len(REQUIRED_PROVIDERS)}
    print("CATALYST_INGESTION_PASS "+str(result))
    return result


def verify_coverage(tickers,*,anchor):
    from .database import connection
    wanted=list(dict.fromkeys(str(x).upper() for x in tickers if x))
    with connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT ticker,provider,max(checked_at)
                       FROM catalyst_check
                       WHERE ticker=ANY(%s) AND checked_at<=%s
                         AND result_status IN ('EVENTS','NO_EVENT')
                         AND rejected_count=0
                       GROUP BY ticker,provider""",(wanted,anchor))
        rows=cur.fetchall()
        present={(ticker,provider) for ticker,provider,checked_at in rows
                 if anchor-checked_at <= PROVIDER_MAX_AGE[provider]}
    required={(ticker,provider) for ticker in wanted for provider in REQUIRED_PROVIDERS}
    missing=required-present
    if missing:
        sample=",".join(f"{t}:{p}" for t,p in sorted(missing)[:10])
        raise RuntimeError(f"CATALYST_COVERAGE_INCOMPLETE missing={len(missing)} sample={sample}")
    return True


def main():
    return run()


if __name__=="__main__":
    main()
