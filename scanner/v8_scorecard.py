from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any

import pandas as pd

from .control_plane import read_record, write_record


SCHEMA_VERSION = "8.2.0"


@dataclass(frozen=True)
class ScorecardSettings:
    monitoring_samples: int = 30
    v5_samples: int = 100
    v9_samples: int = 220
    minimum_daily_candidates: int = 10
    maximum_snapshot_age_days: int = 4


def _progress(current: int, required: int) -> float:
    return round(min(100.0, max(0.0, current / required * 100.0)), 1) if required else 100.0


def evaluate_scorecard(
    now: datetime | None = None,
    settings: ScorecardSettings | None = None,
) -> dict[str, Any]:
    """Summarize genuine forward-evidence maturity without estimating performance."""
    now = now or datetime.now(timezone.utc)
    settings = settings or ScorecardSettings()
    operational = read_record("v8_1_operational_health", required=False)
    evidence = read_record("v7_1_evidence_health", required=False)

    observations = int(evidence.get("observations", 0) or 0)
    observation_days = int(evidence.get("observation_days", 0) or 0)
    mature = int(evidence.get("mature_training_samples", 0) or 0)
    latest_candidates = int(evidence.get("latest_snapshot_candidates", 0) or 0)
    snapshot_age = evidence.get("snapshot_age_calendar_days")
    snapshot_current = isinstance(snapshot_age, (int, float)) and snapshot_age <= settings.maximum_snapshot_age_days
    daily_capture_complete = latest_candidates >= settings.minimum_daily_candidates
    resolution_progressing = "FORWARD_RESOLUTION_STALLED" not in evidence.get("failed_checks", [])
    operational_healthy = operational.get("status") == "HEALTHY" and operational.get("safe_to_serve") is True
    evidence_not_degraded = evidence.get("status") in {"COLLECTING", "HEALTHY"}

    checks = {
        "operational_health": operational_healthy,
        "evidence_not_degraded": evidence_not_degraded,
        "snapshot_current": snapshot_current,
        "daily_capture_complete": daily_capture_complete,
        "resolution_progressing": resolution_progressing,
    }
    failed = [name for name, passed in checks.items() if not passed]
    degraded = not all(checks.values())
    ready = not degraded and mature >= settings.v9_samples
    status = "DEGRADED" if degraded else ("V9_REVIEW_READY" if ready else "HEALTHY_COLLECTING")

    milestones = [
        ("MONITORING", settings.monitoring_samples),
        ("V5_VALIDATION", settings.v5_samples),
        ("V9_REVIEW", settings.v9_samples),
    ]
    next_name, next_required = milestones[-1]
    for name, required in milestones:
        if mature < required:
            next_name, next_required = name, required
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": status,
        "checks": checks,
        "failed_checks": failed,
        "observations": observations,
        "observation_days": observation_days,
        "mature_samples": mature,
        "unresolved_observations": int(evidence.get("unresolved_observations", 0) or 0),
        "latest_snapshot_session": evidence.get("latest_snapshot_session"),
        "latest_snapshot_candidates": latest_candidates,
        "snapshot_age_calendar_days": snapshot_age,
        "progress_pct": {
            "monitoring_30": _progress(mature, settings.monitoring_samples),
            "v5_100": _progress(mature, settings.v5_samples),
            "v9_220": _progress(mature, settings.v9_samples),
        },
        "next_milestone": {
            "name": next_name,
            "current": mature,
            "required": next_required,
            "remaining": max(0, next_required - mature),
        },
        "settings": asdict(settings),
        "block_v9_review": not ready,
        "performance_claim_allowed": False,
        "detail": (
            "evidence pipeline requires attention"
            if degraded
            else "minimum V9 evidence is mature; manual gate review may proceed"
            if ready
            else "forward outcomes are accumulating; no performance conclusion is permitted yet"
        ),
    }


def run() -> dict[str, Any]:
    report = evaluate_scorecard()
    write_record("v8_2_evidence_scorecard", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    run()
