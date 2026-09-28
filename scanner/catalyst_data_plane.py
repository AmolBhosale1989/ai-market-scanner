from __future__ import annotations

import argparse
import os
import time
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
    # Two total attempts, matching the Yahoo/SEC cap. Never log an exception
    # URL: requests embeds the API key in that URL on HTTP failures.
    for attempt in range(2):
        try:
            response=requests.get("https://www.alphavantage.co/query",params={
                "function":"EARNINGS_CALENDAR","horizon":"3month","apikey":key},
                timeout=(3.0,5.0),allow_redirects=False)
            try:
                response.raise_for_status()
                if 300 <= response.status_code < 400:
                    raise RuntimeError("ALPHA_VANTAGE_REDIRECT_REJECTED")
                return response.text
            finally:
                response.close()
        except requests.RequestException as exc:
            status=getattr(getattr(exc,"response",None),"status_code",None)
            print(f"CATALYST_HTTP_FAILURE provider=ALPHA_VANTAGE attempt={attempt+1} "
                  f"status={status} error={type(exc).__name__}",flush=True)
            # Rate limits fail immediately. No Retry-After or quota sleep loop.
            transient=isinstance(exc,(requests.Timeout,requests.ConnectionError)) or status in {500,502,503,504}
            if attempt == 1 or not transient:
                raise RuntimeError(f"ALPHA_VANTAGE_HTTP_FAILED status={status} error={type(exc).__name__}") from None
            time.sleep(1.0)


def _universe():
    frame=read_dataset("live_universe",published_mode="production",required=False)
    if frame.empty or "ticker" not in frame.columns:
        raise RuntimeError("CATALYST_PUBLISHED_UNIVERSE_EMPTY")
    return list(dict.fromkeys(frame["ticker"].dropna().astype(str).str.upper()))


def run(provider: str = "all", *, tickers=None) -> dict:
    """Independent data-plane worker. Provider selection is orchestration-only."""
    provider = provider.lower()
    if provider not in {"all", "yahoo", "sec", "alpha_vantage"}:
        raise ValueError(f"CATALYST_PROVIDER_UNKNOWN: {provider}")

    tickers = _universe() if tickers is None else list(tickers)
    if not tickers:
        raise RuntimeError("CATALYST_INGEST_UNIVERSE_EMPTY")
    checked_at = datetime.now(timezone.utc)
    started = time.monotonic()
    print(f"CATALYST_DATA_PLANE_START provider={provider} tickers={len(tickers)} "
          f"checked_at={checked_at.isoformat()}",flush=True)
    failures = []

    if provider in {"all", "yahoo"}:
        yahoo = ExistingYahooNewsAdapter(max_workers=8, max_tickers=len(tickers))
        try:
            result, health = yahoo.fetch_batch(tickers, anchor=checked_at)
            successful = list(dict.fromkeys(
                str(x).upper() for x in health.get("successful_tickers", [])
            ))
            knowledge_times = {
                str(item["ticker"]).upper(): item["checked_at"]
                for item in health.get("successful_checks", [])
            }
            persist_batch_result(
                yahoo, successful, result, anchor=checked_at,
                checked_at_by_ticker=knowledge_times,
            )
            failed = list(dict.fromkeys(
                str(x).upper() for x in health.get("failed_tickers", [])
            ))
            if failed:
                failures.append(
                    f"YAHOO_NEWS_FAILED count={len(failed)} sample={failed[:20]!r}"
                )
        except Exception as exc:
            failures.append(f"YAHOO_NEWS:{type(exc).__name__}:{exc}")

    if provider in {"all", "sec"}:
        sec = ExistingSecEdgarAdapter(max_workers=5)
        try:
            print("CATALYST_FETCH_START provider=SEC_EDGAR",flush=True)
            result, health = sec.fetch_batch(tickers, anchor=checked_at)
            print("CATALYST_FETCH_COMPLETE provider=SEC_EDGAR",flush=True)
            unresolved = set(health.get("unresolved_tickers") or [])
            successful = list(dict.fromkeys(
                str(x).upper() for x in health.get("successful_tickers", [])
            ))
            knowledge_times = {
                str(item["ticker"]).upper(): item["checked_at"]
                for item in health.get("successful_checks", [])
            }
            persist_batch_result(
                sec, successful, result, anchor=checked_at,
                checked_at_by_ticker=knowledge_times,
            )
            failed = list(dict.fromkeys(
                str(x).upper() for x in health.get("failed_tickers", [])
            ))
            if failed:
                failures.append(
                    f"SEC_EDGAR_FAILED count={len(failed)} sample={failed[:20]!r}"
                )
            if unresolved:
                failures.append(
                    f"SEC_EDGAR_UNRESOLVED count={len(unresolved)} "
                    f"sample={sorted(unresolved)[:20]!r}"
                )
        except Exception as exc:
            failures.append(f"SEC_EDGAR:{type(exc).__name__}:{exc}")

    if provider in {"all", "alpha_vantage"}:
        try:
            ingest_alpha_vantage_batch(
                AlphaVantageCalendarBatch(_alpha_calendar_fetch),
                tickers,
                anchor=checked_at,
                lookforward=timedelta(days=14),
            )
        except Exception as exc:
            failures.append(f"ALPHA_VANTAGE:{type(exc).__name__}")

    for detail in failures:
        print("CATALYST_DATA_PLANE_FAILURE " + detail, flush=True)

    result = {
        "provider": provider,
        "tickers": len(tickers),
        "checked_at": checked_at.isoformat(),
        "failures": len(failures),
        "duration_seconds": round(time.monotonic()-started,3),
    }
    print("CATALYST_DATA_PLANE_COMPLETE " + str(result), flush=True)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--provider",default="all",choices=["all","yahoo","sec","alpha_vantage"])
    args=parser.parse_args()
    return run(args.provider)


if __name__=="__main__":
    main()
