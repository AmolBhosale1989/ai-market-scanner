from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from typing import Any

import pandas as pd

from .control_plane import read_dataset, read_record, write_dataset, write_record


SCHEMA_VERSION = "9.1.0-paper-rehearsal"


@dataclass(frozen=True)
class PilotSettings:
    maximum_review_candidates: int = 2
    maximum_risk_per_position_pct: float = 0.25
    maximum_total_risk_pct: float = 0.50
    manual_order_entry_required: bool = True


def build_pilot_rehearsal(
    now: datetime | None = None,
    settings: PilotSettings | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Prepare a non-executable manual review packet only after every V9 gate passes."""
    now = now or datetime.now(timezone.utc)
    settings = settings or PilotSettings()
    readiness = read_record("v9_readiness", required=False)
    scorecard = read_record("v8_2_evidence_scorecard", required=False)
    allocation = read_record("v7_allocation_health", required=False)
    portfolio = read_dataset("v7_paper_portfolio", required=False)

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


def run() -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates, health = build_pilot_rehearsal()
    write_dataset("v9_1_pilot_candidates", candidates, entity_key="ticker")
    write_record("v9_1_pilot_health", health)
    print(json.dumps(health, indent=2, sort_keys=True))
    return candidates, health


if __name__ == "__main__":
    run()
