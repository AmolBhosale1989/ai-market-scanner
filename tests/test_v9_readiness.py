from datetime import datetime, timezone
import json

from scanner.v9_readiness import evaluate_v9_readiness, run


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def write(path, value):
    path.write_text(json.dumps(value))


def seed(output):
    output.mkdir()
    write(output / "v8_1_operational_health.json", {"status": "HEALTHY", "safe_to_serve": True})
    write(output / "v7_1_evidence_health.json", {"status": "HEALTHY", "mature_training_samples": 220})
    write(output / "v7_3_challenger_health.json", {"status": "CHALLENGER_VALIDATED"})
    write(output / "v4_6_cutover_evaluation.json", {"eligible": True})
    write(output / "validation_gate.json", {"ready_for_real_money": True})
    write(output / "v7_allocation_health.json", {"paper_only": True, "broker_execution_enabled": False})


def test_v9_fails_closed_when_v8_evidence_is_missing(tmp_path):
    report = evaluate_v9_readiness(tmp_path, now=NOW)
    assert report["status"] == "BLOCKED_BY_V8_VALIDATION"
    assert report["eligible_for_manual_review"] is False
    assert report["production_applied"] is False
    assert report["broker_execution_enabled"] is False
    assert report["automatic_activation_allowed"] is False


def test_all_gates_only_allow_manual_limited_pilot_review(tmp_path):
    output = tmp_path / "outputs"
    seed(output)
    report = evaluate_v9_readiness(output, now=NOW)
    assert report["status"] == "ELIGIBLE_FOR_MANUAL_LIMITED_PILOT_REVIEW"
    assert report["eligible_for_manual_review"] is True
    assert report["failed_gates"] == []
    assert report["manual_approval_required"] is True
    assert report["live_orders_enabled"] is False


def test_broker_flag_or_immature_evidence_blocks_v9(tmp_path):
    output = tmp_path / "outputs"
    seed(output)
    write(output / "v7_1_evidence_health.json", {"status": "HEALTHY", "mature_training_samples": 219})
    write(output / "v7_allocation_health.json", {"paper_only": True, "broker_execution_enabled": True})
    report = run(output)
    assert set(report["failed_gates"]) == {"mature_evidence", "broker_lock"}
    assert report["eligible_for_manual_review"] is False
    assert (output / "v9_readiness.json").exists()
    assert (output / "v9_readiness.csv").exists()

