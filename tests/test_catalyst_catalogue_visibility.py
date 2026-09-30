import os
import uuid

import pandas as pd
import pytest

from scanner import control_plane as cp, bitemporal_warehouse as pit
from scanner import consumer_snapshot as boundary, catalyst_warehouse as catalysts
from scanner.catalyst_pipeline import verify_coverage, OPTIONAL_PROVIDERS, REQUIRED_PROVIDERS
from scanner.catalogue_snapshot import instrument_cte, read_frozen_catalogue
from scanner.warehouse_gate import coverage_frame, CoverageTier

pytestmark = pytest.mark.postgres_integration


@pytest.fixture
def database(monkeypatch):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    import psycopg
    monkeypatch.delenv('PRODUCTION_RUN_ID', raising=False)
    monkeypatch.delenv('WAREHOUSE_CONSUMER_SNAPSHOT', raising=False)
    cp.migrate()
    return lambda: psycopg.connect(os.environ['DATABASE_URL'], sslmode=os.getenv('PGSSLMODE', 'require'))


def setup_symbol():
    symbol = 'VIS_' + uuid.uuid4().hex.upper()
    rid = pit.start_run('TEST', 'visibility', {})
    event = pd.Timestamp('2026-01-05T15:00Z')
    pit.ingest_observations(pd.DataFrame([dict(ticker=symbol, event_timestamp=event,
        ingested_at=event, Open=10., High=12., Low=9., Close=11., Volume=100.)]), rid, 'TEST', 'OHLCV', '5m')
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT instrument_id FROM instrument WHERE canonical_symbol=%s', (symbol,))
        iid = cur.fetchone()[0]
    return symbol, iid, rid, event


@pytest.mark.parametrize('commit', [True, False])
def test_late_catalyst_evidence_and_correction_stay_invisible(database, commit, capsys):
    symbol, iid, rid, event = setup_symbol()
    catalysts.ingest_catalyst_revision(provider='TEST', provider_event_id='evt', ticker=symbol,
        catalyst_type='NEWS', event_timestamp=event, warehouse_run_id=rid, payload={'headline': 'original'})
    writer = database()
    try:
        with writer.cursor() as cur:
            cur.execute('SELECT pg_current_xact_id()')
            cur.execute('SAVEPOINT provider_batch')
            catalysts._ingest_revision_cursor(cur, provider='TEST', provider_event_id='evt', ticker=symbol,
                catalyst_type='NEWS', event_timestamp=event, warehouse_run_id=rid, payload={'headline': 'late revision'})
            for provider in REQUIRED_PROVIDERS+OPTIONAL_PROVIDERS:
                cur.execute('''INSERT INTO catalyst_check
                    (provider,instrument_id,ticker,checked_at,warehouse_run_id,result_status,event_count)
                    VALUES (%s,%s,%s,clock_timestamp(),%s,'NO_EVENT',0)''', (provider, iid, symbol, rid))
            cur.execute('RELEASE SAVEPOINT provider_batch')
        at, visibility = boundary.capture_boundary()
        kwargs = dict(tickers=[symbol], as_of=at, start_time=event-pd.Timedelta(days=1),
                      end_time=at, pg_snapshot=visibility)
        before = catalysts.catalyst_context(**kwargs)
        assert before.iloc[0]['payload']['headline'] == 'original'
        assert catalysts.latest_catalyst_checks(tickers=[symbol], as_of=at, pg_snapshot=visibility).empty
        with pytest.raises(RuntimeError,match='CATALYST_COVERAGE_INCOMPLETE'):
            verify_coverage([symbol], anchor=at, pg_snapshot=visibility)
        writer.commit() if commit else writer.rollback()
        pd.testing.assert_frame_equal(before, catalysts.catalyst_context(**kwargs))
        assert catalysts.latest_catalyst_checks(tickers=[symbol], as_of=at, pg_snapshot=visibility).empty
        with pytest.raises(RuntimeError,match='CATALYST_COVERAGE_INCOMPLETE'):
            verify_coverage([symbol], anchor=at, pg_snapshot=visibility)
        later, new_visibility = boundary.capture_boundary()
        latest = catalysts.catalyst_context(**dict(kwargs, as_of=later, end_time=later, pg_snapshot=new_visibility))
        assert latest.iloc[0]['payload']['headline'] == ('late revision' if commit else 'original')
        if commit:
            assert verify_coverage([symbol], anchor=later, pg_snapshot=new_visibility)
            assert "CATALYST_COVERAGE_WARNING" not in capsys.readouterr().out
    finally:
        writer.close()


def catalogue_row(iid, at, visibility):
    cte, params = instrument_cte(at, visibility)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute(f'WITH {cte} SELECT canonical_symbol,currency,active FROM visible_instrument WHERE instrument_id=%s', params+(iid,))
        return cur.fetchone()


@pytest.mark.parametrize('commit', [True, False])
def test_catalogue_rename_metadata_delisting_preserves_old_join(database, commit):
    symbol, iid, rid, event = setup_symbol()
    renamed = symbol + '_NEW'
    writer = database()
    try:
        with writer.cursor() as cur:
            cur.execute('UPDATE instrument SET canonical_symbol=%s,currency=%s,active=FALSE WHERE instrument_id=%s',
                        (renamed, 'EUR', iid))
        at, visibility = boundary.capture_boundary()
        requirement = pit.PointInTimeRequirement('catalogue', (symbol,), timeframe='5m', as_of=at, pg_snapshot=visibility)
        before = pit.point_in_time(requirement)
        assert catalogue_row(iid, at, visibility) == (symbol, None, True)
        writer.commit() if commit else writer.rollback()
        pd.testing.assert_frame_equal(before, pit.point_in_time(requirement))
        assert catalogue_row(iid, at, visibility) == (symbol, None, True)
        assert coverage_frame(CoverageTier('TEST', (symbol,), '5m', 1., 1, 10), at, visibility).iloc[0]['ticker'] == symbol
        later, new_visibility = boundary.capture_boundary()
        assert catalogue_row(iid, later, new_visibility) == ((renamed, 'EUR', False) if commit else (symbol, None, True))
    finally:
        writer.close()


def test_catalogue_delete_keeps_historical_identity_and_conflicts_make_no_ghosts(database):
    symbol = 'DELETE_' + uuid.uuid4().hex.upper()
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO instrument(canonical_symbol) VALUES (%s) RETURNING instrument_id', (symbol,))
        iid = cur.fetchone()[0]
        cur.execute('INSERT INTO instrument(canonical_symbol) VALUES (%s) ON CONFLICT DO NOTHING', (symbol,))
    at, visibility = boundary.capture_boundary()
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM instrument_catalogue_revision WHERE canonical_symbol=%s', (symbol,))
        assert cur.fetchone()[0] == 1
        cur.execute('DELETE FROM instrument WHERE instrument_id=%s', (iid,))
    assert catalogue_row(iid, at, visibility) == (symbol, None, True)
    later, new_visibility = boundary.capture_boundary()
    assert catalogue_row(iid, later, new_visibility) is None


def test_universe_capture_excludes_inflight_rewrite_and_survives_commit(database, monkeypatch):
    rid = cp.start_run('catalogue-capture')
    for name in ('master_universe', 'live_universe', 'tradable_universe'):
        cp.write_dataset(name, pd.DataFrame({'ticker': ['BEFORE']}), run_id=rid)
    writer = database()
    try:
        with writer.cursor() as cur:
            cur.execute('SELECT dataset_version_id FROM dataset_version WHERE pipeline_run_id=%s AND dataset_name=%s',
                        (rid, 'live_universe'))
            version = cur.fetchone()[0]
            cur.execute('UPDATE dataset_row SET payload=%s::jsonb WHERE dataset_version_id=%s',
                        (cp._canonical({'ticker': 'AFTER'}), version))
            cur.execute('UPDATE dataset_version SET content_hash=%s WHERE dataset_version_id=%s',
                        (cp._hash([{'ticker': 'AFTER'}]), version))
        at, visibility = boundary.capture_boundary(run_id=rid)
        writer.commit()
        cp.write_dataset('warehouse_snapshot', pd.DataFrame([{'status':'PASS', 'production_run_id':rid,
            'as_of_utc':at.isoformat(), 'pg_snapshot':visibility}]), run_id=rid)
        monkeypatch.setenv('PRODUCTION_RUN_ID', rid)
        monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT', '1')
        assert cp.read_dataset('live_universe')['ticker'].tolist() == ['BEFORE']
        assert read_frozen_catalogue('live_universe', rid, at, visibility)['ticker'].tolist() == ['BEFORE']
    finally:
        writer.close()


def test_immutable_evidence_and_dimension_history(database):
    symbol, iid, rid, event = setup_symbol()
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO instrument_dimension_history
            (instrument_id,attribute_name,attribute_value,event_timestamp,ingested_at,warehouse_run_id)
            VALUES (%s,'split','2'::jsonb,%s,clock_timestamp(),%s)''', (iid, event, rid))
    with pytest.raises(Exception, match='SNAPSHOT_INPUT_APPEND_ONLY'):
        with cp._connect() as conn, conn.cursor() as cur:
            cur.execute('UPDATE instrument_dimension_history SET attribute_value=3::text::jsonb WHERE instrument_id=%s', (iid,))
