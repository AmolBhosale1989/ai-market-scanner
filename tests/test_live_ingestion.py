import pandas as pd
import pytest
from scanner import live_ingestion as module
from scanner.warehouse_gate import CoverageTier, evaluate_tier


def test_only_deficient_required_symbols_receive_one_bounded_retry(monkeypatch):
    now = pd.Timestamp.now(tz='UTC')
    frame = pd.DataFrame([
        dict(ticker='SPY', bars=150, invalid_bars=0, event_timestamp=now-pd.Timedelta(minutes=5), ingested_at=now),
        dict(ticker='SKYY', bars=150, invalid_bars=0, event_timestamp=now-pd.Timedelta(minutes=30), ingested_at=now),
    ])
    tier = CoverageTier('CRITICAL_INTRADAY', ('SPY','SKYY','FINX'), '5m', 1., 120, 10)
    monkeypatch.setattr(module, 'coverage_frame', lambda *a: frame)
    # Fix the market clock to an open session while retaining realistic ages.
    monkeypatch.setattr(module, 'evaluate_tier', lambda t,f,**kw: {
        'status': 'PASS' if set(t.symbols) == {'SPY'} else 'FAIL'})
    calls=[]
    monkeypatch.setattr(module, 'refresh', lambda ts,**kw: calls.append((ts,kw)))
    module.recover_required(tier)
    assert calls == [(['SKYY','FINX'], dict(period='5d', interval='5m', benchmark_backfill=False, include_ingestion_dependencies=False))]


def test_healthy_required_tier_does_not_fetch_again(monkeypatch):
    monkeypatch.setattr(module, 'coverage_frame', lambda *a: pd.DataFrame())
    monkeypatch.setattr(module, 'evaluate_tier', lambda *a,**kw: {'status':'PASS'})
    monkeypatch.setattr(module, 'refresh', lambda *a,**kw: pytest.fail('unexpected fetch'))
    module.recover_required(None)


def test_failed_recovery_is_not_swallowed(monkeypatch):
    tier=CoverageTier('CRITICAL_INTRADAY', ('SKYY',), '5m', 1., 120, 10)
    monkeypatch.setattr(module, 'coverage_frame', lambda *a: pd.DataFrame())
    monkeypatch.setattr(module, 'evaluate_tier', lambda *a,**kw: {'status':'FAIL'})
    def fail(*a,**kw):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(module, 'refresh', fail)
    with pytest.raises(RuntimeError, match='provider unavailable'):
        module.recover_required(tier)


def test_stale_data_still_fails_real_gate_after_unsuccessful_retry(monkeypatch):
    anchor=pd.Timestamp('2026-09-25T18:20:00Z')
    frame=pd.DataFrame([dict(ticker='SKYY', bars=150, invalid_bars=0,
        event_timestamp=pd.Timestamp('2026-09-25T17:55:00Z'), ingested_at=anchor)])
    tier=CoverageTier('CRITICAL_INTRADAY', ('SKYY',), '5m', 1., 120, 10)
    result=evaluate_tier(tier,frame,now_utc=anchor)
    assert result['status']=='FAIL'
    assert result['stale_sample']==['SKYY']
