from datetime import datetime, timezone

import pandas as pd

from scanner.v9_readiness import evaluate_v9_readiness, run

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def put(memory, name, payload):
    memory["datasets"][(memory["run_id"], name)] = pd.DataFrame([payload])


def seed(memory):
    put(memory, "v8_1_operational_health", {"status": "HEALTHY", "safe_to_serve": True})
    put(memory, "v8_2_evidence_scorecard", {"status": "V9_REVIEW_READY", "block_v9_review": False})
    put(memory, "v7_1_evidence_health", {"status": "HEALTHY", "mature_training_samples": 220})
    put(memory, "v7_3_challenger_health", {"status": "CHALLENGER_VALIDATED"})
    put(memory, "v4_6_cutover_evaluation", {"eligible": True})
    put(memory, "validation_gate", {"ready_for_real_money": True})
    put(memory, "v7_allocation_health", {"paper_only": True, "broker_execution_enabled": False})


def test_v9_fails_closed_when_v8_evidence_is_missing(memory_control_plane):
    report = evaluate_v9_readiness(now=NOW)
    assert report["status"] == "BLOCKED_BY_V8_VALIDATION"
    assert report["eligible_for_manual_review"] is False
    assert report["automatic_activation_allowed"] is False


def test_all_gates_only_allow_manual_limited_pilot_review(memory_control_plane):
    seed(memory_control_plane)
    report = evaluate_v9_readiness(now=NOW)
    assert report["status"] == "ELIGIBLE_FOR_MANUAL_LIMITED_PILOT_REVIEW"
    assert report["eligible_for_manual_review"] is True
    assert report["manual_approval_required"] is True
    assert report["live_orders_enabled"] is False


def test_broker_flag_or_immature_evidence_blocks_v9(memory_control_plane):
    seed(memory_control_plane)
    put(memory_control_plane, "v7_1_evidence_health", {"status": "HEALTHY", "mature_training_samples": 219})
    put(memory_control_plane, "v7_allocation_health", {"paper_only": True, "broker_execution_enabled": True})
    report = run()
    assert set(report["failed_gates"]) == {"mature_evidence", "broker_lock"}
    assert report["eligible_for_manual_review"] is False
