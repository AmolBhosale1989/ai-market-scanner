from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA_VERSION = "9.1.0-readiness-only"
MIN_MATURE_EVIDENCE = 220


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def evaluate_v9_readiness(output_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    """Evaluate readiness for manual V9 review; never activate models or brokers."""
    output_dir = Path(output_dir)
    now = now or datetime.now(timezone.utc)
    operational = _json(output_dir / "v8_1_operational_health.json")
    scorecard = _json(output_dir / "v8_2_evidence_scorecard.json")
    evidence = _json(output_dir / "v7_1_evidence_health.json")
    challenger = _json(output_dir / "v7_3_challenger_health.json")
    cutover = _json(output_dir / "v4_6_cutover_evaluation.json")
    validation = _json(output_dir / "validation_gate.json")
    allocation = _json(output_dir / "v7_allocation_health.json")

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


def run(output_dir: Path) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = evaluate_v9_readiness(output_dir)
    (output_dir / "v9_readiness.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False)
    )
    pd.DataFrame([{
        "schema_version": result["schema_version"],
        "generated_at_utc": result["generated_at_utc"],
        "status": result["status"],
        "eligible_for_manual_review": result["eligible_for_manual_review"],
        "mature_evidence": result["mature_evidence"],
        "minimum_mature_evidence": result["minimum_mature_evidence"],
        "failed_gates": ",".join(result["failed_gates"]),
        "production_applied": False,
        "broker_execution_enabled": False,
    }]).to_csv(output_dir / "v9_readiness.csv", index=False)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate fail-closed Market Hunt V9 readiness")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()
    run(Path(args.output_dir))
