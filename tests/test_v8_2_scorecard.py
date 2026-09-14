from datetime import datetime, timezone
import json

from scanner.v8_scorecard import evaluate_scorecard, run


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def write(path, value):
    path.write_text(json.dumps(value))


def seed(output, mature=0):
    output.mkdir()
    write(output / "v8_1_operational_health.json", {"status": "HEALTHY", "safe_to_serve": True})
    write(output / "v7_1_evidence_health.json", {
        "status": "HEALTHY" if mature >= 30 else "COLLECTING",
        "failed_checks": [],
        "observations": max(20, mature),
        "observation_days": 12,
        "mature_training_samples": mature,
        "unresolved_observations": max(0, 220 - mature),
        "latest_snapshot_session": "2026-09-12",
        "latest_snapshot_candidates": 20,
        "snapshot_age_calendar_days": 2,
    })


def test_scorecard_reports_truthful_collection_progress(tmp_path):
    output = tmp_path / "outputs"
    seed(output, mature=20)
    report = evaluate_scorecard(output, now=NOW)
    assert report["status"] == "HEALTHY_COLLECTING"
    assert report["next_milestone"]["name"] == "MONITORING"
    assert report["next_milestone"]["remaining"] == 10
    assert report["block_v9_review"] is True
    assert report["performance_claim_allowed"] is False


def test_scorecard_blocks_stale_or_incomplete_capture(tmp_path):
    output = tmp_path / "outputs"
    seed(output, mature=220)
    write(output / "v7_1_evidence_health.json", {
        "status": "DEGRADED", "failed_checks": ["SNAPSHOT_STALE"],
        "mature_training_samples": 220, "latest_snapshot_candidates": 5,
        "snapshot_age_calendar_days": 5,
    })
    report = evaluate_scorecard(output, now=NOW)
    assert report["status"] == "DEGRADED"
    assert set(report["failed_checks"]) >= {"evidence_not_degraded", "snapshot_current", "daily_capture_complete"}
    assert report["block_v9_review"] is True


def test_220_fresh_samples_only_unlock_manual_v9_review(tmp_path):
    output = tmp_path / "outputs"
    seed(output, mature=220)
    report = run(output)
    assert report["status"] == "V9_REVIEW_READY"
    assert report["block_v9_review"] is False
    assert (output / "v8_2_evidence_scorecard.json").exists()
    assert (output / "v8_2_evidence_scorecard.csv").exists()
