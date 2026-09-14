from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA_VERSION = "9.1.0-paper-rehearsal"


@dataclass(frozen=True)
class PilotSettings:
    maximum_review_candidates: int = 2
    maximum_risk_per_position_pct: float = 0.25
    maximum_total_risk_pct: float = 0.50
    manual_order_entry_required: bool = True


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def build_pilot_rehearsal(
    output_dir: Path,
    now: datetime | None = None,
    settings: PilotSettings | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Prepare a non-executable manual review packet only after every V9 gate passes."""
    output_dir = Path(output_dir)
    now = now or datetime.now(timezone.utc)
    settings = settings or PilotSettings()
    readiness = _json(output_dir / "v9_readiness.json")
    scorecard = _json(output_dir / "v8_2_evidence_scorecard.json")
    allocation = _json(output_dir / "v7_allocation_health.json")
    portfolio = _csv(output_dir / "v7_paper_portfolio.csv")

    checks = {
        "v9_manual_review_eligible": readiness.get("eligible_for_manual_review") is True,
        "v8_evidence_ready": scorecard.get("status") == "V9_REVIEW_READY",
        "paper_allocator": allocation.get("paper_only") is True,
        "broker_lock": allocation.get("broker_execution_enabled") is False,
        "paper_candidates_available": not portfolio.empty,
    }
    failed = [name for name, passed in checks.items() if not passed]
    allowed_columns = [
        "ticker", "sector", "theme", "entry_trigger", "stop", "effective_rr",
        "v6_robust_score", "v6_confidence", "v6_p5_lower",
    ]
    if failed:
        candidates = pd.DataFrame(columns=[*allowed_columns, "v9_action", "maximum_risk_pct"])
    else:
        available = [column for column in allowed_columns if column in portfolio.columns]
        candidates = portfolio.loc[:, available].head(settings.maximum_review_candidates).copy()
        candidates["v9_action"] = "MANUAL_REVIEW_ONLY"
        candidates["maximum_risk_pct"] = settings.maximum_risk_per_position_pct

    ready = not failed and not candidates.empty
    health = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": "PAPER_REHEARSAL_READY" if ready else "BLOCKED_PAPER_REHEARSAL",
        "checks": checks,
        "failed_checks": failed,
        "review_candidates": len(candidates),
        "settings": asdict(settings),
        "paper_only": True,
        "manual_approval_required": True,
        "production_applied": False,
        "automatic_activation_allowed": False,
        "broker_execution_enabled": False,
        "live_orders_enabled": False,
        "orders_generated": 0,
        "detail": (
            "manual paper-rehearsal packet is ready; no order has been generated"
            if ready
            else "pilot rehearsal remains blocked until evidence and safety gates pass"
        ),
    }
    return candidates, health


def run(output_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates, health = build_pilot_rehearsal(output_dir)
    candidates.to_csv(output_dir / "v9_1_pilot_candidates.csv", index=False)
    (output_dir / "v9_1_pilot_health.json").write_text(
        json.dumps(health, indent=2, sort_keys=True, allow_nan=False)
    )
    print(json.dumps(health, indent=2, sort_keys=True))
    return candidates, health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a fail-closed V9.1 paper-pilot rehearsal")
    parser.add_argument("--output-dir", default="outputs")
    arguments = parser.parse_args()
    run(Path(arguments.output_dir))
