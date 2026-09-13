from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any

import pandas as pd


MODEL_MONITOR_SCHEMA = "4.monitor.1"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class ModelMonitorSettings:
    min_mature_samples: int = 30
    recent_window: int = 60
    max_brier_degradation: float = 0.02
    min_average_r: float = 0.0
    max_false_breakout_pct: float = 45.0
    min_hit5_delta_vs_v3_pct: float = -5.0


def evaluate_model_health(
    observations: pd.DataFrame,
    model_payload: dict[str, Any],
    settings: ModelMonitorSettings | None = None,
) -> dict[str, Any]:
    settings = settings or ModelMonitorSettings()
    version = str(model_payload.get("model_version", ""))
    result: dict[str, Any] = {
        "schema_version": MODEL_MONITOR_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "COLLECTING",
        "model_version": version,
        "mature_samples": 0,
        "failed_checks": [],
        "settings": asdict(settings),
    }
    if observations is None or observations.empty:
        result["detail"] = "no shadow observations"
        return result

    frame = observations.copy()
    mature = pd.to_numeric(frame.get("daily_bars_resolved"), errors="coerce").fillna(0).ge(5)
    selected = frame.get("selected_v45", pd.Series(False, index=frame.index)).map(_truthy)
    validated = frame.get("v45_model_status", pd.Series("", index=frame.index)).astype(str).eq("VALIDATED_SHADOW")
    v45 = frame[mature & selected & validated].sort_values(["as_of_session", "v45_rank"]).tail(settings.recent_window)
    result["mature_samples"] = len(v45)
    result["model_versions_seen"] = int(v45.get("v45_model_version", pd.Series(dtype=object)).nunique())
    if len(v45) < settings.min_mature_samples:
        result["detail"] = f"need >= {settings.min_mature_samples} mature out-of-sample observations"
        return result

    failed: list[str] = []
    for threshold in (5, 10, 15):
        actual = v45[f"forward_hit_{threshold}pct"].map(_truthy).astype(float)
        probability = pd.to_numeric(v45.get(f"v45_p{threshold}_probability"), errors="coerce") / 100.0
        usable = probability.notna()
        brier = float(((probability[usable] - actual[usable]) ** 2).mean()) if usable.any() else math.nan
        global_probability = _number(model_payload.get("targets", {}).get(f"p{threshold}", {}).get("global_probability"))
        baseline = (
            float(((global_probability - actual) ** 2).mean())
            if global_probability is not None else math.nan
        )
        result[f"p{threshold}_brier_score"] = round(brier, 6) if math.isfinite(brier) else None
        result[f"p{threshold}_baseline_brier"] = round(baseline, 6) if math.isfinite(baseline) else None
        result[f"p{threshold}_brier_degradation"] = (
            round(brier - baseline, 6) if math.isfinite(brier) and math.isfinite(baseline) else None
        )
        if not math.isfinite(brier) or not math.isfinite(baseline) or brier > baseline + settings.max_brier_degradation:
            failed.append(f"P{threshold}_CALIBRATION")

    hit5 = float(v45["forward_hit_5pct"].map(_truthy).mean() * 100)
    average_r = pd.to_numeric(v45.get("r_multiple_5d"), errors="coerce").mean()
    false_breakout = float(v45.get("false_breakout", pd.Series(False, index=v45.index)).map(_truthy).mean() * 100)
    sessions = set(v45["as_of_session"].astype(str))
    v3 = frame[
        mature
        & frame.get("selected_v3", pd.Series(False, index=frame.index)).map(_truthy)
        & frame["as_of_session"].astype(str).isin(sessions)
    ]
    v3_hit5 = float(v3["forward_hit_5pct"].map(_truthy).mean() * 100) if len(v3) else math.nan
    delta = hit5 - v3_hit5 if math.isfinite(v3_hit5) else math.nan
    result.update({
        "hit_5pct_rate": round(hit5, 2),
        "v3_hit_5pct_rate_same_sessions": round(v3_hit5, 2) if math.isfinite(v3_hit5) else None,
        "hit_5pct_delta_vs_v3": round(delta, 2) if math.isfinite(delta) else None,
        "average_r_multiple_5d": round(float(average_r), 4) if math.isfinite(average_r) else None,
        "false_breakout_rate_pct": round(false_breakout, 2),
    })
    if not math.isfinite(average_r) or average_r < settings.min_average_r:
        failed.append("NEGATIVE_EXPECTANCY")
    if false_breakout > settings.max_false_breakout_pct:
        failed.append("FALSE_BREAKOUT_RATE")
    if not math.isfinite(delta) or delta < settings.min_hit5_delta_vs_v3_pct:
        failed.append("V3_RELATIVE_PRECISION")
    result["failed_checks"] = sorted(set(failed))
    result["status"] = "DEGRADED" if failed else "HEALTHY"
    result["detail"] = "automatic rollback required" if failed else "recent shadow evidence is within guardrails"
    return result
