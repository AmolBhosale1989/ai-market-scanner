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
    indexed=batch_adapter.fetch_and_index(anchor=anchor,lookforward=lookforward)
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
        except Exception as exc:
            finish_run(run_id,"FAILED",{"events":len(events),"rejected":rejected,"global_batch":True},
                       error=f"{type(exc).__name__}:{exc}")
            raise
    return results



def persist_batch_result(adapter,tickers,result,*,anchor) -> dict:
    """Persist a successful provider batch poll as per-ticker atomic checks."""
    provider=adapter.provider_name
    wanted=list(dict.fromkeys(str(x).upper() for x in tickers if x))
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
    for ticker,events in grouped.items():
        run_id=start_run(provider,"CATALYST_CONTEXT",{"ticker":ticker,"batch":True})
        try:
            rows=[{"provider_event_id":e.provider_event_id,"catalyst_type":e.catalyst_type,
                   "event_timestamp":e.event_timestamp,
                   "payload":{k:v for k,v in dict(e.payload).items() if k!="_canonical_ticker"}}
                  for e in events]
            revisions=ingest_catalyst_batch(provider=provider,ticker=ticker,warehouse_run_id=run_id,
                                            events=rows,checked_at=anchor,rejected_count=0)
            finish_run(run_id,"AVAILABLE",{"events":len(rows),"rejected":0,"batch":True})
            output[ticker]={"events":len(rows),"revisions":revisions}
        except Exception as exc:
            finish_run(run_id,"FAILED",{"events":len(events),"rejected":0,"batch":True},
                       error=f"{type(exc).__name__}:{exc}")
            raise
    return output
