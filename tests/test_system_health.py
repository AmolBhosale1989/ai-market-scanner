from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scanner import system_health


def test_warehouse_snapshot_is_recognized_as_production_health(tmp_path, monkeypatch):
    monkeypatch.setattr(system_health, "OUTPUT_DIR", tmp_path)
    (tmp_path / "warehouse_snapshot.json").write_text(
        json.dumps({"status": "PASS", "as_of_utc": "2026-09-20T08:07:14Z"})
    )

    health = system_health.run()
    warehouse = health.loc[health["module"] == "Warehouse Snapshot"].iloc[0]

    assert warehouse["status"] == "OK"
    assert warehouse["last_update_utc"] == "2026-09-20T08:07:14+00:00"


def test_live_health_job_restores_published_warehouse_snapshot():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/market-hunt-live-core.yml"
    ).read_text()
    restore_step = workflow.split("- name: Restore published health artifacts", 1)[1]
    restore_step = restore_step.split("- name: Build unified live health", 1)[0]

    assert "warehouse_snapshot.json" in restore_step


def test_semantic_health_rejects_wrong_session_and_dropped_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(system_health,"OUTPUT_DIR",tmp_path)
    monkeypatch.setattr(system_health,"expected_market_data_session",lambda now: pd.Timestamp("2026-09-18").date())
    timestamp="2026-09-20T08:00:00Z"
    (tmp_path/"warehouse_snapshot.json").write_text(json.dumps({"as_of_utc":timestamp}))
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-18","themes_ranked":19}]).to_csv(tmp_path/"theme_health.csv",index=False)
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-20","themes_scanned":0,"stocks_scanned":0}]).to_csv(tmp_path/"sector_rotation_health.csv",index=False)
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-18","candidate_inputs":33,"leaders_evaluated":0}]).to_csv(tmp_path/"momentum_health.csv",index=False)
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-18","expected_inputs":0,"evaluated":0}]).to_csv(tmp_path/"order_flow_strategy_health.csv",index=False)

    health=system_health.run().set_index("module")
    assert health.loc["Themes","status"]=="OK"
    assert health.loc["Sector Rotation","status"]=="INVALID"
    assert health.loc["Momentum","status"]=="INVALID"
    assert health.loc["Order Flow","status"]=="INVALID"


def test_semantic_health_accepts_no_trade_when_all_inputs_were_evaluated(tmp_path, monkeypatch):
    monkeypatch.setattr(system_health,"OUTPUT_DIR",tmp_path)
    monkeypatch.setattr(system_health,"expected_market_data_session",lambda now: pd.Timestamp("2026-09-18").date())
    timestamp="2026-09-20T08:00:00Z"
    (tmp_path/"warehouse_snapshot.json").write_text(json.dumps({"as_of_utc":timestamp}))
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-18","themes_ranked":19}]).to_csv(tmp_path/"theme_health.csv",index=False)
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-18","themes_scanned":11,"stocks_scanned":110}]).to_csv(tmp_path/"sector_rotation_health.csv",index=False)
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-18","candidate_inputs":33,"leaders_evaluated":33}]).to_csv(tmp_path/"momentum_health.csv",index=False)
    pd.DataFrame([{"updated_at_et":timestamp,"session_date":"2026-09-18","expected_inputs":33,"evaluated":33}]).to_csv(tmp_path/"order_flow_strategy_health.csv",index=False)

    health=system_health.run().set_index("module")
    assert set(health.loc[["Themes","Sector Rotation","Momentum","Order Flow"],"status"])=={"OK"}
