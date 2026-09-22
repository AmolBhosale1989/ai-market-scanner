from datetime import datetime, timezone

import pandas as pd

from scanner.v8_operations import STREAMLIT_HEALTH_PATH, evaluate_operational_health, run

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def put(memory, name, payload):
    frame = payload if isinstance(payload, pd.DataFrame) else pd.DataFrame([payload])
    memory["datasets"][(memory["run_id"], name)] = frame


def seed(memory):
    put(memory, "scan_health", {"status": "PASS"})
    put(memory, "social_engine_health", {"publish_authorized": False, "external_actions_taken": 0})
    put(memory, "v7_allocation_health", {"broker_execution_enabled": False})
    put(memory, "v4_shadow_observations", pd.DataFrame([{"observation_id": "one"}]))
    put(memory, "v4_outcomes", pd.DataFrame([{"signal_id": "one"}]))


def test_complete_safe_operational_state_is_healthy(memory_control_plane):
    seed(memory_control_plane)
    report = evaluate_operational_health(now=NOW)
    assert report["status"] == "HEALTHY"
    assert report["safe_to_serve"] is True
    assert report["block_model_promotion"] is False
    assert report["health_endpoint"] == STREAMLIT_HEALTH_PATH


def test_missing_evidence_collects_and_fails_closed(memory_control_plane):
    put(memory_control_plane, "scan_health", {"status": "PASS"})
    report = evaluate_operational_health(now=NOW)
    assert report["status"] == "COLLECTING"
    assert report["safe_to_serve"] is False
    assert report["block_model_promotion"] is True


def test_external_social_action_or_broker_enablement_fails_closed(memory_control_plane):
    seed(memory_control_plane)
    put(memory_control_plane, "social_engine_health", {"publish_authorized": True, "external_actions_taken": 1})
    put(memory_control_plane, "v7_allocation_health", {"broker_execution_enabled": True})
    report = evaluate_operational_health(now=NOW)
    assert report["status"] == "DEGRADED"
    assert report["safe_to_serve"] is False
    assert {"SOCIAL_EXTERNAL_ACTION_DETECTED", "BROKER_EXECUTION_ENABLED"}.issubset(report["failed_checks"])


def test_run_publishes_health_dataset(memory_control_plane):
    seed(memory_control_plane)
    report = run()
    stored = memory_control_plane["datasets"][(memory_control_plane["run_id"], "v8_1_operational_health")]
    assert stored.iloc[0]["status"] == report["status"] == "HEALTHY"
