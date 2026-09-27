import os
import uuid

import pandas as pd
import pytest

from scanner import control_plane as cp, consumer_snapshot as snapshot, intraday

# Exercise the real control-plane functions, not the in-memory fixture.
pytestmark = pytest.mark.postgres_integration
T0 = pd.Timestamp('2026-09-25T16:00:00Z')


@pytest.mark.parametrize('name', sorted(cp.V3_PROVENANCE_DATASETS))
@pytest.mark.parametrize('empty', [False, True])
def test_provenance_round_trip(monkeypatch, name, empty):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    rid = cp.start_run('test-v3-provenance')
    monkeypatch.setenv('PRODUCTION_RUN_ID', rid)
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: T0)
    source = pd.DataFrame() if empty else pd.DataFrame([{'ticker': 'AAA', 'score': 91.5}])
    receipt = cp.write_dataset(name, source)
    result = cp.read_dataset(name)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT metadata,content_hash FROM dataset_version WHERE dataset_version_id=%s',
                    (receipt['dataset_version_id'],))
        metadata, digest = cur.fetchone()
    assert metadata == receipt['metadata']
    assert metadata['as_of_utc'] == T0.isoformat()
    assert metadata['production_run_id'] == rid
    assert metadata['content_hash'] == digest == cp._hash(result.to_dict('records'))
    assert len(result) == len(source)
    if not empty:
        row = result.iloc[0].to_dict()
        signal_hash = row.pop('signal_content_hash')
        assert signal_hash == cp._hash(row)
        assert row['as_of_utc'] == T0.isoformat()
        assert row['production_run_id'] == rid
        assert 'as_of_utc' not in source.columns


def test_output_missing_anchor_blocks_before_write(monkeypatch):
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: None)
    monkeypatch.setattr(cp, '_connect', lambda: pytest.fail('write attempted'))
    with pytest.raises(RuntimeError, match='OUTPUT_ANCHOR_REQUIRED'):
        cp.write_dataset('recommended_trades', pd.DataFrame(), run_id=str(uuid.uuid4()))


def test_output_wrong_run_blocks(monkeypatch):
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'current')
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: T0)
    with pytest.raises(RuntimeError, match='OUTPUT_RUN_MISMATCH'):
        cp.write_dataset('intraday_live', pd.DataFrame(), run_id='other')


def test_stamp_recomputes_hash_for_current_anchor(monkeypatch):
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'current')
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: T0)
    stamped, meta = cp._v3_provenance([{'ticker': 'AAA', 'signal_content_hash': 'stale',
                                     'production_run_id': 'current', 'as_of_utc': T0.isoformat()}], 'current')
    row = stamped[0].copy()
    assert row.pop('signal_content_hash') == cp._hash(row)
    assert meta['content_hash'] == cp._hash(stamped)


def test_state_cutoff_excludes_later_mutations_and_includes_exact_boundary(monkeypatch):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    monkeypatch.delenv('PRODUCTION_RUN_ID', raising=False)
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: None)
    namespace = 'v3'
    key = 'cutoff-' + uuid.uuid4().hex
    with cp._connect() as conn, conn.cursor() as cur:
        for rev, at in enumerate([T0-pd.Timedelta(microseconds=1), T0,
                                   T0+pd.Timedelta(microseconds=1)], 1):
            cur.execute('INSERT INTO state_document(namespace,document_key,revision,payload,created_at) '
                        'VALUES (%s,%s,%s,%s::jsonb,%s)',
                        (namespace, key, rev, cp._canonical({'revision': rev}), at))
    assert cp.read_state(namespace, key, as_of=T0) == {'revision': 2}
    assert cp.read_state(namespace, key) == {'revision': 3}
    assert cp.read_state(namespace, key, default={}, as_of=T0-pd.Timedelta(seconds=1)) == {}
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'current')
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: T0)
    assert cp.read_state(namespace, key) == {'revision': 2}
    # Another committed revision must not change the replay at T0.
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('INSERT INTO state_document(namespace,document_key,revision,payload,created_at) '
                    'VALUES (%s,%s,4,%s::jsonb,%s)',
                    (namespace, key, cp._canonical({'revision': 4}), T0+pd.Timedelta(seconds=1)))
    assert cp.read_state(namespace, key, as_of=T0) == {'revision': 2}


@pytest.mark.parametrize('at,error', [(T0+pd.Timedelta(seconds=1), 'READ_AFTER_ANCHOR'),
                                    (pd.Timestamp('2026-09-25'), 'ANCHOR_INVALID')])
def test_invalid_state_anchor_fails_before_query(monkeypatch, at, error):
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: T0)
    monkeypatch.setattr(cp, '_connect', lambda: pytest.fail('query attempted'))
    with pytest.raises(RuntimeError, match=error):
        cp.read_state('v3', 'state', as_of=at)


def test_production_state_requires_anchor(monkeypatch):
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'current')
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: None)
    with pytest.raises(RuntimeError, match='STATE_ANCHOR_REQUIRED'):
        cp.read_state('v3', 'state')


def test_v3_all_state_reads_pass_anchor(monkeypatch):
    seen = []
    monkeypatch.setattr(intraday, 'consumer_anchor', lambda: T0)
    def read(namespace, key, default=None, *, as_of=None):
        seen.append((key, as_of))
        return default
    monkeypatch.setattr(intraday, 'read_state', read)
    monkeypatch.setattr(intraday, 'append_state', lambda *a, **k: None)
    monkeypatch.setattr(intraday, 'write_dataset', lambda *a, **k: None)
    intraday._load_state()
    intraday._write_state_transitions([])
    intraday._update_paper_journal(pd.DataFrame(), T0.isoformat())
    assert seen == [('market_hunt_state', T0), ('state_transitions', T0), ('paper_journal', T0)]


@pytest.mark.parametrize("field,value", [("production_run_id", "other"),
                                        ("as_of_utc", "2026-09-25T15:59:00Z")])
def test_output_rejects_conflicting_input_lineage(monkeypatch, field, value):
    monkeypatch.setenv("PRODUCTION_RUN_ID", "current")
    monkeypatch.setattr(snapshot, "consumer_anchor", lambda: T0)
    with pytest.raises(RuntimeError, match="V3_OUTPUT_INPUT_"):
        cp._v3_provenance([{"ticker": "AAA", field: value}], "current")
