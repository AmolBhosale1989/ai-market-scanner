import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from scanner import control_plane as cp
from scanner.v4.contracts import EventType, MarketEvent
from scanner.v4.engine import MomentumEngine
from scanner.v4.store import PostgresEventStore


def event(ticker='AAA', price=10.5, at='2026-09-30T18:00:00+00:00'):
    return MomentumEngine.snapshot_event(dict(
        ticker=ticker, stage='ARMED', live_status='LIVE', live_price=price,
        entry_trigger=10, stop=9.5, effective_target=11.5,
        live_trigger_reached=True, live_above_vwap=True, intraday_rvol=2,
        negative_catalyst_risk=False, live_trade_action='BUY / LIVE CONFIRMED + CATALYST'), at)


def test_batch_matches_sequential_and_deduplicates_replay(tmp_path):
    inputs=[event(), event(price=12,at='2026-09-30T18:05:00+00:00'), event('BBB')]
    batch=MomentumEngine(PostgresEventStore(str(tmp_path/'batch')))
    serial=MomentumEngine(PostgresEventStore(str(tmp_path/'serial')))
    expected=[serial.process(item) for item in inputs]
    assert batch.process_many(inputs) == expected
    assert batch.store.load_states() == serial.store.load_states()
    assert batch.process_many(inputs) == []
    assert len(batch.store.load_events()) == 6


def test_one_reduction_for_cycle_and_empty_does_no_io(monkeypatch):
    store=PostgresEventStore('counted')
    original=store.reduce_events
    calls=[]
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(store,'reduce_events',counted)
    engine=MomentumEngine(store)
    assert engine.process_batch([]) == []
    engine.process_many([event(f'T{i}') for i in range(30)])
    assert len(calls) == 1
    assert len(store.load_states()) == 30


@pytest.fixture
def postgres_batch(monkeypatch):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    monkeypatch.delenv('PRODUCTION_RUN_ID', raising=False)
    return MomentumEngine(PostgresEventStore('batch-'+uuid.uuid4().hex))


@pytest.mark.postgres_integration
def test_postgres_batch_atomic_visibility_and_idempotency(postgres_batch):
    engine=postgres_batch
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_current_snapshot()::text')
        before=cur.fetchone()[0]
    inputs=[event(f'T{i}') for i in range(30)]
    assert len(engine.process_many(inputs)) == 30
    assert engine.process_many(inputs) == []
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*),bool_and(NOT pg_visible_in_snapshot(writer_xid,%s::pg_snapshot)) '
                    'FROM state_document WHERE namespace=%s',(before,engine.store.namespace))
        assert cur.fetchone() == (1,True)
        # Immutable event records do not have writer_xid; snapshot visibility
        # applies to the state revision. Verify event deduplication separately.
        cur.execute('SELECT count(*) FROM event_record WHERE namespace=%s',
                    (engine.store.namespace+'.events',))
        assert cur.fetchone()[0] == 60


@pytest.mark.postgres_integration
def test_postgres_failure_after_event_insert_rolls_back(monkeypatch,postgres_batch):
    original=cp._insert_event_rows
    def fail(cur, rows):
        original(cur,rows)
        # Another connection cannot see the uncommitted events or state.
        assert postgres_batch.store.load_events() == []
        assert postgres_batch.store.load_states() == {}
        raise RuntimeError('injected after insert')
    monkeypatch.setattr(cp,'_insert_event_rows',fail)
    with pytest.raises(RuntimeError,match='injected'):
        postgres_batch.process_many([event()])
    assert postgres_batch.store.load_states() == {}
    assert postgres_batch.store.load_events() == []


@pytest.mark.postgres_integration
def test_postgres_concurrent_batches_preserve_both_updates(postgres_batch):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda x:postgres_batch.process_many([event(x)]),['AAA','BBB']))
    assert all(len(result)==1 for result in results)
    assert len(postgres_batch.store.load_states()) == 2
    assert len(postgres_batch.store.load_events()) == 4


@pytest.mark.postgres_integration
def test_postgres_bulk_events_duplicate_counts(postgres_batch):
    store=postgres_batch.store
    item=event()
    assert store.append_events([item,item]) == 1
    assert store.append_events([item]) == 0

