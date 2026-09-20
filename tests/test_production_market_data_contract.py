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


def test_live_workflows_gate_the_same_frozen_universe_they_refresh():
    expected="--live-file outputs/live_universe.csv --tier CRITICAL_DAILY"
    for path in (
        Path(".github/workflows/market-hunt-live-core.yml"),
        Path(".github/workflows/market-hunt-order-flow.yml"),
    ):
        assert expected in path.read_text()


def test_live_core_exposes_postgres_to_every_production_stage():
    workflow=Path(".github/workflows/market-hunt-live-core.yml").read_text()
    live_job_header=workflow.split("    steps:",1)[0]
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in live_job_header
    assert "PGSSLMODE: require" in live_job_header
    for module in ("theme_live.py","sector_rotation.py","momentum_signals.py","order_flow_strategy.py"):
        assert f"scanner/{module}" in workflow


def test_smoke_calibration_fixture_represents_entered_wins_and_losses():
    from scanner.performance import build_empirical_calibration, build_performance_reports
    rows=[]
    for i in range(24):
        rows.append({
            "ticker":f"T{i}","outcome":"TARGET_HIT" if i%2==0 else "FAILED_BREAKOUT",
            "return_pct":5.0 if i%2==0 else -2.0,"r_multiple":2.0 if i%2==0 else -1.0,
            "market_hunt_score":70 if i<20 else 50,"technical_score":70,
            "effective_rr":3.0,"live_confirmation_score":80,
        })
    journal=pd.DataFrame(rows)
    summary,_=build_performance_reports(journal)
    calibration=build_empirical_calibration(journal,min_samples=20)
    assert int(summary.iloc[0]["closed_signals"])==24
    assert float(summary.iloc[0]["win_rate_pct"])==50.0
    assert "USABLE" in set(calibration["calibration_status"])


def test_sector_rotation_keeps_core_etfs_strict_and_constituents_tolerant(monkeypatch):
    from types import SimpleNamespace
    import scanner.sector_rotation as module
    captured={}
    def strict(req):
        captured["strict"]=set(req.tickers)
        return SimpleNamespace(frame=pd.DataFrame(columns=["ticker","bar_timestamp"]))
    def tolerant(tickers,**kwargs):
        captured["tolerant"]=set(tickers)
        captured["require_complete"]=kwargs["require_complete"]
        return {}
    monkeypatch.setattr(module,"provide",strict)
    monkeypatch.setattr(module,"warehouse_frames",tolerant)
    _,raw=module._load_rotation_history()
    assert captured["strict"]=={"SPY",*module.THEME_ETFS.values()}
    assert not captured["strict"] & captured["tolerant"]
    assert captured["require_complete"] is False
    assert raw=={}
