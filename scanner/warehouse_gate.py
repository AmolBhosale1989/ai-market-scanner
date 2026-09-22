from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from .bitemporal_warehouse import _connect, verify_health
from .config import (
    CORE_INTRADAY_MARKET_SYMBOLS,
    CRITICAL_DAILY_MIN_COVERAGE,
    CRITICAL_INTRADAY_MIN_COVERAGE,
    CRITICAL_MARKET_SYMBOLS,
    LIVE_INTRADAY_MAX_AGE_MINUTES,
    LIVE_INTRADAY_MIN_COVERAGE,
    MASTER_DAILY_MIN_COVERAGE,
    MASTER_UNIVERSE_MINIMUM,
    THEME_INTRADAY_MARKET_SYMBOLS,
    THEME_INTRADAY_MAX_AGE_MINUTES,
    THEME_INTRADAY_MIN_COVERAGE,
)
from .warehouse import _freshness_failures
from .control_plane import current_run_id, read_dataset, write_dataset


@dataclass(frozen=True)
class CoverageTier:
    name: str
    symbols: tuple[str, ...]
    timeframe: str
    minimum_coverage: float
    minimum_bars: int
    max_age_minutes: int


def _symbols(frame: pd.DataFrame, dataset_name: str, *, required: bool = True) -> tuple[str, ...]:
    if frame is None or frame.empty:
        if required:
            raise RuntimeError(f"WAREHOUSE_GATE_INPUT_MISSING: {dataset_name}")
        return ()
    for col in ("ticker","symbol","Ticker","Symbol"):
        if col in frame.columns:
            values=frame[col].dropna().astype(str).str.upper().str.strip()
            return tuple(dict.fromkeys(x for x in values if x))
    raise RuntimeError(f"WAREHOUSE_GATE_INPUT_INVALID: {dataset_name} has no symbol column")


def _catalogue_hash(symbols: Iterable[str]) -> str:
    payload="\n".join(sorted(set(str(x).upper() for x in symbols if x)))
    return hashlib.sha256(payload.encode()).hexdigest()


def coverage_frame(tier: CoverageTier, as_of: datetime) -> pd.DataFrame:
    """Aggregate point-in-time quality per symbol with bounded per-instrument sorts."""
    sql="""WITH wanted AS (
      SELECT DISTINCT ON (i.canonical_symbol) i.instrument_id,i.canonical_symbol AS ticker
      FROM instrument i WHERE i.canonical_symbol = ANY(%s)
      ORDER BY i.canonical_symbol,i.instrument_id
    )
    SELECT w.ticker,s.bars,s.event_timestamp,s.ingested_at,s.invalid_bars,s.warehouse_run_id
    FROM wanted w CROSS JOIN LATERAL (
      SELECT count(*) AS bars,max(v.event_timestamp) AS event_timestamp,
             max(v.ingested_at) AS ingested_at,
             count(*) FILTER (WHERE v.open IS NULL OR v.high IS NULL OR v.low IS NULL OR v.close IS NULL
               OR v.open<=0 OR v.high<=0 OR v.low<=0 OR v.close<=0 OR v.volume IS NULL OR v.volume<0
               OR v.high<GREATEST(v.open,v.close,v.low)
               OR v.low>LEAST(v.open,v.close,v.high)) AS invalid_bars,
             (array_agg(v.warehouse_run_id ORDER BY v.event_timestamp DESC,v.ingested_at DESC))[1] AS warehouse_run_id
      FROM (
        SELECT DISTINCT ON (o.event_timestamp) o.event_timestamp,o.ingested_at,
               o.open,o.high,o.low,o.close,o.volume,o.warehouse_run_id
        FROM market_observation o
        WHERE o.instrument_id=w.instrument_id AND o.data_type='OHLCV' AND o.timeframe=%s
          AND o.event_timestamp <= %s AND o.ingested_at <= %s
        ORDER BY o.event_timestamp,o.ingested_at DESC,o.observation_id DESC
      ) v
    ) s WHERE s.bars>0 ORDER BY w.ticker"""
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
        CoverageTier("CRITICAL_INTRADAY",tuple(CORE_INTRADAY_MARKET_SYMBOLS),"5m",CRITICAL_INTRADAY_MIN_COVERAGE,120,10),
        CoverageTier(
            "THEME_INTRADAY",tuple(THEME_INTRADAY_MARKET_SYMBOLS),"5m",
            THEME_INTRADAY_MIN_COVERAGE,120,THEME_INTRADAY_MAX_AGE_MINUTES,
        ),
        CoverageTier(
            "LIVE_INTRADAY",live_symbols,"5m",LIVE_INTRADAY_MIN_COVERAGE,20,
            LIVE_INTRADAY_MAX_AGE_MINUTES,
        ),
    )


def run(master_frame: pd.DataFrame, live_frame: pd.DataFrame, as_of: datetime | None = None, selected: set[str] | None = None) -> dict:
    verify_health()
    selected=selected or set()
    master=_symbols(master_frame,"master_universe")
    live=_symbols(live_frame,"live_universe",required="LIVE_INTRADAY" in selected or not selected)
    as_of=as_of or datetime.now(timezone.utc)
    tiers=[x for x in build_tiers(master,live) if not selected or x.name in selected]
    results=[evaluate_tier(tier,coverage_frame(tier,as_of)) for tier in tiers]
    snapshot={
        "schema_version":1,
        "production_run_id":current_run_id(),
        "as_of_utc":as_of.isoformat(),
        "master_catalogue_hash":_catalogue_hash(master),
        "master_symbols":len(master),
        "live_symbols":len(live),
        "tiers":results,
        "status":"PASS" if results and all(x["status"]=="PASS" for x in results) else "FAIL",
    }
    write_dataset("warehouse_snapshot",pd.DataFrame([snapshot]),entity_key=None)
    write_dataset(
        "warehouse_coverage",
        pd.DataFrame(results).drop(columns=["missing_sample","short_history_sample","invalid_sample","stale_sample"]),
        entity_key="tier",
    )
    for result in results:
        print(
            "WAREHOUSE_TIER "
            f"tier={result['tier']} status={result['status']} "
            f"usable={result['usable_symbols']}/{result['expected_symbols']} "
            f"usable_coverage={result['usable_coverage']:.6f} "
            f"minimum_coverage={result['minimum_coverage']:.6f} "
            f"missing={result['missing_count']} short={result['short_history_count']} "
            f"invalid={result['invalid_symbol_count']} stale={result['stale_symbol_count']} "
            f"missing_sample={','.join(result['missing_sample'][:10]) or '-'} "
            f"stale_sample={','.join(result['stale_sample'][:10]) or '-'}",
            flush=True,
        )
    if snapshot["status"]!="PASS":
        failed=",".join(x["tier"] for x in results if x["status"]!="PASS")
        raise RuntimeError(f"WAREHOUSE_GATE_FAILED: {failed}")
    return snapshot


def main():
    p=argparse.ArgumentParser(description="Fail-closed production warehouse coverage gate")
    p.add_argument(
        "--tier",action="append",
        choices=["MASTER_DAILY","CRITICAL_DAILY","CRITICAL_INTRADAY","THEME_INTRADAY","LIVE_INTRADAY"],
    )
    p.add_argument("--as-of",default="now")
    args=p.parse_args()
    as_of=None if args.as_of=="now" else pd.Timestamp(args.as_of).to_pydatetime()
    master=read_dataset("master_universe")
    live=read_dataset("live_universe", required=False)
    selected=set(args.tier or ())
    if live.empty and "LIVE_INTRADAY" not in selected:
        live=master.iloc[0:0].copy()
    snapshot=run(master,live,as_of=as_of,selected=selected)
    print(f"WAREHOUSE_SNAPSHOT_AVAILABLE run_id={snapshot['production_run_id']} as_of={snapshot['as_of_utc']}")


if __name__ == "__main__":
    main()
