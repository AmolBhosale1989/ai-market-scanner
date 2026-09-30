from pathlib import Path
import subprocess
import textwrap

import pandas as pd
import pytest

from scanner import control_plane as cp, intraday, strategy_finalize as finalizer
from scanner.consumer_snapshot import _validated_anchor, capture_boundary


@pytest.mark.parametrize('lane', ['full', 'live'])
@pytest.mark.parametrize('failed', ['', 'broad_breakout', 'premarket', 'v3_live', 'theme_live',
                                    'sector_rotation', 'momentum_signals', 'order_flow_strategy'])
def test_shell_barrier_waits_for_all_producers(lane, failed):
    workflow = Path('.github/workflows/production.yml').read_text()
    body = textwrap.dedent(workflow.split('      - name: Execute complete dependency chain', 1)[1]
                           .split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
    shim = '''
GITHUB_ENV=/dev/null
python() {
  if [[ "$*" == *scanner.run_policy* ]]; then echo "$PIPELINE_MODE"; return 0; fi
  printf '%s\\n' "$*"
  if [[ "$*" == *"-m scanner.v3_live "* ]]; then sleep 0.05; fi
  if [[ -n "$FAILED" && "$*" == *"-m scanner.$FAILED"* ]]; then return 7; fi
  return 0
}
timeout() { while [[ "$1" == --* ]]; do shift; done; shift; "$@"; }
env() { while [[ "$1" == *=* ]]; do local "$1"; shift; done; "$@"; }
bash() { printf '%s\\n' "$*"; }
'''
    result = subprocess.run(['bash'], input=f'PIPELINE_MODE={lane}\nFAILED={failed}\n'+shim+body,
                            text=True, capture_output=True)
    trace = result.stdout
    if failed:
        assert result.returncode != 0
        assert '-m scanner.strategy_finalize' not in trace
        assert '--finalize --mode production' not in trace
    else:
        assert result.returncode == 0, result.stderr
        joined = trace.index('-m scanner.strategy_finalize')
        for name in ('v3_live', 'theme_live', 'sector_rotation', 'momentum', 'order_flow'):
            assert trace.index('stage-pass --name '+name) < joined
        if lane == 'full':
            assert trace.index('stage-pass --name v3_live') < trace.index('--finalize-prepared')
            assert trace.index('stage-pass --name daily_scan') < joined
        assert joined < trace.index('-m scanner.production_acceptance')


def test_deferred_empty_v3_does_not_write_recommendations_or_feed(monkeypatch, memory_control_plane):
    for name in ('build_performance_reports', 'build_empirical_calibration', '_write_monitor_health'):
        monkeypatch.setattr(intraday, name, lambda *a, **k: None)
    monkeypatch.setattr(intraday, '_write_recommendations', lambda *a: pytest.fail('early recommendations'))
    monkeypatch.setattr(intraday, 'build_product_feed', lambda: pytest.fail('early product feed'))
    intraday.run(input_frame=pd.DataFrame(columns=['stage']), defer_finalization=True)
    assert (memory_control_plane['run_id'], 'intraday_live') in memory_control_plane['datasets']


@pytest.mark.postgres_integration
@pytest.mark.parametrize('blocked', [None, 'v3_live', 'sector_rotation', 'momentum', 'order_flow',
                                     'theme_live', 'daily_scan', 'lineage'])
def test_real_finalize_requires_completed_current_run_producers(monkeypatch, blocked):
    import os
    if not os.getenv('DATABASE_URL'):
        pytest.skip('requires isolated PostgreSQL')
    cp.migrate()
    rid = cp.start_run('finalize-test')
    monkeypatch.setenv('PRODUCTION_RUN_ID', rid)
    anchor, visibility = capture_boundary()
    cp.write_dataset('warehouse_snapshot', pd.DataFrame([{
        'status': 'PASS', 'production_run_id': rid, 'as_of_utc': anchor.isoformat(),
        'pg_snapshot': visibility}]), run_id=rid)
    monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT', '1')
    _validated_anchor.cache_clear()
    try:
        cp.write_dataset('intraday_live', pd.DataFrame([{
            'ticker': 'AAA', 'live_trade_action': 'BUY / LIVE CONFIRMED',
            'monitor_state': 'LIVE_CONFIRMED'}]))
        for name in ('v3_live', 'sector_rotation', 'momentum', 'order_flow', 'theme_live', 'daily_scan'):
            cp.stage_started(name, 1)
            if name != blocked:
                cp.stage_finished(name)
        if blocked == 'lineage':
            with cp._connect() as conn, conn.cursor() as cur:
                cur.execute("""UPDATE dataset_version SET metadata='{}'::jsonb
                               WHERE pipeline_run_id=%s AND dataset_name='intraday_live'""", (rid,))
        if blocked:
            with pytest.raises(RuntimeError, match='STRATEGY_FINALIZE_'):
                finalizer.run(full=True)
            with pytest.raises(RuntimeError, match='DATASET_UNAVAILABLE'):
                cp.read_dataset('recommended_trades')
        else:
            result = finalizer.run(full=True)
            assert result['ticker'].tolist() == ['AAA']
            stored = cp.read_dataset('recommended_trades')
            assert stored['pg_snapshot'].eq(visibility).all()
            assert stored['production_run_id'].eq(rid).all()
    finally:
        _validated_anchor.cache_clear()
