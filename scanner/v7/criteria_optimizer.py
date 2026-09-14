from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

import pandas as pd


V7_2_SCHEMA = "7.2.0-shadow"
TARGET_COLUMNS = {
    "p5_precision_pct": "forward_hit_5pct",
    "p10_precision_pct": "forward_hit_10pct",
    "p15_precision_pct": "forward_hit_15pct",
}

# V7.2 may test only these point-in-time numeric fields and threshold values.
# It cannot invent fields, alter source code, or relax the hard tradability gates.
APPROVED_CRITERIA: dict[str, tuple[float, ...]] = {
    "market_hunt_score": (65.0, 70.0, 75.0, 80.0),
    "effective_rr": (2.5, 3.0, 3.5),
    "technical_score": (60.0, 70.0, 80.0),
    "catalyst_score": (30.0, 45.0),
    "intraday_rvol": (1.2, 1.5, 2.0),
    "runway_to_next_resistance_pct": (5.0, 8.0, 10.0),
    "rs20_vs_spy": (0.0, 3.0, 5.0, 10.0),
    "adr20_pct": (2.0, 3.0, 4.0, 5.0),
    "avg_dollar_volume": (20_000_000.0, 50_000_000.0, 100_000_000.0),
    "median_dollar_volume20": (25_000_000.0, 50_000_000.0, 100_000_000.0),
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _event_time(frame: pd.DataFrame) -> pd.Series:
    for column in ("evidence_session", "as_of_session", "entry_session", "entered_at_utc"):
        if column in frame:
            parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
            if parsed.notna().any():
                return parsed
    return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")


@dataclass(frozen=True)
class CriteriaOptimizerSettings:
    min_total_samples: int = 220
    min_train_sessions: int = 5
    min_validation_sessions: int = 3
    min_validation_samples: int = 60
    min_candidate_samples: int = 30
    validation_fraction: float = 0.30
    desired_p5_precision_pct: float = 55.0
    min_train_retention: float = 0.25
    min_validation_retention: float = 0.25
    min_train_utility_improvement: float = 1.0
    min_validation_utility_improvement: float = 0.5
    max_target_degradation_pp: float = 2.0
    max_false_breakout_degradation_pp: float = 2.0


def _metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {"samples": len(frame)}
    for name, column in TARGET_COLUMNS.items():
        values = frame[column].map(_truthy).astype(float)
        metrics[name] = round(float(values.mean() * 100.0), 4) if len(values) else 0.0
    r_source = frame.get("r_multiple_5d", pd.Series(float("nan"), index=frame.index))
    r_values = pd.to_numeric(r_source, errors="coerce").dropna()
    false_values = frame.get("false_breakout", pd.Series(False, index=frame.index)).map(_truthy)
    metrics["avg_r_multiple"] = round(float(r_values.mean()), 6) if len(r_values) else 0.0
    metrics["false_breakout_rate_pct"] = round(float(false_values.mean() * 100.0), 4) if len(false_values) else 0.0
    metrics["utility"] = round(
        _finite(metrics["p5_precision_pct"]) * 0.50
        + _finite(metrics["p10_precision_pct"]) * 0.30
        + _finite(metrics["p15_precision_pct"]) * 0.20
        + _finite(metrics["avg_r_multiple"]) * 5.0
        - _finite(metrics["false_breakout_rate_pct"]) * 0.15,
        6,
    )
    return metrics


def _comparison(
    split: str,
    baseline: dict[str, float | int],
    proposal: dict[str, float | int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baseline_samples = max(1, int(baseline["samples"]))
    for name, metrics in (("BASELINE", baseline), ("PROPOSAL", proposal)):
        rows.append({
            "split": split,
            "variant": name,
            **metrics,
            "retention_pct": round(int(metrics["samples"]) / baseline_samples * 100.0, 4),
        })
    return rows


def _base_payload(status: str, reason: str, samples: int, settings: CriteriaOptimizerSettings) -> dict[str, Any]:
    return {
        "schema_version": V7_2_SCHEMA,
        "model_version": "untrained",
        "status": status,
        "reason": reason,
        "samples": samples,
        "settings": asdict(settings),
        "approved_criteria": {key: list(values) for key, values in APPROVED_CRITERIA.items()},
        "shadow_only": True,
        "production_applied": False,
        "activation_allowed": False,
        "manual_review_required": False,
        "broker_execution_enabled": False,
    }


def _versioned(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    stable = {key: value for key, value in result.items() if key not in {"created_at_utc", "model_version"}}
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    result["model_version"] = "v7.2-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return result


def fit_criteria_optimizer(
    outcomes: pd.DataFrame,
    settings: CriteriaOptimizerSettings | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Select one bounded threshold on train data and evaluate it once on holdout."""
    settings = settings or CriteriaOptimizerSettings()
    criteria = APPROVED_CRITERIA
    frame = outcomes.copy() if outcomes is not None else pd.DataFrame()
    required = set(TARGET_COLUMNS.values())
    if frame.empty:
        return _base_payload("INSUFFICIENT_DATA", "no mature outcomes", 0, settings), pd.DataFrame(), pd.DataFrame()
    if not required.issubset(frame.columns):
        payload = _base_payload("INSUFFICIENT_DATA", "mature target labels are incomplete", len(frame), settings)
        return payload, pd.DataFrame(), pd.DataFrame()
    frame["_event_time"] = _event_time(frame)
    frame = frame[frame["_event_time"].notna()].sort_values("_event_time").reset_index(drop=True)
    if len(frame) < settings.min_total_samples:
        payload = _base_payload(
            "INSUFFICIENT_DATA",
            f"need >= {settings.min_total_samples} mature point-in-time outcomes",
            len(frame),
            settings,
        )
        return payload, pd.DataFrame(), pd.DataFrame()

    sessions = list(pd.Series(frame["_event_time"].dt.normalize().unique()).sort_values())
    validation_sessions = max(settings.min_validation_sessions, int(math.ceil(len(sessions) * settings.validation_fraction)))
    if len(sessions) - validation_sessions < settings.min_train_sessions:
        payload = _base_payload("INSUFFICIENT_DATA", "need more complete chronological sessions", len(frame), settings)
        return payload, pd.DataFrame(), pd.DataFrame()
    boundary = sessions[-validation_sessions]
    train = frame[frame["_event_time"].dt.normalize() < boundary].copy()
    validation = frame[frame["_event_time"].dt.normalize() >= boundary].copy()
    if len(validation) < settings.min_validation_samples:
        payload = _base_payload("INSUFFICIENT_DATA", "chronological holdout is too small", len(frame), settings)
        return payload, pd.DataFrame(), pd.DataFrame()

    train_baseline = _metrics(train)
    validation_baseline = _metrics(validation)
    if _finite(train_baseline["p5_precision_pct"]) >= settings.desired_p5_precision_pct:
        payload = _base_payload("BASELINE_MEETS_TARGET", "no criteria change is justified", len(frame), settings)
        payload.update({
            "train_samples": len(train),
            "validation_samples": len(validation),
            "train_end_utc": train["_event_time"].max().isoformat(),
            "validation_start_utc": validation["_event_time"].min().isoformat(),
            "baseline_metrics": {"train": train_baseline, "validation": validation_baseline},
        })
        rows = _comparison("TRAIN", train_baseline, train_baseline)
        rows.extend(_comparison("VALIDATION", validation_baseline, validation_baseline))
        return _versioned(payload), pd.DataFrame(rows), pd.DataFrame()

    grid_rows: list[dict[str, Any]] = []
    choices: list[tuple[float, str, float, dict[str, float | int]]] = []
    for criterion, thresholds in criteria.items():
        if criterion not in frame:
            continue
        train_values = pd.to_numeric(train[criterion], errors="coerce")
        for threshold in thresholds:
            selected = train[train_values.ge(float(threshold))]
            metrics = _metrics(selected)
            retention = len(selected) / max(1, len(train))
            improvement = _finite(metrics["utility"]) - _finite(train_baseline["utility"])
            eligible = (
                len(selected) >= settings.min_candidate_samples
                and retention >= settings.min_train_retention
                and improvement >= settings.min_train_utility_improvement
            )
            grid_rows.append({
                "criterion": criterion,
                "operator": ">=",
                "threshold": float(threshold),
                **metrics,
                "retention_pct": round(retention * 100.0, 4),
                "train_utility_improvement": round(improvement, 6),
                "train_gate": "PASS" if eligible else "FAIL",
            })
            if eligible:
                choices.append((improvement, criterion, float(threshold), metrics))

    grid = pd.DataFrame(grid_rows)
    if not choices:
        payload = _base_payload("NO_IMPROVEMENT", "no approved one-parameter change improved training utility", len(frame), settings)
        payload["baseline_metrics"] = {"train": train_baseline, "validation": validation_baseline}
        return _versioned(payload), pd.DataFrame(), grid

    _, criterion, threshold, train_proposal = max(choices, key=lambda item: (item[0], item[3]["samples"]))
    validation_values = pd.to_numeric(validation[criterion], errors="coerce")
    validation_selected = validation[validation_values.ge(threshold)]
    validation_proposal = _metrics(validation_selected)
    retention = len(validation_selected) / max(1, len(validation))
    utility_improvement = _finite(validation_proposal["utility"]) - _finite(validation_baseline["utility"])
    failed: list[str] = []
    if len(validation_selected) < settings.min_candidate_samples:
        failed.append("VALIDATION_SAMPLE_SIZE")
    if retention < settings.min_validation_retention:
        failed.append("VALIDATION_RETENTION")
    if utility_improvement < settings.min_validation_utility_improvement:
        failed.append("VALIDATION_UTILITY")
    for metric in ("p10_precision_pct", "p15_precision_pct"):
        if _finite(validation_proposal[metric]) < _finite(validation_baseline[metric]) - settings.max_target_degradation_pp:
            failed.append(metric.upper() + "_DEGRADATION")
    if _finite(validation_proposal["avg_r_multiple"]) < _finite(validation_baseline["avg_r_multiple"]):
        failed.append("EXPECTANCY_DEGRADATION")
    if _finite(validation_proposal["false_breakout_rate_pct"]) > (
        _finite(validation_baseline["false_breakout_rate_pct"]) + settings.max_false_breakout_degradation_pp
    ):
        failed.append("FALSE_BREAKOUT_DEGRADATION")

    core = {
        "schema_version": V7_2_SCHEMA,
        "status": "PROPOSAL_ELIGIBLE" if not failed else "SHADOW_ONLY",
        "reason": "holdout gates passed; manual review required" if not failed else "proposal failed untouched holdout gates",
        "samples": len(frame),
        "train_samples": len(train),
        "validation_samples": len(validation),
        "train_end_utc": train["_event_time"].max().isoformat(),
        "validation_start_utc": validation["_event_time"].min().isoformat(),
        "recommended_change": {"criterion": criterion, "operator": ">=", "proposed_value": threshold},
        "baseline_metrics": {"train": train_baseline, "validation": validation_baseline},
        "proposal_metrics": {"train": train_proposal, "validation": validation_proposal},
        "validation_utility_improvement": round(utility_improvement, 6),
        "validation_retention_pct": round(retention * 100.0, 4),
        "failed_gates": failed,
        "settings": asdict(settings),
        "approved_criteria": {key: list(values) for key, values in criteria.items()},
        "shadow_only": True,
        "production_applied": False,
        "activation_allowed": False,
        "manual_review_required": not failed,
        "broker_execution_enabled": False,
    }
    validation_rows = _comparison("TRAIN", train_baseline, train_proposal)
    validation_rows.extend(_comparison("VALIDATION", validation_baseline, validation_proposal))
    return _versioned(core), pd.DataFrame(validation_rows), grid
