from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import pandas as pd

from .control_plane import read_dataset, read_record, write_record


SCHEMA_VERSION = "8.1.0"
STREAMLIT_HEALTH_PATH = "/_stcore/health"
EXPECTED_WORKFLOWS_UTC = {
    "daily_broad_scan": "12:30 Monday-Friday",
    "intraday_monitor": "every 15 minutes, 12:00-22:59 Monday-Friday",
    "outcome_resolution": "22:45 Monday-Friday",
    "forward_validation": "22:30 Monday-Friday",
}


def evaluate_operational_health(
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate fail-closed operational invariants without enabling execution."""
    now = now or datetime.now(timezone.utc)
    checks: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    collecting: list[str] = []

    scan = read_record("scan_health", required=False)
    scan_present = bool(scan)
    scan_passed = scan_present and str(scan.get("status", "")).upper() == "PASS"
    checks["verified_scan"] = {"present": scan_present, "passed": scan_passed}
    if scan_present and not scan_passed:
        failed.append("SCAN_NOT_PASS")
    elif not scan_present:
        collecting.append("SCAN_HEALTH_MISSING")

    social = read_record("social_engine_health", required=False)
    social_present = bool(social)
    social_locked = social_present and not bool(social.get("publish_authorized", False)) and int(
        social.get("external_actions_taken", 0) or 0
    ) == 0
    checks["social_approval_lock"] = {"present": social_present, "passed": social_locked}
    if social_present and not social_locked:
        failed.append("SOCIAL_EXTERNAL_ACTION_DETECTED")
    elif not social_present:
        collecting.append("SOCIAL_HEALTH_MISSING")

    allocation = read_record("v7_allocation_health", required=False)
    allocation_present = bool(allocation)
    broker_locked = allocation_present and not bool(allocation.get("broker_execution_enabled", False))
    checks["broker_execution_lock"] = {"present": allocation_present, "passed": broker_locked}
    if allocation_present and not broker_locked:
        failed.append("BROKER_EXECUTION_ENABLED")
    elif not allocation_present:
        collecting.append("ALLOCATION_HEALTH_MISSING")

    ledgers = ("v4_shadow_observations", "v4_outcomes")
    ledger_details: dict[str, Any] = {}
    for dataset_name in ledgers:
        frame = read_dataset(dataset_name, required=False)
        present = not frame.empty
        valid = present
        ledger_details[dataset_name] = {
            "present": present,
            "valid": valid,
            "records": len(frame),
        }
        if present and not valid:
            failed.append(f"CORRUPT_{dataset_name.upper()}")
        elif not present:
            collecting.append(f"MISSING_{dataset_name.upper()}")
    checks["durable_evidence"] = ledger_details

    status = "DEGRADED" if failed else ("COLLECTING" if collecting else "HEALTHY")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": status,
        "safe_to_serve": status == "HEALTHY",
        "block_model_promotion": status != "HEALTHY",
        "health_endpoint": STREAMLIT_HEALTH_PATH,
        "expected_workflows_utc": EXPECTED_WORKFLOWS_UTC,
        "failed_checks": sorted(set(failed)),
        "collecting_checks": sorted(set(collecting)),
        "checks": checks,
    }


def run() -> dict[str, Any]:
    report = evaluate_operational_health()
    write_record("v8_1_operational_health", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    run()
