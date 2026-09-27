from __future__ import annotations

import argparse
import os
from datetime import timedelta

from .consumer_snapshot import consumer_anchor
from .control_plane import read_dataset


REQUIRED_PROVIDERS=("YAHOO_NEWS","SEC_EDGAR","ALPHA_VANTAGE")
PROVIDER_MAX_AGE={"YAHOO_NEWS":timedelta(minutes=15),"SEC_EDGAR":timedelta(minutes=15),
                  "ALPHA_VANTAGE":timedelta(hours=24)}


def ingest_only() -> dict:
    """Pre-snapshot handoff to isolated provider workers for this run's plan."""
    if os.getenv("WAREHOUSE_CONSUMER_SNAPSHOT") == "1":
        raise RuntimeError("CATALYST_INGEST_AFTER_SNAPSHOT_FORBIDDEN")
    from .control_plane import current_run_id
    if not current_run_id():
        raise RuntimeError("CATALYST_INGEST_RUN_REQUIRED")
    tickers = _current_tickers()
    # Import provider code only in the explicitly selected ingestion mode.
    from concurrent.futures import ThreadPoolExecutor
    from .catalyst_data_plane import run as collect
    providers = ("alpha_vantage", "yahoo", "sec")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(collect, provider, tickers=tickers) for provider in providers]
        results = [future.result() for future in futures]
    # Provider failures remain explicit. Stage 72 alone decides whether fresh,
    # valid evidence (including prior checks) satisfies every required pair.
    result = {"tickers": len(tickers), "providers": len(results),
              "failures": sum(row["failures"] for row in results)}
    print("CATALYST_INGEST_ATTEMPTS_COMPLETE " + str(result), flush=True)
    return result


def _current_tickers():
    universe = read_dataset("live_universe")
    if universe.empty or "ticker" not in universe.columns:
        raise RuntimeError("CATALYST_LIVE_UNIVERSE_EMPTY")
    tickers = list(dict.fromkeys(universe["ticker"].dropna().astype(str).str.strip().str.upper()))
    if not tickers or any(not ticker for ticker in tickers):
        raise RuntimeError("CATALYST_LIVE_UNIVERSE_EMPTY")
    return tickers


def run() -> dict:
    """Live control-plane gate: database-only, no provider/network access."""
    anchor=consumer_anchor()
    if anchor is None:
        raise RuntimeError("CATALYST_CONTEXT_ANCHOR_REQUIRED")
    tickers=_current_tickers()
    verify_coverage(tickers,anchor=anchor)
    result={"tickers":len(tickers),"providers":len(REQUIRED_PROVIDERS),
            "required_checks":len(tickers)*len(REQUIRED_PROVIDERS)}
    print("CATALYST_COVERAGE_PASS "+str(result),flush=True)
    return result


def verify_coverage(tickers,*,anchor,pg_snapshot=None):
    from .database import connection
    from .consumer_snapshot import resolve_pg_snapshot
    from .catalogue_snapshot import instrument_cte
    visibility=resolve_pg_snapshot(pg_snapshot)
    catalogue, params=instrument_cte(anchor,visibility)
    clause="AND pg_visible_in_snapshot(c.writer_xid,%s::pg_snapshot)" if visibility else ""
    wanted=list(dict.fromkeys(str(x).upper() for x in tickers if x))
    params += (wanted,anchor) + ((visibility,) if visibility else ())
    with connection() as conn,conn.cursor() as cur:
        cur.execute(f"""WITH {catalogue} SELECT c.ticker,c.provider,max(c.checked_at)
                       FROM catalyst_check c JOIN visible_instrument i ON i.instrument_id=c.instrument_id AND i.canonical_symbol=c.ticker
                       WHERE c.ticker=ANY(%s) AND c.checked_at<=%s
                         {clause}
                         AND result_status IN ('EVENTS','NO_EVENT')
                         AND rejected_count=0
                       GROUP BY c.ticker,c.provider""",params)
        rows=cur.fetchall()
        present={(ticker,provider) for ticker,provider,checked_at in rows
                 if anchor-checked_at <= PROVIDER_MAX_AGE[provider]}
    required={(ticker,provider) for ticker in wanted for provider in REQUIRED_PROVIDERS}
    missing=required-present
    if missing:
        sample=",".join(f"{t}:{p}" for t,p in sorted(missing)[:10])
        raise RuntimeError(f"CATALYST_COVERAGE_INCOMPLETE missing={len(missing)} sample={sample}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Catalyst ingestion and snapshot-bound verification")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ingest-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    return ingest_only() if args.ingest_only else run()


if __name__=="__main__":
    main()
