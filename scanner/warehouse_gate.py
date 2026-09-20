from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from .bitemporal_warehouse import _connect, verify_health
from .config import (
    CRITICAL_DAILY_MIN_COVERAGE,
    CRITICAL_INTRADAY_MIN_COVERAGE,
    CRITICAL_MARKET_SYMBOLS,
    LIVE_INTRADAY_MIN_COVERAGE,
    MASTER_DAILY_MIN_COVERAGE,
    MASTER_UNIVERSE_MINIMUM,
    OUTPUT_DIR,
)
from .warehouse import _freshness_failures


@dataclass(frozen=True)
class CoverageTier:
    name: str
    symbols: tuple[str, ...]
    timeframe: str
    minimum_coverage: float
    minimum_bars: int
    max_age_minutes: int


def _symbols(path: Path, *, required: bool = True) -> tuple[str, ...]:
    if not path.exists() or not path.stat().st_size:
        if required:
            raise RuntimeError(f"WAREHOUSE_GATE_INPUT_MISSING: {path}")
        return ()
    frame=pd.read_csv(path)
    for col in ("ticker","symbol","Ticker","Symbol"):
        if col in frame.columns:
            values=frame[col].dropna().astype(str).str.upper().str.strip()
            return tuple(dict.fromkeys(x for x in values if x))
    raise RuntimeError(f"WAREHOUSE_GATE_INPUT_INVALID: {path} has no symbol column")


def _catalogue_hash(symbols: Iterable[str]) -> str:
    payload="\n".join(sorted(set(str(x).upper() for x in symbols if x)))
    return hashlib.sha256(payload.encode()).hexdigest()


def coverage_frame(tier: CoverageTier, as_of: datetime) -> pd.DataFrame:
    """Aggregate point-in-time quality/freshness by symbol without loading bar history."""
    sql="""WITH ranked AS (
      SELECT i.canonical_symbol AS ticker,o.event_timestamp,o.ingested_at,
             o.open,o.high,o.low,o.close,o.volume,o.warehouse_run_id,
             row_number() OVER (
               PARTITION BY o.instrument_id,o.data_type,o.timeframe,o.event_timestamp
               ORDER BY o.ingested_at DESC,o.observation_id DESC
             ) AS version_rank
      FROM market_observation o JOIN instrument i ON i.instrument_id=o.instrument_id
      WHERE i.canonical_symbol = ANY(%s) AND o.data_type='OHLCV' AND o.timeframe=%s
        AND o.event_timestamp <= %s AND o.ingested_at <= %s
    )
    SELECT ticker,count(*) AS bars,max(event_timestamp) AS event_timestamp,
           max(ingested_at) AS ingested_at,
           count(*) FILTER (WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
             OR open<=0 OR high<=0 OR low<=0 OR close<=0 OR volume IS NULL OR volume<0
             OR high<GREATEST(open,close,low) OR low>LEAST(open,close,high)) AS invalid_bars,
           (array_agg(warehouse_run_id ORDER BY event_timestamp DESC,ingested_at DESC))[1] AS warehouse_run_id
    FROM ranked WHERE version_rank=1 GROUP BY ticker ORDER BY ticker"""
    with _connect() as conn:
        return pd.read_sql_query(sql,conn,params=(list(tier.symbols),tier.timeframe,as_of,as_of))


def evaluate_tier(tier: CoverageTier, frame: pd.DataFrame) -> dict:
    wanted=set(tier.symbols)
    have=set(frame.get("ticker",pd.Series(dtype=str)).astype(str).str.upper()) if not frame.empty else set()
    missing=sorted(wanted-have)
    coverage=len(have)/len(wanted) if wanted else 0.0
    short=[]
    invalid=[]
    stale=[]
    stale_expectation=""
    if not frame.empty:
        bars=pd.to_numeric(frame.get("bars"),errors="coerce").fillna(0)
        short=frame.loc[bars<tier.minimum_bars,"ticker"].astype(str).tolist()
        bad=pd.to_numeric(frame.get("invalid_bars"),errors="coerce").fillna(0)
        invalid=frame.loc[bad>0,"ticker"].astype(str).tolist()
        stale,_,stale_expectation=_freshness_failures(
            frame,tier.timeframe,tier.max_age_minutes,f"warehouse_gate.{tier.name}"
        )
    quarantined=set(short)|set(invalid)|set(stale)
    usable_symbols=len(have-quarantined)
    usable_coverage=usable_symbols/len(wanted) if wanted else 0.0
    strict=tier.minimum_coverage>=1.0
    passed=(usable_coverage>=tier.minimum_coverage and (not strict or not quarantined))
    return {
        "tier":tier.name,"timeframe":tier.timeframe,"expected_symbols":len(wanted),
        "covered_symbols":len(have),"coverage":round(coverage,6),
        "usable_symbols":usable_symbols,"usable_coverage":round(usable_coverage,6),
        "minimum_coverage":tier.minimum_coverage,"minimum_bars":tier.minimum_bars,
        "missing_count":len(missing),"short_history_count":len(short),
        "invalid_symbol_count":len(invalid),"stale_symbol_count":len(stale),
        "stale_error":(
            f"stale_symbols={len(stale)} sample={','.join(stale[:10])} {stale_expectation}" if stale else ""
        ),
        "missing_sample":missing[:20],"short_history_sample":short[:20],
        "invalid_sample":invalid[:20],"stale_sample":stale[:20],"status":"PASS" if passed else "FAIL",
    }


def build_tiers(master: Iterable[str], live: Iterable[str]) -> tuple[CoverageTier, ...]:
    master_symbols=tuple(dict.fromkeys(str(x).upper() for x in master if x))
    live_symbols=tuple(dict.fromkeys(str(x).upper() for x in live if x))
    if len(master_symbols)<MASTER_UNIVERSE_MINIMUM:
        raise RuntimeError(
            f"MASTER_UNIVERSE_INCOMPLETE: got={len(master_symbols)} minimum={MASTER_UNIVERSE_MINIMUM}"
        )
    return (
        CoverageTier("MASTER_DAILY",master_symbols,"1d",MASTER_DAILY_MIN_COVERAGE,40,20),
        CoverageTier("CRITICAL_DAILY",tuple(CRITICAL_MARKET_SYMBOLS),"1d",CRITICAL_DAILY_MIN_COVERAGE,220,20),
        CoverageTier("CRITICAL_INTRADAY",tuple(CRITICAL_MARKET_SYMBOLS),"5m",CRITICAL_INTRADAY_MIN_COVERAGE,120,10),
        CoverageTier("LIVE_INTRADAY",live_symbols,"5m",LIVE_INTRADAY_MIN_COVERAGE,20,10),
    )


def run(master_file: Path, live_file: Path, as_of: datetime | None = None, selected: set[str] | None = None) -> dict:
    verify_health()
    master=_symbols(master_file)
    live=_symbols(live_file)
    as_of=as_of or datetime.now(timezone.utc)
    tiers=[x for x in build_tiers(master,live) if not selected or x.name in selected]
    results=[evaluate_tier(tier,coverage_frame(tier,as_of)) for tier in tiers]
    snapshot={
        "schema_version":1,
        "production_run_id":os.getenv("PRODUCTION_RUN_ID") or str(uuid.uuid4()),
        "as_of_utc":as_of.isoformat(),
        "master_catalogue_hash":_catalogue_hash(master),
        "master_symbols":len(master),
        "live_symbols":len(live),
        "tiers":results,
        "status":"PASS" if results and all(x["status"]=="PASS" for x in results) else "FAIL",
    }
    OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
    (OUTPUT_DIR/"warehouse_snapshot.json").write_text(json.dumps(snapshot,indent=2,sort_keys=True))
    pd.DataFrame(results).drop(columns=["missing_sample","short_history_sample","invalid_sample","stale_sample"]).to_csv(
        OUTPUT_DIR/"warehouse_coverage.csv",index=False
    )
    if snapshot["status"]!="PASS":
        failed=",".join(x["tier"] for x in results if x["status"]!="PASS")
        raise RuntimeError(f"WAREHOUSE_GATE_FAILED: {failed}")
    return snapshot


def main():
    p=argparse.ArgumentParser(description="Fail-closed production warehouse coverage gate")
    p.add_argument("--master-file",type=Path,default=OUTPUT_DIR/"master_universe.csv")
    p.add_argument("--live-file",type=Path,default=OUTPUT_DIR/"live_universe.csv")
    p.add_argument("--tier",action="append",choices=["MASTER_DAILY","CRITICAL_DAILY","CRITICAL_INTRADAY","LIVE_INTRADAY"])
    p.add_argument("--as-of",default="now")
    args=p.parse_args()
    as_of=None if args.as_of=="now" else pd.Timestamp(args.as_of).to_pydatetime()
    snapshot=run(args.master_file,args.live_file,as_of=as_of,selected=set(args.tier or ()))
    print(f"WAREHOUSE_SNAPSHOT_AVAILABLE run_id={snapshot['production_run_id']} as_of={snapshot['as_of_utc']}")


if __name__ == "__main__":
    main()
