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


def test_live_core_triggers_when_warehouse_contract_changes():
    workflow=Path(".github/workflows/market-hunt-live-core.yml").read_text()
    trigger=workflow.split("permissions:",1)[0]
    assert "'scanner/config.py'" in trigger
    assert "'scanner/warehouse_gate.py'" in trigger


def test_live_core_publishes_current_order_flow_metadata():
    workflow=Path(".github/workflows/market-hunt-live-core.yml").read_text()
    order_flow=workflow.split("- name: Complete production order-flow chain",1)[1]
    order_flow=order_flow.split("- name: Build fail-closed production health",1)[0]
    assert "outputs/order_flow_live_metadata.csv" in order_flow
    validation=workflow.split("- name: Validate Refactor 2 freshness contract",1)[1]
    validation=validation.split("- name: Publish V3 live production atomically",1)[0]
    assert 'grep -q ",$GITHUB_RUN_ID,$GITHUB_SHA,FRESH"' in validation
    publication=workflow.split("- name: Publish V3 live production atomically",1)[1]
    assert "order_flow_live_metadata.csv" in publication


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


def test_theme_live_uses_sparse_theme_freshness_contract(monkeypatch):
    from types import SimpleNamespace
    import scanner.theme_live as module
    captured={}
    def load(req):
        captured["requirement"]=req
        return SimpleNamespace(frame=pd.DataFrame())
    monkeypatch.setattr(module,"provide",load)
    module._load_theme_history(["SPY","FINX"])
    requirement=captured["requirement"]
    assert requirement.tickers==("SPY","FINX")
    assert requirement.max_age_minutes==module.THEME_INTRADAY_MAX_AGE_MINUTES==20
    assert requirement.minimum_fresh_coverage==module.THEME_INTRADAY_MIN_COVERAGE==0.90
    assert requirement.required_fresh_tickers==("SPY",)


def test_momentum_emits_valid_empty_artifacts_when_session_has_no_leaders(monkeypatch,tmp_path):
    import scanner.momentum_signals as module
    monkeypatch.setattr(module,"OUTPUT_DIR",tmp_path)
    (tmp_path/"rotation_leaders.csv").write_text("\n")
    result=module.run()
    assert result.empty
    assert "ticker" in pd.read_csv(tmp_path/"momentum_signals.csv").columns
    health=pd.read_csv(tmp_path/"momentum_health.csv")
    assert int(health.loc[0,"leaders_evaluated"])==0
    assert int(health.loc[0,"candidate_inputs"])==0


def test_theme_and_rotation_stats_use_explicit_latest_session():
    from scanner import sector_rotation, theme_live
    index=pd.DatetimeIndex([
        "2026-09-17T15:55:00-04:00",
        "2026-09-18T15:55:00-04:00",
    ])
    frame=pd.DataFrame({
        "Open":[10.0,10.5],"High":[10.5,11.2],"Low":[9.9,10.4],
        "Close":[10.0,11.0],"Volume":[100.0,200.0],
    },index=index)
    move,bar=theme_live._stats(frame,pd.Timestamp("2026-09-18").date())
    rotation=sector_rotation._stats(frame,"TEST",pd.Timestamp("2026-09-18").date())
    assert round(move,2)==10.0
    assert bar.startswith("2026-09-18T15:55:00")
    assert rotation["day_change_pct"]==10.0


def test_momentum_evaluates_latest_warehouse_session_on_weekend(monkeypatch,tmp_path):
    from types import SimpleNamespace
    import scanner.momentum_signals as module

    monkeypatch.setattr(module,"OUTPUT_DIR",tmp_path)
    pd.DataFrame([{
        "ticker":"TEST","source":"BROAD_BREAKOUT","theme":"BROAD",
        "broad_breakout_score":80,"theme_rotation_score":0,"rotation_leader_score":80,
        "rel_vs_spy_pct":2.0,"day_change_pct":3.0,"move_30m_pct":0.5,
        "intraday_volume":3_000_000,
    }]).to_csv(tmp_path/"broad_breakout_discovery.csv",index=False)
    pd.DataFrame(columns=["ticker","rotation_leader"]).to_csv(tmp_path/"rotation_leaders.csv",index=False)
    rows=[]
    for session,base in (("2026-09-16",9.5),("2026-09-17",9.8),("2026-09-18",10.0)):
        for n,stamp in enumerate(pd.date_range(f"{session} 13:30:00Z",periods=6,freq="5min")):
            rows.append({
                "ticker":"TEST","bar_timestamp":stamp,"Open":base+n*.02,
                "High":base+n*.02+.1,"Low":base+n*.02-.1,"Close":base+n*.02+.05,
                "Volume":200_000+n*10_000,
            })
    monkeypatch.setattr(module,"provide",lambda req: SimpleNamespace(frame=pd.DataFrame(rows)))

    out=module.run(limit=1)
    health=pd.read_csv(tmp_path/"momentum_health.csv")
    assert len(out)==1
    assert out.iloc[0]["last_bar_et"].startswith("2026-09-18")
    assert health.loc[0,"session_date"]=="2026-09-18"
    assert int(health.loc[0,"candidate_inputs"])==1
    assert int(health.loc[0,"leaders_evaluated"])==1
