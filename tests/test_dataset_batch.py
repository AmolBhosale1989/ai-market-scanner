import os
import uuid

import pandas as pd
import pytest

from scanner import control_plane as cp


@pytest.fixture
def real_run(monkeypatch):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    cp.migrate()
    rid = cp.start_run('test-dataset-batch')
    monkeypatch.setenv('PRODUCTION_RUN_ID', rid)
    return rid


@pytest.mark.postgres_integration
def test_bundle_commits_matching_hashes_empty_rows_and_read_own_writes(real_run):
    names = ['v4_test_'+uuid.uuid4().hex for _ in range(2)]
    with cp.batch_datasets():
        result = cp.write_dataset(names[0], pd.DataFrame([{'ticker':'AAA', 'value':None}]))
        cp.write_dataset(names[1], pd.DataFrame())
        assert result['pending']
        assert cp.read_dataset(names[0]).iloc[0]['ticker'] == 'AAA'
        with cp._connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM dataset_version WHERE pipeline_run_id=%s', (real_run,))
            assert cur.fetchone()[0] == 0
    assert cp.read_dataset(names[0]).iloc[0]['ticker'] == 'AAA'
    assert cp.read_dataset(names[1]).empty


@pytest.mark.postgres_integration
def test_bundle_insert_error_rolls_back_replacements_and_new_outputs(real_run, monkeypatch):
    a, b = ['v4_test_'+uuid.uuid4().hex for _ in range(2)]
    cp.write_dataset(a, pd.DataFrame([{'ticker':'OLD'}]))
    canonical = cp._canonical
    def broken_rows(value):
        if isinstance(value, list) and value and isinstance(value[0], dict) and 'ordinal' in value[0]:
            return 'invalid-json'
        return canonical(value)
    monkeypatch.setattr(cp, '_canonical', broken_rows)
    with pytest.raises(Exception, match='json'):
        with cp.batch_datasets():
            cp.write_dataset(a, pd.DataFrame([{'ticker':'NEW'}]))
            cp.write_dataset(b, pd.DataFrame([{'ticker':'NEW'}]))
    assert cp.read_dataset(a).iloc[0]['ticker'] == 'OLD'
    assert cp.read_dataset(b, required=False).empty


@pytest.mark.postgres_integration
def test_bundle_body_exception_discards_pending_outputs(real_run):
    name = 'v4_test_'+uuid.uuid4().hex
    with pytest.raises(RuntimeError, match='before commit'):
        with cp.batch_datasets():
            cp.write_dataset(name, pd.DataFrame([{'ticker':'AAA'}]))
            raise RuntimeError('before commit')
    assert cp.read_dataset(name, required=False).empty


def test_database_alerts_use_one_batch_and_retry_only_failed_sinks(monkeypatch):
    from scanner.v4 import alerting
    from scanner.v4.engine import Transition
    calls = []
    monkeypatch.setattr(alerting, 'append_events', lambda namespace, rows, **kw: calls.append(list(rows)))
    router = alerting.AlertRouter([alerting.DatabaseAlertSink()], namespace='batch-test')
    transitions = [Transition('signal-'+str(i), 'AAA', 'ARMED', 'TRIGGERED', str(i),
                              '2026-10-01T14:15:00Z') for i in range(20)]
    assert router.route(transitions) == (20, [])
    assert len(calls) == 1 and len(calls[0]) == 20
    assert router.route(transitions) == (0, [])
    assert len(calls) == 1
