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
                                        events=events,rejected_count=result.rejected_count)
        finish_run(run_id,"AVAILABLE",{"events":len(events),"rejected":result.rejected_count})
        return {"warehouse_run_id":run_id,"events":len(events),"rejected":result.rejected_count,
                "revisions":revisions}
    except Exception as exc:
        finish_run(run_id,"FAILED",{"events":len(result.events),"rejected":result.rejected_count},
                   error=f"{type(exc).__name__}:{exc}")
        raise
