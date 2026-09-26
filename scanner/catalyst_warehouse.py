from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Mapping

import pandas as pd

from .database import connection


def _canonical_payload(payload: Mapping) -> tuple[str, str]:
    encoded=json.dumps(payload,sort_keys=True,separators=(",",":"),default=str)
    return encoded,hashlib.sha256(encoded.encode()).hexdigest()


def ingest_catalyst_revision(*, provider: str, provider_event_id: str, ticker: str,
                             catalyst_type: str, event_timestamp: datetime,
                             warehouse_run_id: str, payload: Mapping) -> tuple[int,str]:
    provider=str(provider).strip()
    event_id=str(provider_event_id).strip()
    symbol=str(ticker).strip().upper()
    if not provider or not event_id or not symbol:
        raise RuntimeError("CATALYST_IDENTITY_INVALID")
    event=pd.Timestamp(event_timestamp)
    if event.tzinfo is None:
        raise RuntimeError("CATALYST_EVENT_TIME_NAIVE")
    event=event.tz_convert("UTC").to_pydatetime()
    payload_json,payload_hash=_canonical_payload(payload)
    with connection() as conn, conn.cursor() as cur:
        # Advisory locking also serializes the first-revision/absent-row case,
        # which SELECT ... FOR UPDATE alone cannot lock.
        logical=f"{provider}\x1f{event_id}\x1f{symbol}"
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(logical,))
        cur.execute("""SELECT catalyst_revision_id,payload_hash FROM warehouse_catalyst
                       WHERE provider=%s AND provider_event_id=%s AND ticker=%s
                         AND known_to IS NULL FOR UPDATE""",(provider,event_id,symbol))
        active=cur.fetchone()
        if active and active[1]==payload_hash:
            return int(active[0]),"UNCHANGED"
        cur.execute("""SELECT instrument_id FROM instrument
                       WHERE canonical_symbol=%s ORDER BY instrument_id LIMIT 1""",(symbol,))
        instrument=cur.fetchone()
        if instrument is None:
            raise RuntimeError(f"CATALYST_INSTRUMENT_UNKNOWN: {symbol}")
        # Use one database timestamp for both sides of the supersession boundary.
        cur.execute("SELECT clock_timestamp()")
        knowledge_time=cur.fetchone()[0]
        if active:
            cur.execute("""UPDATE warehouse_catalyst SET known_to=%s
                           WHERE catalyst_revision_id=%s AND known_to IS NULL""",
                        (knowledge_time,active[0]))
            if cur.rowcount != 1:
                raise RuntimeError("CATALYST_SUPERSESSION_RACE")
        cur.execute("""INSERT INTO warehouse_catalyst
          (provider,provider_event_id,instrument_id,ticker,catalyst_type,event_timestamp,
           known_from,warehouse_run_id,payload_hash,payload)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
          RETURNING catalyst_revision_id""",
          (provider,event_id,instrument[0],symbol,str(catalyst_type),event,knowledge_time,
           warehouse_run_id,payload_hash,payload_json))
        revision=int(cur.fetchone()[0])
    return revision,"SUPERSEDED" if active else "INSERTED"


def catalyst_context(*, tickers, as_of: datetime, start_time: datetime, end_time: datetime) -> pd.DataFrame:
    anchor=pd.Timestamp(as_of)
    start=pd.Timestamp(start_time)
    end=pd.Timestamp(end_time)
    if any(x.tzinfo is None for x in (anchor,start,end)):
        raise RuntimeError("CATALYST_PIT_TIME_NAIVE")
    anchor,start,end=(x.tz_convert("UTC") for x in (anchor,start,end))
    if end > anchor:
        raise RuntimeError("CATALYST_LOOKAHEAD_BLOCKED")
    wanted=list(dict.fromkeys(str(x).upper() for x in tickers if x))
    if not wanted:
        raise RuntimeError("CATALYST_PIT_TICKERS_REQUIRED")
    sql="""SELECT ticker,provider,provider_event_id,catalyst_type,event_timestamp,
                  known_from,known_to,payload_hash,payload
           FROM warehouse_catalyst
           WHERE ticker=ANY(%s) AND event_timestamp BETWEEN %s AND %s
             AND known_from<=%s AND (known_to IS NULL OR known_to>%s)
           ORDER BY ticker,event_timestamp,provider,provider_event_id"""
    with connection() as conn:
        return pd.read_sql_query(sql,conn,params=(wanted,start,end,anchor,anchor))
