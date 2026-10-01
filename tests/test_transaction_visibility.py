import os
import uuid

import pandas as pd
import pytest

from scanner import consumer_snapshot as snapshot, control_plane as cp
from scanner import bitemporal_warehouse as pit, warehouse_gate as gate


@pytest.mark.parametrize('value', [None, '', 'bad', '20:10:', '10:20:20', '10:20:12,11'])
def test_invalid_visibility_rejected(value):
    with pytest.raises(RuntimeError, match='PG_SNAPSHOT'):
        snapshot.validate_pg_snapshot(value)


def test_old_manifest_cannot_silently_fall_back_to_timestamps(monkeypatch):
    snapshot._validated_anchor.cache_clear()
    monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT', '1')
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'legacy')
    monkeypatch.setattr(cp, 'read_dataset', lambda *a, **k: pd.DataFrame([{
        'status': 'PASS', 'production_run_id': 'legacy', 'as_of_utc': '2026-01-01T00:00Z'}]))
    with pytest.raises(RuntimeError, match='PG_SNAPSHOT'):
        snapshot.consumer_anchor()


def test_manifest_pair_and_mismatch(monkeypatch):
    snapshot._validated_anchor.cache_clear()
    monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT', '1')
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'paired')
    monkeypatch.setattr(cp, 'read_dataset', lambda *a, **k: pd.DataFrame([{
        'status': 'PASS', 'production_run_id': 'paired', 'as_of_utc': '2026-01-01T00:00Z',
        'pg_snapshot': '10:20:12'}]))
    assert snapshot.consumer_anchor() == pd.Timestamp('2026-01-01T00:00Z')
    assert snapshot.consumer_pg_snapshot() == '10:20:12'
    with pytest.raises(RuntimeError, match='MISMATCH'):
        snapshot.resolve_pg_snapshot('10:20:13')


def test_gate_captures_and_uses_same_visibility_for_every_tier(monkeypatch):
    at = pd.Timestamp('2026-09-25T16:00Z')
    visibility = '10:20:12'
    seen = []
    monkeypatch.setattr(gate, 'verify_health', lambda: None)
    monkeypatch.setattr(snapshot, 'capture_boundary', lambda **kwargs: (at, visibility))
    tiers = [gate.CoverageTier(n, ('AAA',), '5m', 1., 1, 10) for n in ('A', 'B')]
    def build_tiers(master, live):
        assert master == live == ('AAA',)
        return tiers
    monkeypatch.setattr(gate, 'build_tiers', build_tiers)
    monkeypatch.setattr('scanner.catalogue_snapshot.read_frozen_catalogue', lambda *a, **k: pd.DataFrame({'ticker': ['AAA']}))
    def coverage(tier, anchor, pg_snapshot):
        seen.append((anchor, pg_snapshot))
        return pd.DataFrame([dict(ticker='AAA', event_timestamp=at-pd.Timedelta(minutes=5),
                                  ingested_at=at, bars=10, invalid_bars=0)])
    monkeypatch.setattr(gate, 'coverage_frame', coverage)
    result = gate.run(pd.DataFrame({'ticker': ['AAA']}), pd.DataFrame({'ticker': ['AAA']}))
    # Overlapping tiers read their identical timeframe once at the same boundary.
    assert seen == [(at, visibility)]
    assert result['pg_snapshot'] == visibility
    replay = gate.run(pd.DataFrame({'ticker': ['FUTURE']}), pd.DataFrame({'ticker': ['FUTURE']}),
                      as_of=at, pg_snapshot=visibility)
    assert replay['master_catalogue_hash'] == result['master_catalogue_hash']
    with pytest.raises(RuntimeError, match='PG_SNAPSHOT'):
        gate.run(pd.DataFrame({'ticker': ['AAA']}), pd.DataFrame({'ticker': ['AAA']}), as_of=at)


@pytest.mark.postgres_integration
@pytest.mark.parametrize('savepoint', [False, True])
@pytest.mark.parametrize('commit', [False, True])
def test_late_commit_cannot_change_saved_ohlcv_or_state_snapshot(monkeypatch, savepoint, commit):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    import psycopg
    monkeypatch.delenv('WAREHOUSE_CONSUMER_SNAPSHOT', raising=False)
    monkeypatch.delenv('PRODUCTION_RUN_ID', raising=False)
    cp.migrate()
    symbol = 'SNAP_' + uuid.uuid4().hex.upper()
    rid = pit.start_run('test', 'snapshot', {})
    event = pd.Timestamp('2026-01-05T15:00Z')
    known = event+pd.Timedelta(minutes=5)
    pit.ingest_observations(pd.DataFrame([dict(ticker=symbol, event_timestamp=event,
        ingested_at=known, Open=10., High=12., Low=9., Close=10., Volume=100.)]), rid, 'test', 'OHLCV', '5m')
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO state_document(namespace,document_key,revision,payload,created_at) '
                    'VALUES (%s,%s,1,%s::jsonb,%s)', (symbol, 'state', '{"v":1}', known))
    writer = psycopg.connect(os.environ['DATABASE_URL'], sslmode=os.getenv('PGSSLMODE', 'require'))
    try:
        with writer.cursor() as cur:
            cur.execute('SELECT pg_current_xact_id()::text')
            writer_xid = cur.fetchone()[0]
            if savepoint:
                cur.execute('SAVEPOINT child')
            cur.execute('INSERT INTO market_observation '
                '(instrument_id,data_type,timeframe,event_timestamp,ingested_at,warehouse_run_id,provider,open,high,low,close,volume,payload) '
                'SELECT instrument_id,data_type,timeframe,event_timestamp,%s,warehouse_run_id,provider,open,high,low,11,volume,payload '
                'FROM market_observation WHERE warehouse_run_id=%s RETURNING writer_xid::text',
                (known+pd.Timedelta(seconds=1), rid))
            assert cur.fetchone()[0] == writer_xid
            cur.execute('INSERT INTO state_document(namespace,document_key,revision,payload,created_at) '
                        'VALUES (%s,%s,2,%s::jsonb,%s)', (symbol, 'state', '{"v":2}', known))
            if savepoint:
                cur.execute('RELEASE SAVEPOINT child')
        # Advance another top-level transaction while the writer remains in flight.
        with cp._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT pg_current_xact_id()')
        at, visibility = snapshot.capture_boundary()
        assert writer_xid in visibility.split(':')[2].split(',')
        requirement = pit.PointInTimeRequirement('replay', (symbol,), timeframe='5m', as_of=at,
                                                pg_snapshot=visibility)
        before = pit.point_in_time(requirement)
        assert before['close'].tolist() == [10.]
        assert cp.read_state(symbol, 'state', as_of=at, pg_snapshot=visibility) == {'v': 1}
        writer.commit() if commit else writer.rollback()
        after = pit.point_in_time(requirement)
        pd.testing.assert_frame_equal(before, after)
        assert cp.read_state(symbol, 'state', as_of=at, pg_snapshot=visibility) == {'v': 1}
        tier = gate.CoverageTier('TEST', (symbol,), '5m', 1., 1, 10)
        assert gate.coverage_frame(tier, at, visibility).iloc[0]['ingested_at'] == known
        # A fresh boundary sees a committed revision, but never an aborted one.
        later, new_visibility = snapshot.capture_boundary()
        fresh = pit.point_in_time(pit.PointInTimeRequirement('fresh', (symbol,), timeframe='5m',
            as_of=later, pg_snapshot=new_visibility))
        assert fresh['close'].tolist() == ([11.] if commit else [10.])
        # Physical freezing must not rewrite the durable writer identity.
        with psycopg.connect(os.environ['DATABASE_URL'], sslmode=os.getenv('PGSSLMODE', 'require'), autocommit=True) as maintenance:
            maintenance.execute('VACUUM (FREEZE) market_observation')
        pd.testing.assert_frame_equal(before, pit.point_in_time(requirement))
    finally:
        writer.close()
