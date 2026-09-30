import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from scanner import database as db
from scanner import catalyst_ingestion as ingestion
from scanner.catalyst_bulk import persist_alpha
from scanner.catalyst_db_bounds import bounded_persistence, CatalystDBTimeout
from scanner.catalyst_warehouse import catalyst_context

pytestmark = pytest.mark.postgres_integration


@pytest.fixture
def batch():
    if not os.getenv('DATABASE_URL'):
        pytest.skip('PostgreSQL required')
    from scanner import control_plane
    control_plane.migrate()
    provider = 'BULK_' + uuid.uuid4().hex[:12]
    symbols = ['ZZBULK80A', 'ZZBULK80B']
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO instrument(canonical_symbol) VALUES (%s) ON CONFLICT DO NOTHING',
                            [(t,) for t in symbols])
    return provider, symbols


def payload(value=1):
    event = SimpleNamespace(provider_event_id='calendar-1',catalyst_type='EARNINGS',
                            event_timestamp=datetime(2026,9,20,tzinfo=timezone.utc),payload={'value':value})
    return {'ZZBULK80A': SimpleNamespace(events=(event,),rejected_count=0)}


def write(batch, data=None):
    provider, symbols = batch
    adapter = SimpleNamespace(provider_name=provider,fetch_and_index=lambda **kw: data or {})
    return ingestion.ingest_alpha_vantage_batch(adapter,symbols,
            anchor=datetime.now(timezone.utc),lookforward=timedelta(days=14))


def test_bulk_idempotency_supersession_and_frozen_visibility(batch):
    provider, symbols = batch
    first = write(batch,payload())
    again = write(batch,payload())
    assert first[symbols[0]]['revisions'][0][1] == 'INSERTED'
    assert again[symbols[0]]['revisions'] == [(first[symbols[0]]['revisions'][0][0], 'UNCHANGED')]
    assert again[symbols[1]]['events'] == 0
    # Capture while the newer writer has already written but not committed.
    with bounded_persistence() as cur:
        result = persist_alpha(cur,provider,symbols,payload(2),datetime.now(timezone.utc))
        assert result[symbols[0]]['revisions'][0][1] == 'SUPERSEDED'
        with db.connection() as reader:
            anchor, snapshot = reader.execute('SELECT clock_timestamp(),pg_current_snapshot()::text').fetchone()
    old = catalyst_context(tickers=symbols,as_of=anchor,
        start_time=datetime(2026,9,1,tzinfo=timezone.utc),end_time=anchor,pg_snapshot=snapshot)
    assert [r['value'] for r in old[old.provider == provider].payload] == [1]
    with db.connection() as conn:
        anchor2,snapshot2 = conn.execute('SELECT clock_timestamp(),pg_current_snapshot()::text').fetchone()
        checks=conn.execute('SELECT ticker,result_status,verified_event_ids FROM catalyst_check WHERE provider=%s ORDER BY catalyst_check_id',(provider,)).fetchall()
    new = catalyst_context(tickers=symbols,as_of=anchor2,
        start_time=datetime(2026,9,1,tzinfo=timezone.utc),end_time=anchor2,pg_snapshot=snapshot2)
    assert [r['value'] for r in new[new.provider == provider].payload] == [2]
    assert len(checks) == 6
    assert all(status == 'NO_EVENT' and ids == [] for t,status,ids in checks if t == symbols[1])


def test_failure_after_evidence_insert_rolls_back_whole_batch(batch):
    provider,symbols=batch
    class FailAfterChecks:
        def __init__(self,cur): self.cur=cur
        def __getattr__(self,name): return getattr(self.cur,name)
        def executemany(self,sql,params):
            if 'UPDATE warehouse_run_log' in sql:
                raise RuntimeError('injected after evidence writes')
            return self.cur.executemany(sql,params)
    with pytest.raises(CatalystDBTimeout,match='DB_ERROR'):
        with bounded_persistence() as cur:
            persist_alpha(FailAfterChecks(cur),provider,symbols,payload(),datetime.now(timezone.utc))
    with db.connection() as conn:
        for table in ('warehouse_run_log','warehouse_catalyst','catalyst_check'):
            assert conn.execute(f'SELECT count(*) FROM {table} WHERE provider=%s',(provider,)).fetchone()[0] == 0


def test_concurrent_identical_batches_keep_one_revision(batch):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(write,batch,payload()) for _ in range(2)]
        results=[f.result(timeout=15) for f in futures]
    assert sorted(r[batch[1][0]]['revisions'][0][1] for r in results) == ['INSERTED','UNCHANGED']
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_catalyst WHERE provider=%s',(batch[0],)).fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM catalyst_check WHERE provider=%s',(batch[0],)).fetchone()[0] == 4


def test_large_empty_calendar_batch_records_each_check(batch):
    provider,_=batch
    symbols=[f'ZZB80{i:04d}' for i in range(1100)]
    with db.connection() as conn,conn.cursor() as cur:
        cur.executemany('INSERT INTO instrument(canonical_symbol) VALUES (%s) ON CONFLICT DO NOTHING',[(t,) for t in symbols])
    result=write((provider,symbols))
    assert len(result) == 1100
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM catalyst_check WHERE provider=%s AND result_status='NO_EVENT' AND rejected_count=0",(provider,)).fetchone()[0] == 1100
