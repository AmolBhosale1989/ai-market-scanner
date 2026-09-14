from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

import pandas as pd

from .criteria_optimizer import APPROVED_CRITERIA, V7_2_SCHEMA, _metrics


V7_3_SCHEMA = "7.3.0-prospective-shadow"
TERMINAL = {"CHALLENGER_VALIDATED", "CHALLENGER_REJECTED"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safety() -> dict[str, bool]:
    return {
        "shadow_only": True,
        "production_applied": False,
        "activation_allowed": False,
        "broker_execution_enabled": False,
    }


@dataclass(frozen=True)
class ChallengerSettings:
    min_future_sessions: int = 10
    min_baseline_samples: int = 100
    min_challenger_samples: int = 30
    min_retention: float = 0.25
    min_utility_improvement: float = 0.5
    max_target_degradation_pp: float = 2.0
    max_false_breakout_degradation_pp: float = 2.0


def empty_state(reason: str = "waiting for a V7.2 holdout-passing proposal") -> dict[str, Any]:
    return {
        "schema_version": V7_3_SCHEMA,
        "status": "WAITING_FOR_PROPOSAL",
        "reason": reason,
        "active_challenger": None,
        "settings": asdict(ChallengerSettings()),
        **_safety(),
    }


def _valid_change(proposal: dict[str, Any]) -> dict[str, Any] | None:
    if proposal.get("schema_version") != V7_2_SCHEMA or proposal.get("status") != "PROPOSAL_ELIGIBLE":
        return None
    change = proposal.get("recommended_change")
    if not isinstance(change, dict) or change.get("operator") != ">=":
        return None
    criterion = str(change.get("criterion", ""))
    try:
        threshold = float(change.get("proposed_value"))
    except (TypeError, ValueError):
        return None
    if criterion not in APPROVED_CRITERIA or threshold not in APPROVED_CRITERIA[criterion]:
        return None
    return {"criterion": criterion, "operator": ">=", "proposed_value": threshold}


def _latest_session(observations: pd.DataFrame, proposal: dict[str, Any]) -> str:
    dates: list[str] = []
    if observations is not None and not observations.empty and "as_of_session" in observations:
        sessions = pd.to_datetime(observations["as_of_session"], errors="coerce", utc=True).dropna()
        if len(sessions):
            dates.append(sessions.max().date().isoformat())
    created = pd.to_datetime(proposal.get("created_at_utc"), errors="coerce", utc=True)
    if pd.notna(created):
        dates.append(created.date().isoformat())
    return max(dates) if dates else datetime.now(timezone.utc).date().isoformat()


def _register(proposal: dict[str, Any], observations: pd.DataFrame, settings: ChallengerSettings) -> dict[str, Any]:
    change = _valid_change(proposal)
    if change is None:
        return empty_state("V7.2 proposal is absent, unvalidated, or outside the committed allowlist")
    start_after = _latest_session(observations, proposal)
    identity = json.dumps({"proposal": proposal.get("model_version"), "change": change, "start_after": start_after}, sort_keys=True)
    challenger_id = "v7.3-" + hashlib.sha256(identity.encode()).hexdigest()[:12]
    return {
        "schema_version": V7_3_SCHEMA,
        "status": "COLLECTING_FUTURE",
        "reason": "challenger frozen; collecting future-only sessions",
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "active_challenger": {
            "challenger_id": challenger_id,
            "v7_2_model_version": str(proposal.get("model_version", "")),
            "change": change,
            "start_after_session": start_after,
        },
        "future_sessions": 0,
        "baseline_samples": 0,
        "challenger_samples": 0,
        "settings": asdict(settings),
        **_safety(),
    }


def _comparison(state: dict[str, Any]) -> pd.DataFrame:
    rows = [{"variant": variant.upper(), **metrics} for variant, metrics in state.get("evaluation_metrics", {}).items()]
    return pd.DataFrame(rows)


def evaluate_challenger(
    proposal: dict[str, Any],
    observations: pd.DataFrame,
    state: dict[str, Any] | None = None,
    settings: ChallengerSettings | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    settings = settings or ChallengerSettings()
    current = dict(state or {})
    if current.get("schema_version") != V7_3_SCHEMA or not current.get("active_challenger"):
        current = _register(proposal, observations, settings)
    if current.get("status") in TERMINAL:
        return current, _comparison(current)
    active = current.get("active_challenger")
    if not isinstance(active, dict):
        return current, pd.DataFrame()

    frame = observations.copy() if observations is not None else pd.DataFrame()
    required = {"as_of_session", "daily_bars_resolved", "forward_hit_5pct", "forward_hit_10pct", "forward_hit_15pct"}
    if frame.empty or not required.issubset(frame.columns):
        return current, pd.DataFrame()
    sessions = pd.to_datetime(frame["as_of_session"], errors="coerce", utc=True)
    mature = pd.to_numeric(frame["daily_bars_resolved"], errors="coerce").fillna(0).ge(5)
    after = sessions.dt.date.astype(str).gt(str(active["start_after_session"]))
    future = frame[mature & sessions.notna() & after].copy()
    session_count = int(pd.to_datetime(future.get("as_of_session"), errors="coerce").dt.date.nunique()) if len(future) else 0
    change = active["change"]
    values = pd.to_numeric(future.get(change["criterion"]), errors="coerce") if change["criterion"] in future else pd.Series(float("nan"), index=future.index)
    challenger = future[values.ge(float(change["proposed_value"]))]
    current.update({
        "future_sessions": session_count,
        "baseline_samples": len(future),
        "challenger_samples": len(challenger),
        "settings": asdict(settings),
    })
    if (
        session_count < settings.min_future_sessions
        or len(future) < settings.min_baseline_samples
        or len(challenger) < settings.min_challenger_samples
    ):
        current["status"] = "COLLECTING_FUTURE"
        current["reason"] = "prospective sample gates are not yet complete"
        return current, pd.DataFrame()

    baseline_metrics = _metrics(future)
    challenger_metrics = _metrics(challenger)
    retention = len(challenger) / max(1, len(future))
    improvement = _number(challenger_metrics["utility"]) - _number(baseline_metrics["utility"])
    failed: list[str] = []
    if retention < settings.min_retention:
        failed.append("OPPORTUNITY_RETENTION")
    if improvement < settings.min_utility_improvement:
        failed.append("UTILITY")
    for metric in ("p10_precision_pct", "p15_precision_pct"):
        if _number(challenger_metrics[metric]) < _number(baseline_metrics[metric]) - settings.max_target_degradation_pp:
            failed.append(metric.upper() + "_DEGRADATION")
    if _number(challenger_metrics["avg_r_multiple"]) < _number(baseline_metrics["avg_r_multiple"]):
        failed.append("EXPECTANCY_DEGRADATION")
    if _number(challenger_metrics["false_breakout_rate_pct"]) > (
        _number(baseline_metrics["false_breakout_rate_pct"]) + settings.max_false_breakout_degradation_pp
    ):
        failed.append("FALSE_BREAKOUT_DEGRADATION")

    current.update({
        "status": "CHALLENGER_VALIDATED" if not failed else "CHALLENGER_REJECTED",
        "reason": "future-only gates passed; manual review required" if not failed else "future-only gates rejected challenger",
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_end_session": max(future["as_of_session"].astype(str)),
        "evaluation_metrics": {"baseline": baseline_metrics, "challenger": challenger_metrics},
        "retention_pct": round(retention * 100.0, 4),
        "utility_improvement": round(improvement, 6),
        "failed_gates": failed,
        "manual_review_required": not failed,
        **_safety(),
    })
    return current, _comparison(current)
