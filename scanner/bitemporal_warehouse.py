from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

@dataclass(frozen=True)
class PointInTimeRequirement:
    consumer: str
    tickers: tuple[str, ...]
    data_type: str = "OHLCV"
    timeframe: str = "1d"
    as_of: datetime | None = None
    max_age_minutes: int = 20
    columns: tuple[str, ...] = ()

def _connect():
    url=os.getenv("DATABASE_URL","").strip()
    if not url:
        raise RuntimeError("BITEMPORAL_WAREHOUSE_UNAVAILABLE: DATABASE_URL is not configured")
    try:
        import psycopg
    except ImportError as e:
        raise RuntimeError("BITEMPORAL_WAREHOUSE_UNAVAILABLE: psycopg is not installed") from e
    return psycopg.connect(url)

def start_run(provider: str, request_type: str, payload: dict) -> str:
    run_id=str(uuid.uuid4())
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO warehouse_run_log
          (warehouse_run_id,provider,request_type,requested_at,status,request_payload)
          VALUES (%s,%s,%s,now(),'STARTED',%s::jsonb)""",
          (run_id,provider,request_type,__import__("json").dumps(payload)))
    return run_id

def finish_run(run_id: str, status: str, metadata: dict | None=None, error: str | None=None):
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE warehouse_run_log SET completed_at=now(),status=%s,
          response_metadata=%s::jsonb,error_detail=%s WHERE warehouse_run_id=%s""",
          (status,__import__("json").dumps(metadata or {}),error,run_id))

def point_in_time(req: PointInTimeRequirement) -> pd.DataFrame:
    """Return only versions that were known by req.as_of. Never query future ingestion."""
    as_of=req.as_of or datetime.now(timezone.utc)
    tickers=[str(x).upper() for x in req.tickers if x]
    if not tickers:
        raise RuntimeError(f"WAREHOUSE_REQUIREMENT_INVALID: {req.consumer}")
    sql="""WITH ranked AS (
      SELECT i.canonical_symbol AS ticker, o.*,
             row_number() OVER (
               PARTITION BY o.instrument_id,o.data_type,o.timeframe,o.event_timestamp
               ORDER BY o.ingested_at DESC,o.observation_id DESC
             ) AS version_rank
      FROM market_observation o
      JOIN instrument i ON i.instrument_id=o.instrument_id
      WHERE i.canonical_symbol = ANY(%s)
        AND o.data_type=%s AND o.timeframe=%s
        AND o.event_timestamp <= %s
        AND o.ingested_at <= %s
    )
    SELECT * FROM ranked WHERE version_rank=1 ORDER BY ticker,event_timestamp"""
    with _connect() as conn:
        df=pd.read_sql_query(sql,conn,params=(tickers,req.data_type,req.timeframe,as_of,as_of))
    if df.empty:
        raise RuntimeError(f"WAREHOUSE_POINT_IN_TIME_EMPTY: {req.consumer}")
    return df

def ingest_observations(frame: pd.DataFrame, run_id: str, provider: str, data_type: str, timeframe: str):
    """Append immutable provider versions; never UPDATE historical observations."""
    required={"ticker","event_timestamp","ingested_at"}
    missing=required-set(frame.columns)
    if missing:
        raise RuntimeError(f"BITEMPORAL_INGEST_SCHEMA_FAILED: {sorted(missing)}")
    with _connect() as conn, conn.cursor() as cur:
        for _,r in frame.iterrows():
            ticker=str(r["ticker"]).upper()
            cur.execute("""INSERT INTO instrument(canonical_symbol)
                VALUES(%s) ON CONFLICT DO NOTHING""",(ticker,))
            cur.execute("""SELECT instrument_id FROM instrument
                WHERE canonical_symbol=%s ORDER BY instrument_id LIMIT 1""",(ticker,))
            instrument_id=cur.fetchone()[0]
            payload={k:(None if pd.isna(v) else v) for k,v in r.items()
                     if k not in {"ticker","event_timestamp","ingested_at","Open","High","Low","Close","Volume"}}
            cur.execute("""INSERT INTO market_observation
              (instrument_id,data_type,timeframe,event_timestamp,ingested_at,warehouse_run_id,
               provider,open,high,low,close,volume,payload)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
              ON CONFLICT DO NOTHING""",
              (instrument_id,data_type,timeframe,r["event_timestamp"],r["ingested_at"],run_id,
               provider,r.get("Open"),r.get("High"),r.get("Low"),r.get("Close"),r.get("Volume"),
               __import__("json").dumps(payload,default=str)))
