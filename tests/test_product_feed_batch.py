import json
import pandas as pd
import pytest
from scanner import product_feed as module


def test_feed_batches_current_run_and_preserves_rows_limits_and_nulls(monkeypatch):
    frame = pd.DataFrame([{'ticker':str(i), 'value':None if i==0 else i} for i in range(120)])
    calls=[]
    def batch(names, *, run_id):
        calls.append((tuple(names),run_id))
        return {name:(frame.copy(),'postgresql') for name in names}
    written=[]
    monkeypatch.setattr(module,'current_run_id',lambda:'current-run')
    monkeypatch.setattr(module,'read_dashboard_datasets',batch)
    monkeypatch.setattr(module,'write_dataset',lambda *a,**kw:written.append(a))
    payload=module.build_product_feed()
    assert calls==[(module.FEED_DATASETS,'current-run')]
    limits=[20,50,50,50,100,20,1,1,50,1,1,100]
    for actual,limit in zip(payload['market_hunt'].values(),limits):
        expected=json.loads(frame.head(limit).where(pd.notna(frame.head(limit)),None).to_json(orient='records'))
        assert actual==expected
    assert written[0][0]=='product_feed'


def test_feed_never_writes_when_integrity_check_fails(monkeypatch):
    monkeypatch.setattr(module,'current_run_id',lambda:'current-run')
    def fail(*a,**kw):
        raise RuntimeError('CONTROL_PLANE_DATASET_CORRUPT')
    monkeypatch.setattr(module,'read_dashboard_datasets',fail)
    monkeypatch.setattr(module,'write_dataset',lambda *a,**kw:pytest.fail('must not publish corrupt feed'))
    with pytest.raises(RuntimeError,match='CORRUPT'):
        module.build_product_feed()
