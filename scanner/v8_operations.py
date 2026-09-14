from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd


SCHEMA_VERSION = "8.1.0"
STREAMLIT_HEALTH_PATH = "/_stcore/health"
EXPECTED_WORKFLOWS_UTC = {
    "daily_broad_scan": "12:30 Monday-Friday",
    "intraday_monitor": "every 15 minutes, 12:00-22:59 Monday-Friday",
    "outcome_resolution": "22:45 Monday-Friday",
    "forward_validation": "22:30 Monday-Friday",
}


def _json(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False
    try:
        value = json.loads(path.read_text())
        return (value if isinstance(value, dict) else {}), isinstance(value, dict)
    except (OSError, json.JSONDecodeError):
        return {}, False


def _csv_first(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {}, False
    try:
        frame = pd.read_csv(path)
        return (frame.iloc[0].to_dict() if not frame.empty else {}), not frame.empty
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return {}, False


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def evaluate_operational_health(
    output_dir: Path,
    state_dir: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate fail-closed operational invariants without enabling execution."""
    output_dir, state_dir = Path(output_dir), Path(state_dir)
    now = now or datetime.now(timezone.utc)
    checks: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    collecting: list[str] = []

    scan, scan_present = _csv_first(output_dir / "scan_health.csv")
    scan_passed = scan_present and str(scan.get("status", "")).upper() == "PASS"
    checks["verified_scan"] = {"present": scan_present, "passed": scan_passed}
    if scan_present and not scan_passed:
        failed.append("SCAN_NOT_PASS")
    elif not scan_present:
        collecting.append("SCAN_HEALTH_MISSING")

    social, social_present = _json(output_dir / "social_engine_health.json")
    social_locked = social_present and not bool(social.get("publish_authorized", False)) and int(
        social.get("external_actions_taken", 0) or 0
    ) == 0
    checks["social_approval_lock"] = {"present": social_present, "passed": social_locked}
    if social_present and not social_locked:
        failed.append("SOCIAL_EXTERNAL_ACTION_DETECTED")
    elif not social_present:
        collecting.append("SOCIAL_HEALTH_MISSING")

    allocation, allocation_present = _json(output_dir / "v7_allocation_health.json")
    broker_locked = allocation_present and not bool(allocation.get("broker_execution_enabled", False))
    checks["broker_execution_lock"] = {"present": allocation_present, "passed": broker_locked}
    if allocation_present and not broker_locked:
        failed.append("BROKER_EXECUTION_ENABLED")
    elif not allocation_present:
        collecting.append("ALLOCATION_HEALTH_MISSING")

    ledgers = {
        "v4_shadow_observations.json": "observations",
        "v4_outcomes.json": "signals",
    }
    ledger_details: dict[str, Any] = {}
    for filename, collection in ledgers.items():
        payload, present = _json(state_dir / filename)
        valid = present and isinstance(payload.get(collection), dict)
        ledger_details[filename] = {
            "present": present,
            "valid": valid,
            "records": len(payload.get(collection, {})) if valid else 0,
        }
        if present and not valid:
            failed.append(f"CORRUPT_{filename.upper().replace('.', '_')}")
        elif not present:
            collecting.append(f"MISSING_{filename.upper().replace('.', '_')}")
    checks["durable_evidence"] = ledger_details

    status = "DEGRADED" if failed else ("COLLECTING" if collecting else "HEALTHY")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": status,
        "safe_to_serve": not failed,
        "block_model_promotion": status != "HEALTHY",
        "health_endpoint": STREAMLIT_HEALTH_PATH,
        "expected_workflows_utc": EXPECTED_WORKFLOWS_UTC,
        "failed_checks": sorted(set(failed)),
        "collecting_checks": sorted(set(collecting)),
        "checks": checks,
    }


def run(output_dir: Path, state_dir: Path) -> dict[str, Any]:
    report = evaluate_operational_health(output_dir, state_dir)
    _atomic_write(
        Path(output_dir) / "v8_1_operational_health.json",
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False),
    )
    flat = {
        key: value
        for key, value in report.items()
        if key not in {"checks", "failed_checks", "collecting_checks", "expected_workflows_utc"}
    }
    flat["failed_checks"] = ",".join(report["failed_checks"])
    flat["collecting_checks"] = ",".join(report["collecting_checks"])
    pd.DataFrame([flat]).to_csv(Path(output_dir) / "v8_1_operational_health.csv", index=False)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Market Hunt V8.1 operational health")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--state-dir", default=".state/v4")
    arguments = parser.parse_args()
    run(Path(arguments.output_dir), Path(arguments.state_dir))
