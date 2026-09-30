import os
import uuid
import pandas as pd
import pytest
from scanner import bitemporal_warehouse as pit


@pytest.mark.postgres_integration
def test_bulk_revision_idempotency_snapshot_and_atomicity():
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    from scanner.control_plane import migrate
    migrate()
    names=['BULK_'+uuid.uuid4().hex.upper() for _ in range(3)]
    rid=pit.start_run('test','bulk',{})
    ids=pit.resolve_instrument_ids(names)
    stamp=pd.Timestamp('2026-01-05T15:00Z')
    frame=pd.DataFrame([dict(ticker=t,event_timestamp=stamp,ingested_at=stamp,
        Open=10.,High=12.,Low=9.,Close=10.,Volume=100.) for t in names])
    assert pit.ingest_observations(frame,rid,'test','OHLCV','5m',instrument_ids=ids)==3
    assert pit.ingest_observations(frame,rid,'test','OHLCV','5m',instrument_ids=ids)==0
    with pit._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_current_snapshot()::text')
        visibility=cur.fetchone()[0]
    revision=frame.copy()
    revision['ingested_at']=stamp+pd.Timedelta(seconds=1)
    revision['Close']=11.
    assert pit.ingest_observations(revision,rid,'test','OHLCV','5m',instrument_ids=ids)==3
    frozen=pit.point_in_time(pit.PointInTimeRequirement('bulk',tuple(names),timeframe='5m',
                             as_of=pd.Timestamp.now(tz='UTC'),pg_snapshot=visibility))
    assert len(frozen)==3
    assert set(frozen.close.astype(float))=={10.}
    # A foreign-key failure anywhere in the set must leave no partial rows.
    broken=dict(ids);broken[names[-1]]=9223372036854775807
    revision['ingested_at']=stamp+pd.Timedelta(seconds=2)
    revision['Close']=10.5
    from psycopg.errors import ForeignKeyViolation
    with pytest.raises(ForeignKeyViolation):
        pit.ingest_observations(revision,rid,'test','OHLCV','5m',instrument_ids=broken)
    with pit._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM market_observation WHERE warehouse_run_id=%s',(rid,))
        assert cur.fetchone()[0]==6
