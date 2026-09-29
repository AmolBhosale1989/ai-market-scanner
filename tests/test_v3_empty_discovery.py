import os

import pandas as pd
import pytest

from scanner import v3_live, intraday, control_plane as cp, strategy_finalize
from scanner.consumer_snapshot import capture_boundary, _validated_anchor


@pytest.mark.parametrize('bad', [None, [], pd.DataFrame({'price': [10]}),
                                 pd.DataFrame({'ticker': [None]}), pd.DataFrame({'ticker': [' ']})])
def test_invalid_discovery_remains_fatal(monkeypatch, bad):
    monkeypatch.setattr(v3_live, 'read_dataset', lambda name: bad)
    with pytest.raises(RuntimeError, match='V3_DISCOVERY_INVALID'):
        v3_live.run(reuse_current_broad_discovery=True, defer_finalization=True)


@pytest.mark.parametrize('error', ['CONTROL_PLANE_DATASET_UNAVAILABLE', 'CONTROL_PLANE_HASH_MISMATCH',
                                  'WAREHOUSE_STALE', 'CONSUMER_PG_SNAPSHOT_INVALID'])
def test_discovery_errors_are_not_swallowed(monkeypatch, error):
    def fail(*a, **k):
        raise RuntimeError(error)
    monkeypatch.setattr(v3_live, 'read_dataset', fail)
    with pytest.raises(RuntimeError, match=error):
        v3_live.run(reuse_current_broad_discovery=True, defer_finalization=True)


def quiet_auxiliary(monkeypatch):
    for name in ('build_performance_reports', 'build_empirical_calibration', '_write_monitor_health'):
        monkeypatch.setattr(intraday, name, lambda *a, **k: None)
    monkeypatch.setattr(v3_live, 'refresh_v3_candidates', lambda *a: pytest.fail('empty list requested price history'))


@pytest.mark.parametrize('reuse', [False, True])
def test_empty_v3_writes_all_outputs_without_early_recommendations(monkeypatch, memory_control_plane, capsys, reuse):
    quiet_auxiliary(monkeypatch)
    cp.write_dataset('broad_breakout_discovery', pd.DataFrame())
    monkeypatch.setattr(v3_live, 'run_broad_discovery', lambda **k: pd.DataFrame())
    monkeypatch.setattr(intraday, '_write_recommendations', lambda *a: pytest.fail('early recommendation write'))
    monkeypatch.setattr(intraday, 'build_product_feed', lambda: pytest.fail('early publication'))
    result = v3_live.run(reuse_current_broad_discovery=reuse, defer_finalization=True)
    assert result.empty
    assert 'V3_DISCOVERY_EMPTY' in capsys.readouterr().out
    for name in ('v3_live_discovery', 'v3_live_snapshot', 'intraday_live'):
        assert cp.read_dataset(name).empty
    with pytest.raises(RuntimeError, match='DATASET_UNAVAILABLE'):
        cp.read_dataset('recommended_trades')


@pytest.mark.postgres_integration
def test_empty_v3_real_postgres_provenance_and_finalization_barrier(monkeypatch):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('requires isolated PostgreSQL')
    cp.migrate()
    rid = cp.start_run('empty-v3-test')
    monkeypatch.setenv('PRODUCTION_RUN_ID', rid)
    anchor, snapshot = capture_boundary()
    cp.write_dataset('warehouse_snapshot', pd.DataFrame([{
        'status': 'PASS', 'production_run_id': rid, 'as_of_utc': anchor.isoformat(), 'pg_snapshot': snapshot}]))
    monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT', '1')
    _validated_anchor.cache_clear()
    quiet_auxiliary(monkeypatch)
    try:
        cp.write_dataset('broad_breakout_discovery', pd.DataFrame())
        assert v3_live.run(reuse_current_broad_discovery=True, defer_finalization=True).empty
        for name in ('v3_live_discovery', 'v3_live_snapshot', 'intraday_live'):
            assert cp.read_dataset(name).empty
            with cp._connect() as conn, conn.cursor() as cur:
                cur.execute('SELECT row_count,content_hash,metadata FROM dataset_version WHERE pipeline_run_id=%s AND dataset_name=%s', (rid, name))
                count, digest, meta = cur.fetchone()
            assert count == 0 and digest == cp._hash([])
            assert meta['as_of_utc'] == anchor.isoformat()
            assert meta['pg_snapshot'] == snapshot and meta['production_run_id'] == rid
        with pytest.raises(RuntimeError, match='PRODUCERS_NOT_READY'):
            strategy_finalize.run(full=True)
        with pytest.raises(RuntimeError, match='DATASET_UNAVAILABLE'):
            cp.read_dataset('recommended_trades')
        for name in ('v3_live', 'sector_rotation', 'momentum', 'order_flow', 'theme_live', 'daily_scan'):
            cp.stage_started(name, 1)
            cp.stage_finished(name)
        assert strategy_finalize.run(full=True).empty
        assert cp.read_dataset('recommended_trades').empty
    finally:
        _validated_anchor.cache_clear()
