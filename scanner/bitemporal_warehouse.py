from __future__ import annotations

import argparse
import os
import re
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
    period: str | None = None
    pg_snapshot: str | None = None


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


def period_start(period: str, as_of) -> pd.Timestamp:
    """Inclusive UTC calendar-date window; months/years use calendar offsets."""
    match = re.fullmatch(r"([1-9][0-9]*)(d|wk|mo|y)", str(period))
    if not match:
        raise ValueError(f"WAREHOUSE_PERIOD_INVALID: {period}")
    anchor = pd.Timestamp(as_of)
    if pd.isna(anchor) or anchor.tzinfo is None:
        raise ValueError("WAREHOUSE_PERIOD_ANCHOR_INVALID")
    count, unit = int(match[1]), match[2]
    field = {"d": "days", "wk": "weeks", "mo": "months", "y": "years"}[unit]
    return anchor.tz_convert("UTC").normalize() - pd.DateOffset(**{field: count})


def point_in_time(req: PointInTimeRequirement) -> pd.DataFrame:
    """Return only versions that were known by req.as_of. Never query future ingestion."""
    from .consumer_snapshot import consumer_anchor, resolve_pg_snapshot
    visibility = resolve_pg_snapshot(req.pg_snapshot)
    anchor = consumer_anchor()
    as_of = req.as_of or anchor or datetime.now(timezone.utc)
    if anchor is not None and pd.Timestamp(as_of) > anchor:
        raise RuntimeError('CONSUMER_SNAPSHOT_READ_AFTER_ANCHOR')
    tickers = [str(x).upper() for x in req.tickers if x]
    if not tickers:
        raise RuntimeError(f"WAREHOUSE_REQUIREMENT_INVALID: {req.consumer}")
    lower = period_start(req.period, as_of) if req.period is not None else None
    lower_clause = "AND o.event_timestamp >= %s" if lower is not None else ""
    visibility_clause = "AND pg_visible_in_snapshot(o.writer_xid, %s::pg_snapshot)" if visibility else ""
    from .catalogue_snapshot import instrument_cte
    catalogue, catalogue_params = instrument_cte(as_of, visibility)
    sql = f"""WITH {catalogue}, ranked AS (
      SELECT i.canonical_symbol AS ticker, o.*,
             row_number() OVER (
               PARTITION BY o.instrument_id,o.data_type,o.timeframe,o.event_timestamp
               ORDER BY o.ingested_at DESC,o.observation_id DESC
             ) AS version_rank
      FROM market_observation o
      JOIN visible_instrument i ON i.instrument_id=o.instrument_id
      WHERE i.canonical_symbol = ANY(%s)
        AND o.data_type=%s AND o.timeframe=%s
        AND o.event_timestamp <= %s
        AND o.ingested_at <= %s
        {lower_clause}
        {visibility_clause}
    )
    SELECT * FROM ranked WHERE version_rank=1 ORDER BY ticker,event_timestamp"""
    params = catalogue_params + (tickers, req.data_type, req.timeframe, event_cutoff(req.timeframe, as_of), as_of)
    if lower is not None:
        params += (lower,)
    if visibility:
        params += (visibility,)
    with _connect() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
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

def resolve_instrument_ids(tickers):
    """Run-local writer identity map; never a cache of mutable reader metadata."""
    wanted = list(dict.fromkeys(str(t).upper() for t in tickers))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT canonical_symbol,instrument_id FROM instrument "
                    "WHERE canonical_symbol = ANY(%s) ORDER BY instrument_id", (wanted,))
        ids = {}
        for symbol, iid in cur.fetchall():
            ids.setdefault(symbol, iid)
        missing = sorted(set(wanted)-set(ids))
        if missing:
            cur.execute("""INSERT INTO instrument(canonical_symbol)
                SELECT symbol FROM unnest(%s::text[]) AS symbol
                WHERE NOT EXISTS (SELECT 1 FROM instrument i WHERE i.canonical_symbol=symbol)
                RETURNING canonical_symbol,instrument_id""", (missing,))
            ids.update(cur.fetchall())
    return ids


def ingest_observations(frame, run_id, provider, data_type, timeframe, *, instrument_ids=None):
    """One set-based insert per bounded batch; revisions retain server writer_xid."""
    from psycopg.types.json import Jsonb
    from .ingestion_timing import BatchTiming
    with BatchTiming(run_id=run_id, provider=provider, timeframe=timeframe,
                     rows=len(frame), implementation='bulk') as timing:
        with timing.phase('validation'):
            missing = {'ticker', 'event_timestamp', 'ingested_at'}-set(frame.columns)
            if missing:
                raise RuntimeError(f"BITEMPORAL_INGEST_SCHEMA_FAILED: {sorted(missing)}")
            if frame.empty:
                return 0
            normalized = frame.rename(columns={c:c.lower() for c in
                                       ('Open','High','Low','Close','Volume')})
            if not {'open','high','low','close','volume'} <= set(normalized.columns) or invalid_rows(normalized).any():
                raise RuntimeError('BITEMPORAL_INGEST_QUALITY_FAILED: invalid OHLCV')
        # Preserve sequential revision semantics for unusual multi-revision inputs.
        if frame.assign(ticker=frame.ticker.astype(str).str.upper()).duplicated(['ticker','event_timestamp']).any():
            return _legacy_ingest_observations(frame, run_id, provider, data_type, timeframe)
        with timing.phase('instrument_lookup'):
            ids = instrument_ids if instrument_ids is not None else resolve_instrument_ids(frame.ticker)
            if set(frame.ticker.astype(str).str.upper())-set(ids):
                raise RuntimeError('BITEMPORAL_INSTRUMENT_CACHE_MISS')
        with timing.phase('row_preparation'):
            rows=[]
            excluded={'ticker','event_timestamp','ingested_at','Open','High','Low','Close','Volume'}
            for r in frame.to_dict('records'):
                rows.append(dict(instrument_id=ids[str(r['ticker']).upper()],
                    event_timestamp=pd.Timestamp(r['event_timestamp']).isoformat(),
                    ingested_at=pd.Timestamp(r['ingested_at']).isoformat(),
                    open=r.get('Open'), high=r.get('High'), low=r.get('Low'),
                    close=r.get('Close'), volume=r.get('Volume'),
                    payload={k:None if pd.isna(v) else v for k,v in r.items() if k not in excluded}))
        with timing.connection(_connect) as conn, conn.cursor() as cur:
            with timing.phase('insert_execution'):
                cur.execute("""INSERT INTO market_observation
                    (instrument_id,data_type,timeframe,event_timestamp,ingested_at,
                     warehouse_run_id,provider,open,high,low,close,volume,payload)
                    SELECT b.instrument_id,%s,%s,b.event_timestamp,b.ingested_at,%s,%s,
                           b.open,b.high,b.low,b.close,b.volume,b.payload
                    FROM jsonb_to_recordset(%s::jsonb) AS b(
                        instrument_id bigint,event_timestamp timestamptz,ingested_at timestamptz,
                        open numeric,high numeric,low numeric,close numeric,volume numeric,payload jsonb)
                    LEFT JOIN LATERAL (
                        SELECT o.provider,o.open,o.high,o.low,o.close,o.volume,o.observation_id
                        FROM market_observation o WHERE o.instrument_id=b.instrument_id
                        AND o.data_type=%s AND o.timeframe=%s AND o.event_timestamp=b.event_timestamp
                        ORDER BY o.ingested_at DESC,o.observation_id DESC LIMIT 1
                    ) old ON true
                    WHERE old.observation_id IS NULL OR old.provider IS DISTINCT FROM %s
                       OR old.open IS DISTINCT FROM b.open OR old.high IS DISTINCT FROM b.high
                       OR old.low IS DISTINCT FROM b.low OR old.close IS DISTINCT FROM b.close
                       OR old.volume IS DISTINCT FROM b.volume""",
                    (data_type,timeframe,run_id,provider,
                     Jsonb(rows, dumps=lambda v: __import__('json').dumps(v, default=str, allow_nan=False)),
                     data_type,timeframe,provider))
                inserted=cur.rowcount
            timing.inserted=inserted
        return inserted


def _legacy_ingest_observations(frame: pd.DataFrame, run_id: str, provider: str, data_type: str, timeframe: str):
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
