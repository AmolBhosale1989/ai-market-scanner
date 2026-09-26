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
