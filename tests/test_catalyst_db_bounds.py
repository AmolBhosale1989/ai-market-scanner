import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from scanner import catalyst_db_bounds as bounds
from scanner import catalyst_ingestion as ingestion
from scanner import database as db


def test_pool_acquisition_timeout_is_explicit_and_redacted(monkeypatch, capsys):
    from psycopg_pool import PoolTimeout
    @contextmanager
    def exhausted(**kwargs):
        assert kwargs['timeout'] == 5
        raise PoolTimeout('secret connection string')
        yield
    monkeypatch.setattr(bounds, 'connection', exhausted)
    with pytest.raises(bounds.CatalystDBTimeout, match='DB_CONNECTION_TIMEOUT'):
        with bounds.bounded_persistence():
            pytest.fail('must not enter')
    output=capsys.readouterr().out
    assert 'phase=connection' in output
    assert 'secret' not in output


@pytest.fixture
def postgres():
    if not os.getenv('DATABASE_URL'):
        pytest.skip('PostgreSQL required')
    from scanner import control_plane
    control_plane.migrate()
    with db.connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS catalyst_deadline_test (id integer)')
        conn.execute('TRUNCATE catalyst_deadline_test')
    yield
    with db.connection() as conn:
        conn.execute('DROP TABLE catalyst_deadline_test')


def assert_empty():
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) FROM catalyst_deadline_test').fetchone()[0] == 0


@pytest.mark.postgres_integration
def test_statement_timeout_rolls_back_prior_write(postgres):
    with pytest.raises(bounds.CatalystDBTimeout, match='DB_STATEMENT_TIMEOUT'):
        with bounds.bounded_persistence(statement_ms=50) as cur:
            cur.execute('INSERT INTO catalyst_deadline_test VALUES (1)')
            cur.execute('SELECT pg_sleep(1)')
    assert_empty()


@pytest.mark.postgres_integration
def test_lock_timeout_rolls_back_prior_write(postgres):
    with db.connection() as holder:
        holder.execute('SELECT pg_advisory_xact_lock(7199881)')
        with pytest.raises(bounds.CatalystDBTimeout, match='DB_LOCK_TIMEOUT'):
            with bounds.bounded_persistence(lock_ms=50) as cur:
                cur.execute('INSERT INTO catalyst_deadline_test VALUES (1)')
                cur.execute('SELECT pg_advisory_xact_lock(7199881)')
    assert_empty()


@pytest.mark.postgres_integration
def test_deadline_cancels_long_sql_and_rolls_back(postgres):
    started=time.monotonic()
    with pytest.raises(bounds.CatalystDBTimeout, match='OVERALL_DEADLINE'):
        with bounds.bounded_persistence(deadline_seconds=.3, statement_ms=10000) as cur:
            cur.execute('INSERT INTO catalyst_deadline_test VALUES (1)')
            cur.execute('SELECT pg_sleep(5)')
    assert time.monotonic()-started < 4
    assert_empty()


@pytest.mark.postgres_integration
def test_deadline_stops_sequential_short_queries(postgres):
    with pytest.raises(bounds.CatalystDBTimeout, match='OVERALL_DEADLINE'):
        with bounds.bounded_persistence(deadline_seconds=.3, statement_ms=200) as cur:
            for _ in range(40):
                cur.execute('INSERT INTO catalyst_deadline_test VALUES (1)')
                cur.execute('SELECT pg_sleep(.03)')
    assert_empty()


@pytest.mark.postgres_integration
def test_success_and_timeout_settings_do_not_leak(postgres):
    with db.connection() as conn:
        previous=conn.execute('SHOW statement_timeout').fetchone()[0]
    with bounds.bounded_persistence(statement_ms=73) as cur:
        cur.execute('INSERT INTO catalyst_deadline_test VALUES (1)')
    time.sleep(.05)
    with db.connection() as conn:
        assert conn.execute('SHOW statement_timeout').fetchone()[0] == previous
        assert conn.execute('SELECT count(*) FROM catalyst_deadline_test').fetchone()[0] == 1


@pytest.mark.postgres_integration
def test_alpha_later_ticker_failure_rolls_back_entire_batch(postgres, capsys):
    with db.connection() as conn:
        conn.execute("INSERT INTO instrument(canonical_symbol) VALUES ('ZZALPHABOUND') ON CONFLICT DO NOTHING")
        before=conn.execute("SELECT count(*) FROM catalyst_check WHERE ticker='ZZALPHABOUND'").fetchone()[0]
        runs=conn.execute("SELECT count(*) FROM warehouse_run_log WHERE provider='BOUNDED_TEST'").fetchone()[0]
    adapter=Mock(provider_name='BOUNDED_TEST')
    adapter.fetch_and_index.return_value={}
    with pytest.raises(bounds.CatalystDBTimeout, match='DB_ERROR'):
        ingestion.ingest_alpha_vantage_batch(adapter,['ZZALPHABOUND','NO_SUCH_INSTRUMENT_71'],
            anchor=datetime.now(timezone.utc),lookforward=timedelta(days=14))
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM catalyst_check WHERE ticker='ZZALPHABOUND'").fetchone()[0] == before
        assert conn.execute("SELECT count(*) FROM warehouse_run_log WHERE provider='BOUNDED_TEST'").fetchone()[0] == runs
    assert 'CATALYST_PERSIST_COMPLETE' not in capsys.readouterr().out


@pytest.mark.postgres_integration
def test_real_pool_exhaustion_is_bounded(postgres):
    started=time.monotonic()
    with db.connection(), db.connection():
        with pytest.raises(bounds.CatalystDBTimeout, match='DB_CONNECTION_TIMEOUT'):
            with bounds.bounded_persistence(acquire_seconds=.1):
                pytest.fail('Pool must remain limited to two checkouts')
    assert time.monotonic()-started < 2
    assert_empty()
