from __future__ import annotations

from .catalyst_adapters import CatalystProviderAdapter
from .catalyst_warehouse import ingest_catalyst_batch
from .bitemporal_warehouse import start_run,finish_run


def ingest_ticker(adapter: CatalystProviderAdapter,ticker: str,*,anchor) -> dict:
    """Fetch outside PostgreSQL transaction; persist one atomic provider/ticker unit of work."""
    provider=adapter.provider_name
    result=adapter.fetch_catalysts(str(ticker).upper(),anchor=anchor)
    run_id=start_run(provider,"CATALYST_CONTEXT",{"ticker":str(ticker).upper()})
    try:
        events=[{
            "provider_event_id":e.provider_event_id,
            "catalyst_type":e.catalyst_type,
            "event_timestamp":e.event_timestamp,
            "payload":dict(e.payload),
        } for e in result.events]
        revisions=ingest_catalyst_batch(provider=provider,ticker=ticker,warehouse_run_id=run_id,
                                        events=events,checked_at=anchor,rejected_count=result.rejected_count)
        finish_run(run_id,"AVAILABLE",{"events":len(events),"rejected":result.rejected_count})
        return {"warehouse_run_id":run_id,"events":len(events),"rejected":result.rejected_count,
                "revisions":revisions}
    except Exception as exc:
        finish_run(run_id,"FAILED",{"events":len(result.events),"rejected":result.rejected_count},
                   error=f"{type(exc).__name__}:{exc}")
        raise


def ingest_alpha_vantage_batch(batch_adapter, tickers, *, anchor, lookforward) -> dict:
    """Fetch the global calendar exactly once; persist per-ticker evidence atomically."""
    wanted=list(dict.fromkeys(str(x).upper() for x in tickers if x))
    print("CATALYST_FETCH_START provider=ALPHA_VANTAGE",flush=True)
    indexed=batch_adapter.fetch_and_index(anchor=anchor,lookforward=lookforward)
    print("CATALYST_FETCH_COMPLETE provider=ALPHA_VANTAGE",flush=True)
    print(f"CATALYST_PERSIST_START provider=ALPHA_VANTAGE tickers={len(wanted)}",flush=True)
    results={}
    for ticker in wanted:
        # Absence from the global CSV is a successful quiet check only because
        # fetch_and_index itself succeeded and validated the response envelope.
        fetched=indexed.get(ticker)
        events=() if fetched is None else fetched.events
        rejected=0 if fetched is None else fetched.rejected_count
        run_id=start_run(batch_adapter.provider_name,"CATALYST_CONTEXT",{"ticker":ticker,"global_batch":True})
        try:
            rows=[{"provider_event_id":e.provider_event_id,"catalyst_type":e.catalyst_type,
                   "event_timestamp":e.event_timestamp,"payload":dict(e.payload)} for e in events]
            revisions=ingest_catalyst_batch(provider=batch_adapter.provider_name,ticker=ticker,
                warehouse_run_id=run_id,events=rows,checked_at=anchor,rejected_count=rejected)
            finish_run(run_id,"AVAILABLE",{"events":len(rows),"rejected":rejected,"global_batch":True})
            results[ticker]={"events":len(rows),"rejected":rejected,"revisions":revisions}
            if len(results) == 1 or len(results) % 25 == 0 or len(results) == len(wanted):
                print(f"CATALYST_PERSIST_PROGRESS provider=ALPHA_VANTAGE committed={len(results)} total={len(wanted)}",flush=True)
        except Exception as exc:
            finish_run(run_id,"FAILED",{"events":len(events),"rejected":rejected,"global_batch":True},
                       error=f"{type(exc).__name__}:{exc}")
            raise
    print(f"CATALYST_PERSIST_COMPLETE provider=ALPHA_VANTAGE committed={len(results)}",flush=True)
    return results



def persist_batch_result(adapter,tickers,result,*,anchor,checked_at_by_ticker=None) -> dict:
    """Persist successful provider observations using their own knowledge times."""
    provider=adapter.provider_name
    wanted=list(dict.fromkeys(str(x).upper() for x in tickers if x))
    checked_at_by_ticker={str(k).upper():v for k,v in (checked_at_by_ticker or {}).items()}
    grouped={ticker:[] for ticker in wanted}
    rejected_by_ticker={ticker:0 for ticker in wanted}
    for event in result.events:
        ticker=str(event.payload.get("_canonical_ticker") or "").upper()
        if ticker not in grouped:
            raise RuntimeError(f"CATALYST_BATCH_TICKER_UNKNOWN provider={provider} ticker={ticker}")
        grouped[ticker].append(event)
    # A batch-level rejected_count cannot be safely assigned to a ticker. Fail closed
    # rather than laundering partial corruption into clean per-ticker checks.
    if result.rejected_count:
        raise RuntimeError(f"CATALYST_BATCH_REJECTED provider={provider} rejected={result.rejected_count}")
    output={}
    print(f"CATALYST_PERSIST_START provider={provider} tickers={len(wanted)}",flush=True)
    for ticker,events in grouped.items():
        run_id=start_run(provider,"CATALYST_CONTEXT",{"ticker":ticker,"batch":True})
        try:
            rows=[{"provider_event_id":e.provider_event_id,"catalyst_type":e.catalyst_type,
                   "event_timestamp":e.event_timestamp,
                   "payload":{k:v for k,v in dict(e.payload).items() if k!="_canonical_ticker"}}
                  for e in events]
            checked_at=checked_at_by_ticker.get(ticker,anchor)
            revisions=ingest_catalyst_batch(provider=provider,ticker=ticker,warehouse_run_id=run_id,
                                            events=rows,checked_at=checked_at,rejected_count=0)
            finish_run(run_id,"AVAILABLE",{"events":len(rows),"rejected":0,"batch":True})
            output[ticker]={"events":len(rows),"revisions":revisions}
            if len(output) == 1 or len(output) % 25 == 0 or len(output) == len(wanted):
                print(f"CATALYST_PERSIST_PROGRESS provider={provider} committed={len(output)} total={len(wanted)}",flush=True)
        except Exception as exc:
            finish_run(run_id,"FAILED",{"events":len(events),"rejected":0,"batch":True},
                       error=f"{type(exc).__name__}:{exc}")
            raise
    print(f"CATALYST_PERSIST_COMPLETE provider={provider} committed={len(output)}",flush=True)
    return output
