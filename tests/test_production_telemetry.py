import json

import pytest

from scanner.production_telemetry import REQUIRED_ARTIFACTS, finalize, record_failure


def test_publication_gate_requires_every_artifact(tmp_path):
    (tmp_path/"warehouse_snapshot.json").write_text(json.dumps({"status":"PASS","production_run_id":"r","as_of_utc":"x"}))
    with pytest.raises(RuntimeError,match="PUBLICATION_GATE_FAILED"):
        finalize("live",tmp_path)


def test_publication_manifest_binds_artifacts_to_snapshot(tmp_path):
    for name in REQUIRED_ARTIFACTS["daily"]:
        (tmp_path/name).write_text("x")
    (tmp_path/"warehouse_snapshot.json").write_text(json.dumps({"status":"PASS","production_run_id":"run-1","as_of_utc":"2026-09-18T20:00:00Z"}))
    result=finalize("daily",tmp_path)
    assert result["production_run_id"]=="run-1"
    assert len(result["artifacts"])==len(REQUIRED_ARTIFACTS["daily"])


def test_failure_telemetry_is_durable_without_alert_credentials(monkeypatch,tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN",raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID",raising=False)
    result=record_failure("warehouse","failed",tmp_path)
    assert result["status"]=="FAILED"
    assert (tmp_path/"production_failure.json").exists()
