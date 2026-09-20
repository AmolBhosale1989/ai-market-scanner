from pathlib import Path

import pandas as pd
import pytest

from scanner import intraday
from scanner.production_audit import audit


def test_production_modules_have_no_provider_bypass():
    assert audit(Path("scanner"))==[]


def test_intraday_rejects_missing_or_persisted_candidate_input():
    with pytest.raises(RuntimeError,match="INTRADAY_INPUT_REQUIRED"):
        intraday.run()
    with pytest.raises(RuntimeError,match="INTRADAY_PERSISTED_INPUT_REJECTED"):
        intraday.run("outputs/latest_scan.csv")


def test_order_flow_validation_uses_warehouse(monkeypatch,tmp_path):
    import scanner.order_flow_validation as module
    selected=pd.Timestamp("2026-09-18T14:30:00Z")
    idx=pd.date_range(selected,periods=3,freq="5min")
    hist=pd.DataFrame({"Open":[10,10,10],"High":[10.2,10.6,11.0],"Low":[9.9,10.0,10.4],"Close":[10.1,10.5,10.9],"Volume":[100,200,300]},index=idx)
    monkeypatch.setattr(module,"warehouse_history",lambda *a,**k: hist)
    row=pd.Series({"ticker":"TEST","status":"OPEN","entry_price":10.0,"stop_price":9.0,
                   "target1_price":10.5,"target2_price":11.0,"signal_at_et":selected,
                   "signal_date_et":"2026-09-18"})
    updated=module._update_open(row,pd.Timestamp("2026-09-18T16:00:00Z").to_pydatetime())
    assert updated["status"]=="TARGET2_HIT"


def test_live_enrichment_accepts_preconfirmation_v3_score(monkeypatch):
    import scanner.live as module
    monkeypatch.setattr(module,"analyze_live_candidate",lambda **kwargs: {"live_status":"MARKET CLOSED"})
    frame=pd.DataFrame([{"ticker":"TEST","stage":"ARMED","final_score":81.0}])
    result=module.enrich_live_candidates(frame,limit=1)
    assert result.loc[0,"live_status"]=="MARKET CLOSED"
