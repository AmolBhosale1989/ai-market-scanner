from datetime import datetime, timezone
import pandas as pd

from scanner.v4.evidence import (
    EvidenceHealthSettings,
    _freeze_earliest_daily_cohorts,
    _merge_record,
    evaluate_evidence_health,
    training_outcomes,
)


def observation(identifier: str, resolved: int, hit: bool = True):
    return {
        "observation_id": identifier,
        "as_of_session": identifier.split("|")[0],
        "observed_at_utc": identifier.split("|")[0] + "T20:00:00+00:00",
        "ticker": identifier.split("|")[1],
        "daily_bars_resolved": resolved,
        "forward_hit_5pct": hit if resolved >= 5 else None,
        "forward_hit_10pct": False if resolved >= 5 else None,
        "forward_hit_15pct": False if resolved >= 5 else None,
        "market_hunt_score": 80,
    }


def test_evidence_merge_preserves_more_resolved_record():
    durable = observation("2026-09-01|A", 1)
    local = observation("2026-09-01|A", 5)
    assert _merge_record(durable, local)["daily_bars_resolved"] == 5


def test_evidence_keeps_only_earliest_point_in_time_cohort():
    first_a = observation("2026-09-01|A", 0)
    first_b = observation("2026-09-01|B", 0)
    later = observation("2026-09-01|LATE", 0)
    first_a["observed_at_utc"] = first_b["observed_at_utc"] = "2026-09-01T20:00:00+00:00"
    later["observed_at_utc"] = "2026-09-01T21:00:00+00:00"
    records = {row["observation_id"]: row for row in (first_a, first_b, later)}
    frozen = _freeze_earliest_daily_cohorts(records)
    assert set(frozen) == {"2026-09-01|A", "2026-09-01|B"}


def test_training_outcomes_uses_only_mature_forward_evidence(memory_control_plane):
    mature = observation("2026-09-01|A", 5)
    unresolved = observation("2026-09-02|B", 3)
    key = (memory_control_plane["run_id"], "v4_shadow_observations")
    memory_control_plane["datasets"][key] = pd.DataFrame([mature, unresolved])
    triggered = {
        **mature,
        "signal_id": "A-trigger",
        "entry_session": "2026-09-01",
        "entered_at_utc": "2026-09-01T21:00:00+00:00",
    }
    memory_control_plane["datasets"][(memory_control_plane["run_id"], "v4_outcomes")] = pd.DataFrame([triggered])

    frame = training_outcomes()

    assert frame["ticker"].tolist() == ["A"]
    assert frame.iloc[0]["evidence_source"] == "DAILY_SNAPSHOT"


def test_evidence_health_tracks_milestones_and_stalls():
    rows = [observation(f"2026-09-12|T{i}", 5) for i in range(30)]
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    healthy = evaluate_evidence_health(pd.DataFrame(rows), now=now)
    assert healthy["status"] == "HEALTHY"
    assert healthy["milestones"]["monitoring_30"] is True
    assert healthy["milestones"]["v5_100"] is False

    stale = evaluate_evidence_health(
        pd.DataFrame([observation("2026-08-01|OLD", 1)]),
        now=now,
        settings=EvidenceHealthSettings(stale_after_calendar_days=4, resolution_stalled_after_days=10),
    )
    assert stale["status"] == "DEGRADED"
    assert "SNAPSHOT_STALE" in stale["failed_checks"]
    assert "FORWARD_RESOLUTION_STALLED" in stale["failed_checks"]
