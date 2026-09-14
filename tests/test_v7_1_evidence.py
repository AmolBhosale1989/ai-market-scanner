from datetime import datetime, timezone
import json

import pandas as pd

from scanner.v4.evidence import (
    EvidenceHealthSettings,
    evaluate_evidence_health,
    import_durable_evidence,
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


def test_durable_import_bootstraps_csv_and_preserves_more_resolved_local_record(tmp_path):
    durable = tmp_path / "durable"
    state = tmp_path / "state"
    (durable / "dashboard-data").mkdir(parents=True)
    pd.DataFrame([observation("2026-09-01|A", 1)]).to_csv(
        durable / "dashboard-data/v4_shadow_observations.csv", index=False
    )
    state.mkdir()
    local = observation("2026-09-01|A", 5)
    (state / "v4_shadow_observations.json").write_text(json.dumps({"observations": {local["observation_id"]: local}}))

    counts = import_durable_evidence(state, durable)

    payload = json.loads((state / "v4_shadow_observations.json").read_text())
    assert counts["observations"] == 1
    assert payload["observations"]["2026-09-01|A"]["daily_bars_resolved"] == 5


def test_training_outcomes_uses_only_mature_forward_evidence(tmp_path):
    mature = observation("2026-09-01|A", 5)
    unresolved = observation("2026-09-02|B", 3)
    (tmp_path / "v4_shadow_observations.json").write_text(json.dumps({
        "observations": {mature["observation_id"]: mature, unresolved["observation_id"]: unresolved}
    }))
    triggered = {
        **mature,
        "signal_id": "A-trigger",
        "entry_session": "2026-09-01",
        "entered_at_utc": "2026-09-01T21:00:00+00:00",
    }
    (tmp_path / "v4_outcomes.json").write_text(json.dumps({"signals": {"A-trigger": triggered}}))

    frame = training_outcomes(tmp_path)

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
