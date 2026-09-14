from datetime import datetime, timezone
import json

import pandas as pd

from scanner.v8_operations import STREAMLIT_HEALTH_PATH, evaluate_operational_health, run


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def seed(output, state):
    output.mkdir()
    state.mkdir(parents=True)
    pd.DataFrame([{"status": "PASS"}]).to_csv(output / "scan_health.csv", index=False)
    (output / "social_engine_health.json").write_text(json.dumps({
        "publish_authorized": False, "external_actions_taken": 0,
    }))
    (output / "v7_allocation_health.json").write_text(json.dumps({
        "broker_execution_enabled": False,
    }))
    (state / "v4_shadow_observations.json").write_text(json.dumps({"observations": {}}))
    (state / "v4_outcomes.json").write_text(json.dumps({"signals": {}}))


def test_complete_safe_operational_state_is_healthy(tmp_path):
    output, state = tmp_path / "out", tmp_path / "state"
    seed(output, state)
    report = evaluate_operational_health(output, state, now=NOW)
    assert report["status"] == "HEALTHY"
    assert report["safe_to_serve"] is True
    assert report["block_model_promotion"] is False
    assert report["health_endpoint"] == STREAMLIT_HEALTH_PATH


def test_missing_evidence_collects_but_blocks_promotion(tmp_path):
    output, state = tmp_path / "out", tmp_path / "state"
    output.mkdir()
    state.mkdir()
    pd.DataFrame([{"status": "PASS"}]).to_csv(output / "scan_health.csv", index=False)
    report = evaluate_operational_health(output, state, now=NOW)
    assert report["status"] == "COLLECTING"
    assert report["safe_to_serve"] is True
    assert report["block_model_promotion"] is True


def test_external_social_action_or_broker_enablement_fails_closed(tmp_path):
    output, state = tmp_path / "out", tmp_path / "state"
    seed(output, state)
    (output / "social_engine_health.json").write_text(json.dumps({
        "publish_authorized": True, "external_actions_taken": 1,
    }))
    (output / "v7_allocation_health.json").write_text(json.dumps({
        "broker_execution_enabled": True,
    }))
    report = evaluate_operational_health(output, state, now=NOW)
    assert report["status"] == "DEGRADED"
    assert report["safe_to_serve"] is False
    assert "SOCIAL_EXTERNAL_ACTION_DETECTED" in report["failed_checks"]
    assert "BROKER_EXECUTION_ENABLED" in report["failed_checks"]


def test_run_writes_json_atomically_and_csv_summary(tmp_path):
    output, state = tmp_path / "out", tmp_path / "state"
    seed(output, state)
    report = run(output, state)
    assert json.loads((output / "v8_1_operational_health.json").read_text())["status"] == report["status"]
    assert pd.read_csv(output / "v8_1_operational_health.csv").iloc[0]["status"] == "HEALTHY"

