import pandas as pd
import pytest
from scanner import consumer_snapshot as snapshot
from scanner.warehouse import _freshness_failures


def test_snapshot_clock_does_not_override_explicit_publication_clock(monkeypatch):
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: pd.Timestamp('2026-09-25T17:09:00Z'))
    rows = pd.DataFrame([dict(ticker='SPY',event_timestamp='2026-09-25T17:00:00Z',ingested_at='2026-09-25T17:08:00Z')])
    assert _freshness_failures(rows,'5m',10,'consumer')[0] == []
    assert _freshness_failures(rows,'5m',10,'publication',now_utc=pd.Timestamp('2026-09-25T17:16:00Z'))[0] == ['SPY']


def test_snapshot_requires_current_run_and_validated_manifest(monkeypatch):
    import scanner.control_plane as cp
    snapshot._validated_anchor.cache_clear()
    monkeypatch.setenv('WAREHOUSE_CONSUMER_SNAPSHOT','1')
    monkeypatch.delenv('PRODUCTION_RUN_ID',raising=False)
    with pytest.raises(RuntimeError,match='RUN_REQUIRED'):
        snapshot.consumer_anchor()
    monkeypatch.setenv('PRODUCTION_RUN_ID','test-run')
    monkeypatch.setattr(cp,'read_dataset',lambda *a,**k:pd.DataFrame([dict(status='FAIL')]))
    with pytest.raises(RuntimeError,match='SNAPSHOT_INVALID'):
        snapshot.consumer_anchor()
    monkeypatch.setattr(cp,'read_dataset',lambda *a,**k:pd.DataFrame([dict(status='PASS',production_run_id='other')]))
    with pytest.raises(RuntimeError,match='WRONG_RUN'):
        snapshot.consumer_anchor()
