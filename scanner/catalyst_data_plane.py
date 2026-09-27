from __future__ import annotations

import os
from datetime import datetime,timedelta,timezone

import requests

from .catalyst_adapters import AlphaVantageCalendarBatch
from .catalyst_existing_adapters import ExistingSecEdgarAdapter,ExistingYahooNewsAdapter
from .catalyst_ingestion import ingest_alpha_vantage_batch,persist_batch_result
from .control_plane import read_dataset


def _alpha_calendar_fetch():
    key=os.getenv("ALPHA_VANTAGE_API_KEY","").strip()
    if not key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY_MISSING")
    response=requests.get("https://www.alphavantage.co/query",params={
        "function":"EARNINGS_CALENDAR","horizon":"3month","apikey":key},timeout=20)
    response.raise_for_status()
    return response.text


def _universe():
    frame=read_dataset("live_universe",published_mode="production",required=False)
    if frame.empty or "ticker" not in frame.columns:
        raise RuntimeError("CATALYST_PUBLISHED_UNIVERSE_EMPTY")
    return list(dict.fromkeys(frame["ticker"].dropna().astype(str).str.upper()))


def run() -> dict:
    """Independent data-plane worker. Network time is never a live-stage dependency."""
    tickers=_universe()
    checked_at=datetime.now(timezone.utc)
    failures=[]

    yahoo=ExistingYahooNewsAdapter(max_workers=8,max_tickers=len(tickers))
    try:
        result,health=yahoo.fetch_batch(tickers,anchor=checked_at)
        successful=list(dict.fromkeys(str(x).upper() for x in health.get("successful_tickers",[])))
        persist_batch_result(yahoo,successful,result,anchor=checked_at)
        failed=list(dict.fromkeys(str(x).upper() for x in health.get("failed_tickers",[])))
        if failed:
            failures.append(f"YAHOO_NEWS_FAILED count={len(failed)} sample={failed[:20]!r}")
    except Exception as exc:
        failures.append(f"YAHOO_NEWS:{type(exc).__name__}:{exc}")

    sec=ExistingSecEdgarAdapter(max_workers=5)
    try:
        result,health=sec.fetch_batch(tickers,anchor=checked_at)
        unresolved=set(health.get("unresolved_tickers") or [])
        successful=list(dict.fromkeys(str(x).upper() for x in health.get("successful_tickers",[])))
        persist_batch_result(sec,successful,result,anchor=checked_at)
        failed=list(dict.fromkeys(str(x).upper() for x in health.get("failed_tickers",[])))
        if failed:
            failures.append(f"SEC_EDGAR_FAILED count={len(failed)} sample={failed[:20]!r}")
        if unresolved:
            failures.append(f"SEC_EDGAR_UNRESOLVED count={len(unresolved)} sample={sorted(unresolved)[:20]!r}")
    except Exception as exc:
        failures.append(f"SEC_EDGAR:{type(exc).__name__}:{exc}")

    try:
        ingest_alpha_vantage_batch(AlphaVantageCalendarBatch(_alpha_calendar_fetch),tickers,
                                   anchor=checked_at,lookforward=timedelta(days=14))
    except Exception as exc:
        failures.append(f"ALPHA_VANTAGE:{type(exc).__name__}:{exc}")

    for detail in failures:
        print("CATALYST_DATA_PLANE_FAILURE "+detail,flush=True)
    result={"tickers":len(tickers),"checked_at":checked_at.isoformat(),"failures":len(failures)}
    print("CATALYST_DATA_PLANE_COMPLETE "+str(result),flush=True)
    return result


def main():
    return run()


if __name__=="__main__":
    main()
