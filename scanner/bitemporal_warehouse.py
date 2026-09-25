from __future__ import annotations

import argparse
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from .ohlcv_quality import invalid_rows, invalid_sql
from .market_cutoff import event_cutoff


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
    from .database import connection
    return connection()


def verify_health() -> dict:
    """Fail closed unless PostgreSQL, TLS, and the bitemporal schema are usable."""
    required = ("warehouse_run_log", "instrument", "market_observation")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1, current_setting('ssl', true)")
        one, ssl_enabled = cur.fetchone()
        if one != 1 or str(ssl_enabled).lower() not in {"on", "true"}:
            raise RuntimeError("BITEMPORAL_WAREHOUSE_HEALTH_FAILED: TLS is not active")
        cur.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema=current_schema() AND table_name = ANY(%s)""",
            (list(required),),
        )
        present = {row[0] for row in cur.fetchall()}
    missing = sorted(set(required) - present)
    if missing:
        raise RuntimeError(f"BITEMPORAL_WAREHOUSE_HEALTH_FAILED: missing tables {missing}")
    return {"status": "AVAILABLE", "tls": True, "tables": list(required)}


def start_run(provider: str, request_type: str, payload: dict) -> str:
    run_id = str(uuid.uuid4())
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO warehouse_run_log
          (warehouse_run_id,provider,request_type,requested_at,status,request_payload)
          VALUES (%s,%s,%s,now(),'STARTED',%s::jsonb)""",
          (run_id, provider, request_type, __import__("json").dumps(payload)))
    return run_id


def finish_run(run_id: str, status: str, metadata: dict | None = None, error: str | None = None):
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE warehouse_run_log SET completed_at=now(),status=%s,
          response_metadata=%s::jsonb,error_detail=%s WHERE warehouse_run_id=%s""",
          (status, __import__("json").dumps(metadata or {}), error, run_id))


def point_in_time(req: PointInTimeRequirement) -> pd.DataFrame:
    """Return only versions that were known by req.as_of. Never query future ingestion."""
    from .consumer_snapshot import consumer_anchor
    anchor = consumer_anchor()
    as_of = req.as_of or anchor or datetime.now(timezone.utc)
    if anchor is not None and pd.Timestamp(as_of) > anchor:
        raise RuntimeError('CONSUMER_SNAPSHOT_READ_AFTER_ANCHOR')
    tickers = [str(x).upper() for x in req.tickers if x]
    if not tickers:
        raise RuntimeError(f"WAREHOUSE_REQUIREMENT_INVALID: {req.consumer}")
    sql = """WITH ranked AS (
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
        df = pd.read_sql_query(sql, conn, params=(tickers, req.data_type, req.timeframe, event_cutoff(req.timeframe, as_of), as_of))
    if df.empty:
        raise RuntimeError(f"WAREHOUSE_POINT_IN_TIME_EMPTY: {req.consumer}")
    return df



def latest_event_timestamps(tickers: list[str], data_type: str = "OHLCV", timeframe: str = "1d") -> dict[str, datetime]:
    """Newest stored event time per symbol, used to make provider refreshes incremental."""
    wanted = list(dict.fromkeys(str(x).upper() for x in tickers if x))
    if not wanted:
        return {}
    # Invalid current versions must trigger a full bounded per-symbol backfill.
    sql = f"""WITH wanted AS (
      SELECT instrument_id,canonical_symbol FROM instrument WHERE canonical_symbol = ANY(%s)
    ) SELECT w.canonical_symbol,s.event_timestamp FROM wanted w CROSS JOIN LATERAL (
      SELECT max(o.event_timestamp) AS event_timestamp FROM (
        SELECT DISTINCT ON (event_timestamp) * FROM market_observation
        WHERE instrument_id=w.instrument_id AND data_type=%s AND timeframe=%s
        ORDER BY event_timestamp,ingested_at DESC,observation_id DESC
      ) o HAVING count(*)>0 AND count(*) FILTER (WHERE {invalid_sql("o")})=0
    ) s"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (wanted, data_type, timeframe))
        return {str(symbol): ts for symbol, ts in cur.fetchall() if ts is not None}

def ingest_observations(frame: pd.DataFrame, run_id: str, provider: str, data_type: str, timeframe: str):
    """Bulk insert a provider batch in one transaction; identical logical bars are idempotent."""
    required = {"ticker", "event_timestamp", "ingested_at"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"BITEMPORAL_INGEST_SCHEMA_FAILED: {sorted(missing)}")
    if frame.empty:
        return 0
    normalized=frame.rename(columns={c:c.lower() for c in ("Open","High","Low","Close","Volume")})
    if not set(("open","high","low","close","volume")).issubset(normalized.columns) or invalid_rows(normalized).any():
        raise RuntimeError("BITEMPORAL_INGEST_QUALITY_FAILED: invalid OHLCV")
    tickers = list(dict.fromkeys(frame["ticker"].astype(str).str.upper()))
    with _connect() as conn, conn.cursor() as cur:
        cur.executemany("""INSERT INTO instrument(canonical_symbol)
            SELECT %s WHERE NOT EXISTS (
              SELECT 1 FROM instrument WHERE canonical_symbol=%s
            )""", [(t, t) for t in tickers])
        cur.execute("""SELECT canonical_symbol,instrument_id FROM instrument
            WHERE canonical_symbol = ANY(%s) ORDER BY instrument_id""", (tickers,))
        instrument_ids = {}
        for symbol, iid in cur.fetchall():
            instrument_ids.setdefault(str(symbol), iid)
        rows=[]
        for _, r in frame.iterrows():
            ticker=str(r["ticker"]).upper()
            payload={k:(None if pd.isna(v) else v) for k,v in r.items()
                     if k not in {"ticker","event_timestamp","ingested_at","Open","High","Low","Close","Volume"}}
            rows.append((instrument_ids[ticker],data_type,timeframe,r["event_timestamp"],r["ingested_at"],run_id,
                         provider,r.get("Open"),r.get("High"),r.get("Low"),r.get("Close"),r.get("Volume"),
                         __import__("json").dumps(payload,default=str)))
        before=0
        cur.execute("SELECT count(*) FROM market_observation WHERE warehouse_run_id=%s",(run_id,))
        before=cur.fetchone()[0]
        cur.executemany("""INSERT INTO market_observation
          (instrument_id,data_type,timeframe,event_timestamp,ingested_at,warehouse_run_id,
           provider,open,high,low,close,volume,payload)
          SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
          WHERE NOT EXISTS (
            SELECT 1 FROM (SELECT * FROM market_observation WHERE instrument_id=%s AND data_type=%s
              AND timeframe=%s AND event_timestamp=%s ORDER BY ingested_at DESC,observation_id DESC LIMIT 1) o
              WHERE o.provider=%s
              AND o.open IS NOT DISTINCT FROM %s AND o.high IS NOT DISTINCT FROM %s
              AND o.low IS NOT DISTINCT FROM %s AND o.close IS NOT DISTINCT FROM %s
              AND o.volume IS NOT DISTINCT FROM %s
          )""", [r + (r[0],r[1],r[2],r[3],r[6],r[7],r[8],r[9],r[10],r[11]) for r in rows])
        cur.execute("SELECT count(*) FROM market_observation WHERE warehouse_run_id=%s",(run_id,))
        return int(cur.fetchone()[0]-before)

def main():
    parser = argparse.ArgumentParser(description="Bitemporal PostgreSQL warehouse")
    parser.add_argument("--verify-health", action="store_true")
    parser.add_argument("--as-of", default="now", help="Reserved for health/PIT diagnostics")
    args = parser.parse_args()
    if args.verify_health:
        result = verify_health()
        print(f"BITEMPORAL_WAREHOUSE_AVAILABLE tls={result['tls']} tables={','.join(result['tables'])}")
        return
    parser.error("specify --verify-health")


if __name__ == "__main__":
    main()
