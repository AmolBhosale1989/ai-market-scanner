from __future__ import annotations

import json
from pathlib import Path

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
