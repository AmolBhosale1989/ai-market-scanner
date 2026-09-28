from __future__ import annotations

import argparse
import os
from datetime import timedelta

from .consumer_snapshot import consumer_anchor
from .control_plane import read_dataset


REQUIRED_PROVIDERS=()
OPTIONAL_PROVIDERS=("ALPHA_VANTAGE","YAHOO_NEWS","SEC_EDGAR")
PROVIDER_MAX_AGE={"YAHOO_NEWS":timedelta(minutes=15),"SEC_EDGAR":timedelta(minutes=15),
                  "ALPHA_VANTAGE":timedelta(hours=24)}


def ingest_only(*, mandatory_only: bool = False) -> dict:
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
    providers = ("alpha_vantage",) if mandatory_only else ("alpha_vantage", "yahoo", "sec")
    if mandatory_only:
        print("CATALYST_INGEST_OPTIONAL_SKIPPED providers=YAHOO_NEWS,SEC_EDGAR", flush=True)
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
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
    validate_price_snapshot(anchor)
    tickers=_current_tickers()
    verify_coverage(tickers,anchor=anchor)
    result={"tickers":len(tickers),"providers":len(REQUIRED_PROVIDERS),
            "required_checks":len(tickers)*len(REQUIRED_PROVIDERS),
            "optional_providers":list(OPTIONAL_PROVIDERS)}
    print("CATALYST_COVERAGE_PASS "+str(result),flush=True)
    return result


def validate_price_snapshot(anchor):
    from .consumer_snapshot import consumer_pg_snapshot
    import pandas as pd
    visibility = consumer_pg_snapshot()
    if not visibility:
        raise RuntimeError("CONSUMER_PG_SNAPSHOT_REQUIRED_OR_INVALID")
    snapshot = read_dataset("warehouse_snapshot")
    required = {"MASTER_DAILY", "CRITICAL_DAILY", "CRITICAL_INTRADAY", "THEME_INTRADAY", "LIVE_INTRADAY"}
    if len(snapshot) != 1:
        raise RuntimeError("PRICE_SNAPSHOT_REQUIRED")
    row = snapshot.iloc[0]
    tiers = row.get("tiers")
    if (row.get("status") != "PASS" or pd.Timestamp(row.get("as_of_utc")) != anchor
            or row.get("pg_snapshot") != visibility or not isinstance(tiers, list)
            or not required.issubset({t.get("tier") for t in tiers if t.get("status") == "PASS"})):
        raise RuntimeError("PRICE_SNAPSHOT_INCOMPLETE")


def verify_coverage(tickers,*,anchor,pg_snapshot=None):
    from .database import connection
    from .consumer_snapshot import resolve_pg_snapshot
    from .catalogue_snapshot import instrument_cte
    visibility=resolve_pg_snapshot(pg_snapshot)
    from .catalyst_policy import disabled
    if disabled():
        if visibility is None or anchor is None:
            raise RuntimeError("CONSUMER_PG_SNAPSHOT_REQUIRED_OR_INVALID")
        for provider in OPTIONAL_PROVIDERS:
            print(f"CATALYST_COVERAGE_WARNING provider={provider} status=unavailable reason=disabled", flush=True)
        return True
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
                 if provider in PROVIDER_MAX_AGE and anchor-checked_at <= PROVIDER_MAX_AGE[provider]}
    for provider in OPTIONAL_PROVIDERS:
        optional_missing = sorted(ticker for ticker in wanted if (ticker, provider) not in present)
        if optional_missing:
            print(f"CATALYST_COVERAGE_WARNING provider={provider} missing={len(optional_missing)} "
                  f"sample={','.join(optional_missing[:10])}", flush=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Catalyst ingestion and snapshot-bound verification")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ingest-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--mandatory-only", action="store_true",
                        help="In ingestion mode, collect only Alpha Vantage; skip Yahoo and SEC")
    args = parser.parse_args(argv)
    if args.mandatory_only and not args.ingest_only:
        parser.error("--mandatory-only requires --ingest-only")
    return ingest_only(mandatory_only=args.mandatory_only) if args.ingest_only else run()


if __name__=="__main__":
    main()
