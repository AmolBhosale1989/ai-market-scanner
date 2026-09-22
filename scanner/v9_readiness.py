from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import pandas as pd

from .control_plane import read_record, write_record


SCHEMA_VERSION = "9.1.0-readiness-only"
MIN_MATURE_EVIDENCE = 220


def evaluate_v9_readiness(now: datetime | None = None) -> dict[str, Any]:
    """Evaluate readiness for manual V9 review; never activate models or brokers."""
    now = now or datetime.now(timezone.utc)
    operational = read_record("v8_1_operational_health", required=False)
    scorecard = read_record("v8_2_evidence_scorecard", required=False)
    evidence = read_record("v7_1_evidence_health", required=False)
    challenger = read_record("v7_3_challenger_health", required=False)
    cutover = read_record("v4_6_cutover_evaluation", required=False)
    validation = read_record("validation_gate", required=False)
    allocation = read_record("v7_allocation_health", required=False)

    mature = int(evidence.get("mature_training_samples", 0) or 0)
    checks = {
        "v8_operational_health": operational.get("status") == "HEALTHY",
        "v8_safe_to_serve": operational.get("safe_to_serve") is True,
        "v8_evidence_scorecard": scorecard.get("status") == "V9_REVIEW_READY"
        and scorecard.get("block_v9_review") is False,
        "evidence_pipeline": evidence.get("status") == "HEALTHY",
        "mature_evidence": mature >= MIN_MATURE_EVIDENCE,
        "prospective_challenger": challenger.get("status") == "CHALLENGER_VALIDATED",
        "v4_cutover_evidence": cutover.get("eligible") is True,
        "paper_validation": validation.get("ready_for_real_money") is True,
        "paper_allocator": allocation.get("paper_only") is True,
        "broker_lock": allocation.get("broker_execution_enabled") is False,
    }
    failed = [name for name, passed in checks.items() if not passed]
    eligible = not failed
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": "ELIGIBLE_FOR_MANUAL_LIMITED_PILOT_REVIEW" if eligible else "BLOCKED_BY_V8_VALIDATION",
        "eligible_for_manual_review": eligible,
        "failed_gates": failed,
        "checks": checks,
        "mature_evidence": mature,
        "minimum_mature_evidence": MIN_MATURE_EVIDENCE,
        "manual_approval_required": True,
        "production_applied": False,
        "broker_execution_enabled": False,
        "live_orders_enabled": False,
        "automatic_activation_allowed": False,
    }


def run() -> dict[str, Any]:
    result = evaluate_v9_readiness()
    write_record("v9_readiness", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":
    run()
