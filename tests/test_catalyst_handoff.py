from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock

import pandas as pd
import pytest

from scanner import catalyst_pipeline as pipeline, catalyst_data_plane as data_plane
from scanner import stage_contract


def test_ingest_uses_current_plan_and_joins_all_provider_commits(monkeypatch):
    monkeypatch.delenv('WAREHOUSE_CONSUMER_SNAPSHOT', raising=False)
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'test-run')
    monkeypatch.setattr(pipeline, 'read_dataset', lambda name: pd.DataFrame({'ticker': ['NEW', 'NEW', 'AAA']}))
    monkeypatch.setattr(data_plane, '_universe', lambda: pytest.fail('published universe must not be used'))
    started, release = Event(), Event()
    lock = Lock()
    calls, completed = [], []
    def collect(provider, *, tickers):
        with lock:
            calls.append((provider, tickers))
            if len(calls) == 3:
                started.set()
        if provider == 'sec':
            assert release.wait(5)
        completed.append(provider)
        return {'failures': 1 if provider == 'sec' else 0}
    monkeypatch.setattr(data_plane, 'run', collect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(pipeline.main, ['--ingest-only'])
        try:
            assert started.wait(5)
            assert not future.done()  # cannot capture T0 while SEC is inflight
        finally:
            release.set()
        result = future.result(timeout=5)
    assert len(completed) == 3
    assert all(tickers == ['NEW', 'AAA'] for _, tickers in calls)
    assert result == {'tickers': 2, 'providers': 3, 'failures': 1}


@pytest.mark.parametrize('flags', [[], ['--mandatory-only']])
def test_ingest_refuses_consumer_mode_before_reads_or_network(monkeypatch, flags):
    monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT', '1')
    monkeypatch.setattr(pipeline, 'read_dataset', lambda *a: pytest.fail('must reject before reads'))
    monkeypatch.setattr(data_plane, 'run', lambda *a, **k: pytest.fail('must reject before network'))
    with pytest.raises(RuntimeError, match='INGEST_AFTER_SNAPSHOT_FORBIDDEN'):
        pipeline.main(['--ingest-only'] + flags)


def test_mandatory_only_never_fetches_optional_providers_and_waits_for_alpha(monkeypatch, capsys):
    monkeypatch.delenv('WAREHOUSE_CONSUMER_SNAPSHOT', raising=False)
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'test-run')
    monkeypatch.setattr(pipeline, 'read_dataset', lambda name: pd.DataFrame({'ticker': ['AAA','AAA','BBB']}))
    started, release = Event(), Event()
    calls = []
    def collect(provider, *, tickers):
        assert provider == 'alpha_vantage', 'optional provider must never be submitted'
        calls.append((provider, tickers))
        started.set()
        assert release.wait(5)
        return {'failures': 1}  # still returned explicitly; Gate 72 checks evidence
    monkeypatch.setattr(data_plane, 'run', collect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(pipeline.main, ['--ingest-only','--mandatory-only'])
        try:
            assert started.wait(5)
            assert not future.done()
        finally:
            release.set()
        result = future.result(timeout=5)
    assert calls == [('alpha_vantage', ['AAA','BBB'])]
    assert result == {'tickers': 2, 'providers': 1, 'failures': 1}
    assert 'CATALYST_INGEST_OPTIONAL_SKIPPED' in capsys.readouterr().out


@pytest.mark.parametrize('flags', [['--mandatory-only'], ['--verify-only','--mandatory-only']])
def test_mandatory_flag_requires_ingestion_before_any_work(monkeypatch, flags):
    monkeypatch.setattr(pipeline, 'run', lambda: pytest.fail('verification must not start'))
    monkeypatch.setattr(data_plane, 'run', lambda *a, **k: pytest.fail('network must not start'))
    with pytest.raises(SystemExit) as exc:
        pipeline.main(flags)
    assert exc.value.code == 2


def test_verify_only_never_calls_provider_worker(monkeypatch):
    t0 = datetime(2026, 9, 27, tzinfo=timezone.utc)
    monkeypatch.setattr(pipeline, 'consumer_anchor', lambda: t0)
    monkeypatch.setattr(pipeline, 'read_dataset', lambda name: pd.DataFrame({'ticker': ['AAA']}))
    monkeypatch.setattr(data_plane, 'run', lambda *a, **k: pytest.fail('network on verification path'))
    calls = []
    monkeypatch.setattr(pipeline, 'verify_coverage', lambda tickers, *, anchor: calls.append((tickers, anchor)))
    monkeypatch.setattr(pipeline, 'validate_price_snapshot', lambda anchor: None)
    assert pipeline.main(['--verify-only'])['required_checks'] == 1
    assert calls == [(['AAA'], t0)]


def test_missing_anchor_fails_before_any_universe_read(monkeypatch):
    monkeypatch.setattr(pipeline, 'consumer_anchor', lambda: None)
    monkeypatch.setattr(pipeline, 'read_dataset', lambda *a: pytest.fail('unanchored read'))
    with pytest.raises(RuntimeError, match='CONTEXT_ANCHOR_REQUIRED'):
        pipeline.main(['--verify-only'])


def test_modes_are_exclusive():
    with pytest.raises(SystemExit):
        pipeline.main(['--verify-only', '--ingest-only'])


@pytest.mark.parametrize('dependencies', [stage_contract.LIVE_DEPENDENCIES, stage_contract.FULL_DEPENDENCIES])
def test_contract_requires_ingestion_before_capture_and_gate_before_strategies(dependencies):
    assert dependencies['catalyst_ingest'] == ('intraday_warehouse',)
    assert dependencies['warehouse_gate'] == ('catalyst_ingest',)
    assert dependencies['catalyst_gate'] == ('warehouse_gate',)
    assert dependencies['broad_breakout'] == ('catalyst_gate',)
