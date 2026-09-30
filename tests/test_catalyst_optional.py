from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from scanner import catalyst_policy as policy, catalyst_pipeline as gate
from scanner.main import _final_decision


def test_production_collects_alpha_without_enabling_strategy_enrichment():
    workflow = Path('.github/workflows/production.yml').read_text()
    assert 'CATALYST_MODE: disabled' in workflow
    stage65 = next(x for x in workflow.splitlines() if 'stage catalyst_ingest 65' in x)
    assert "timeout --kill-after=5s 150s" in stage65
    assert "env CATALYST_MODE=optional python -m scanner.catalyst_pipeline --ingest-only --mandatory-only" in stage65
    assert '--catalysts' not in workflow


@pytest.mark.parametrize('value', [None, pd.NA, float('nan'), False, 'false'])
def test_null_catalysts_preserve_technical_decision(value):
    row = {'decision': 'BUY / CONFIRMED', 'stage': 'CONFIRMED',
           'catalyst_gate_ok': value, 'negative_catalyst_risk': value, 'catalyst_score': value}
    assert _final_decision(row) == 'BUY / CONFIRMED'
    out = policy.normalize(pd.DataFrame([row]))
    assert out.iloc[0]['catalyst_status'] == 'unavailable'
    assert pd.isna(out.iloc[0]['catalyst_score'])
    assert not out.iloc[0]['catalyst_fresh']
    assert not out.iloc[0]['negative_catalyst_risk']


def test_optional_known_negative_still_vetoes():
    assert _final_decision({'decision': 'BUY / CONFIRMED', 'stage': 'CONFIRMED',
                           'catalyst_gate_ok': True, 'negative_catalyst_risk': True}) == 'NO TRADE / NEGATIVE CATALYST'


def test_disabled_enrichment_never_resolves_provider_state(monkeypatch):
    from scanner import catalysts, consumer_snapshot
    monkeypatch.setenv('CATALYST_MODE', 'disabled')
    monkeypatch.setattr(consumer_snapshot, 'consumer_anchor', lambda: pd.Timestamp('2026-09-28T17:00Z'))
    monkeypatch.setattr(consumer_snapshot, 'consumer_pg_snapshot', lambda: '10:10:')
    monkeypatch.setattr('scanner.catalyst_contract.resolve_catalyst_state', lambda **k: pytest.fail('Catalyst read'))
    out = catalysts.enrich_candidates(pd.DataFrame({'ticker': ['AAA']}), 1)
    assert out.iloc[0]['catalyst_availability'] == 'unavailable'


def test_disabled_enrichment_does_not_disable_required_alpha(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setenv('CATALYST_MODE','disabled')
    conn=MagicMock(); cur=conn.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value=[]
    monkeypatch.setattr('scanner.database.connection',lambda:conn)
    t0=datetime(2026,9,28,tzinfo=timezone.utc)
    with pytest.raises(RuntimeError,match='CATALYST_COVERAGE_INCOMPLETE'):
        gate.verify_coverage(['AAA'],anchor=t0,pg_snapshot='10:10:')
    cur.fetchall.return_value=[('AAA','ALPHA_VANTAGE',t0)]
    assert gate.verify_coverage(['AAA'],anchor=t0,pg_snapshot='10:10:')


@pytest.mark.parametrize('bad', ['daily_only', 'failed', 'wrong_time', 'wrong_snapshot', None])
def test_price_gate_remains_strict(monkeypatch, bad):
    t0 = pd.Timestamp('2026-09-28T17:00Z')
    tiers = [{'tier': name, 'status': 'PASS'} for name in
             ['MASTER_DAILY', 'CRITICAL_DAILY', 'CRITICAL_INTRADAY', 'THEME_INTRADAY', 'LIVE_INTRADAY']]
    row = {'status': 'PASS', 'as_of_utc': t0.isoformat(), 'pg_snapshot': '10:10:', 'tiers': tiers}
    if bad == 'daily_only': row['tiers'] = tiers[:2]
    if bad == 'failed': tiers[-1]['status'] = 'FAIL'
    if bad == 'wrong_time': row['as_of_utc'] = '2026-09-28T16:00Z'
    if bad == 'wrong_snapshot': row['pg_snapshot'] = '9:9:'
    monkeypatch.setattr('scanner.consumer_snapshot.consumer_pg_snapshot', lambda: '10:10:')
    monkeypatch.setattr(gate, 'read_dataset', lambda name: pd.DataFrame([row]))
    if bad:
        with pytest.raises(RuntimeError, match='PRICE_SNAPSHOT_INCOMPLETE'):
            gate.validate_price_snapshot(t0)
    else:
        gate.validate_price_snapshot(t0)


def test_live_nulls_use_price_history_and_keep_price_failure_strict(monkeypatch):
    from scanner import live
    frame = pd.DataFrame([{'ticker': 'AAA', 'stage': 'ARMED', 'final_score': 90,
                           'catalyst_score': pd.NA, 'negative_catalyst_risk': pd.NA}])
    calls = []
    monkeypatch.setattr(live, 'warehouse_frames', lambda *a, **k: {'AAA': pd.DataFrame()})
    def analyze(**kwargs):
        calls.append(kwargs)
        return {'live_status': 'NO INTRADAY DATA'}
    monkeypatch.setattr(live, 'analyze_live_candidate', analyze)
    out = live.enrich_live_candidates(frame)
    assert len(calls) == 1 and calls[0]['negative_catalyst_risk'] is False
    assert calls[0]['catalyst_score'] == 0
    assert out.iloc[0]['catalyst_status'] == 'unavailable'


def test_output_contract_normalizes_before_hashing():
    source = Path('scanner/control_plane.py').read_text()
    start = source.index('def write_dataset(')
    body = source[start:]
    assert body.index('frame = normalize(frame)') < body.index('digest = _hash(records)')
    assert {'recommended_trades', 'intraday_live', 'daily_prepared_candidates',
            'v3_live_snapshot'}.issubset(policy.OUTPUT_DATASETS)


def test_momentum_and_order_flow_ignore_null_enrichment(monkeypatch, memory_control_plane):
    from scanner import momentum_signals, order_flow_strategy, control_plane as cp
    frame = pd.DataFrame([{'ticker': 'AAA', 'price': 10., 'stop': 9.7,
        'signal': 'MOMENTUM BUY', 'theme_rotation_score': 80., 'rel_vs_spy_pct': 2.,
        'order_flow_score': 80., 'buy_pressure_pct': 70., 'intraday_rvol': 2.,
        'above_vwap': True, 'above_or_high': True, 'volume_imbalance_proxy': 10.,
        'risk_pct': 3., 'day_change_pct': 4.}])
    now = datetime(2026, 9, 28, 17, tzinfo=timezone.utc)
    first = momentum_signals._write_outputs(frame, now, '2026-09-28', 1)
    first_flow = order_flow_strategy.run()
    nulls = frame.assign(catalyst_score=pd.NA, catalyst_headline=None, negative_catalyst_risk=pd.NA)
    second = momentum_signals._write_outputs(nulls, now, '2026-09-28', 1)
    second_flow = order_flow_strategy.run()
    assert first['signal'].tolist() == second['signal'].tolist()
    assert first_flow['order_flow_strategy_signal'].tolist() == second_flow['order_flow_strategy_signal'].tolist()
    assert first_flow['order_flow_strategy_score'].tolist() == second_flow['order_flow_strategy_score'].tolist()
    assert second_flow.iloc[0]['catalyst_availability'] == 'unavailable'


@pytest.mark.postgres_integration
def test_real_output_json_preserves_unavailable_types_and_hash(monkeypatch):
    import os
    if not os.getenv('DATABASE_URL'):
        pytest.skip('requires isolated PostgreSQL')
    from scanner import control_plane as cp
    from scanner.consumer_snapshot import capture_boundary, _validated_anchor
    cp.migrate()
    rid = cp.start_run('optional-output-test')
    monkeypatch.setenv('PRODUCTION_RUN_ID', rid)
    monkeypatch.setenv('CATALYST_MODE', 'disabled')
    anchor, visibility = capture_boundary()
    cp.write_dataset('warehouse_snapshot', pd.DataFrame([{
        'status': 'PASS', 'production_run_id': rid, 'as_of_utc': anchor.isoformat(),
        'pg_snapshot': visibility}]))
    monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT', '1')
    _validated_anchor.cache_clear()
    try:
        for name in ('daily_prepared_candidates', 'v3_live_snapshot', 'intraday_live', 'recommended_trades'):
            cp.write_dataset(name, pd.DataFrame([{'ticker': 'AAA', 'catalyst_score': None,
                'negative_catalyst_risk': None, 'price': 10.}]))
            row = cp.read_dataset(name).iloc[0]
            assert row['catalyst_status'] == 'unavailable'
            assert row['catalyst_availability'] == 'unavailable'
            assert pd.isna(row['catalyst_score'])
            assert not row['catalyst_fresh']
            assert row['price'] == 10.
            if name in cp.V3_PROVENANCE_DATASETS:
                assert pd.Timestamp(row['as_of_utc']) == anchor
    finally:
        _validated_anchor.cache_clear()


def test_daily_finalization_accepts_technical_buy_but_retains_other_gates():
    from scanner.main import _recommendation_mask
    frame = pd.DataFrame([
        {'universal_10pct_gate': True, 'live_trade_action': 'BUY / LIVE CONFIRMED', 'final_decision': 'BUY / CONFIRMED'},
        {'universal_10pct_gate': False, 'live_trade_action': 'BUY / LIVE CONFIRMED', 'final_decision': 'BUY / CONFIRMED'},
        {'universal_10pct_gate': True, 'live_trade_action': 'WAIT', 'final_decision': 'BUY / CONFIRMED'},
        {'universal_10pct_gate': True, 'live_trade_action': 'BUY / LIVE CONFIRMED', 'final_decision': 'NO TRADE / NEGATIVE CATALYST'},
    ])
    assert _recommendation_mask(frame).tolist() == [True, False, False, False]
