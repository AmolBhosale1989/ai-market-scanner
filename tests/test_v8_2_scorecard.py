from datetime import datetime, timezone

import pandas as pd

from scanner.v8_scorecard import evaluate_scorecard, run

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def put(memory, name, payload):
    memory["datasets"][(memory["run_id"], name)] = pd.DataFrame([payload])


def seed(memory, mature=0):
    put(memory, "v8_1_operational_health", {"status": "HEALTHY", "safe_to_serve": True})
    put(memory, "v7_1_evidence_health", {
        "status": "HEALTHY" if mature >= 30 else "COLLECTING", "failed_checks": [],
        "observations": max(20, mature), "observation_days": 12,
        "mature_training_samples": mature, "unresolved_observations": max(0, 220 - mature),
        "latest_snapshot_session": "2026-09-12", "latest_snapshot_candidates": 20,
        "snapshot_age_calendar_days": 2,
    })


def test_scorecard_reports_truthful_collection_progress(memory_control_plane):
    seed(memory_control_plane, 20)
    report = evaluate_scorecard(now=NOW)
    assert report["status"] == "HEALTHY_COLLECTING"
    assert report["next_milestone"]["remaining"] == 10
    assert report["block_v9_review"] is True


def test_scorecard_blocks_stale_or_incomplete_capture(memory_control_plane):
    seed(memory_control_plane, 220)
    put(memory_control_plane, "v7_1_evidence_health", {
        "status": "DEGRADED", "failed_checks": ["SNAPSHOT_STALE"],
        "mature_training_samples": 220, "latest_snapshot_candidates": 5,
        "snapshot_age_calendar_days": 5,
    })
    report = evaluate_scorecard(now=NOW)
    assert report["status"] == "DEGRADED"
    assert set(report["failed_checks"]) >= {"evidence_not_degraded", "snapshot_current", "daily_capture_complete"}


def test_220_fresh_samples_only_unlock_manual_v9_review(memory_control_plane):
    seed(memory_control_plane, 220)
    report = run()
    assert report["status"] == "V9_REVIEW_READY"
    assert report["block_v9_review"] is False
    assert (memory_control_plane["run_id"], "v8_2_evidence_scorecard") in memory_control_plane["datasets"]
