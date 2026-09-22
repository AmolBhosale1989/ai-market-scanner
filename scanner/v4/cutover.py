from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

import pandas as pd

from ..control_plane import append_state, read_state


CUTOVER_SCHEMA = "4.6.0"
PRIMARY_MODE = "V4_5_PRIMARY"
SHADOW_MODE = "V3_PRIMARY_V4_SHADOW"


@dataclass(frozen=True)
class CutoverSettings:
    top_k: int = 20
    min_shadow_agreement_pct: float = 60.0
    min_alert_samples: int = 30
    min_alert_precision_pct: float = 55.0
    min_market_uptime_pct: float = 95.0
    max_event_lag_p95_ms: float = 120_000.0
    require_v45_status: str = "VALIDATED_SHADOW"


@dataclass(frozen=True)
class CutoverDecision:
    generated_at_utc: str
    status: str
    eligible: bool
    shadow_agreement_pct: float | None
    alert_samples: int
    alert_precision_pct: float | None
    market_hours_uptime_pct: float | None
    event_lag_p95_ms: float | None
    v45_model_status: str
    model_monitor_status: str
    evidence_health_status: str
    rollback_drill_passed: bool
    failed_gates: tuple[str, ...]
    settings: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ranked_tickers(frame: pd.DataFrame, score_column: str, top_k: int) -> list[str]:
    if frame is None or frame.empty or "ticker" not in frame:
        return []
    work = frame.copy()
    work["ticker"] = work["ticker"].astype(str).str.upper()
    if score_column in work:
        score = pd.to_numeric(work[score_column], errors="coerce")
        work = work.assign(_score=score).sort_values("_score", ascending=False)
    return work.drop_duplicates("ticker").head(top_k)["ticker"].tolist()


def shadow_agreement(
    v3_candidates: pd.DataFrame,
    v45_candidates: pd.DataFrame,
    top_k: int = 20,
) -> float | None:
    v3 = _ranked_tickers(v3_candidates, "market_hunt_score", top_k)
    v45 = _ranked_tickers(v45_candidates, "v45_calibrated_score", top_k)
    if not v3 or not v45:
        return None
    denom = max(1, min(top_k, len(v3), len(v45)))
    return round(len(set(v3).intersection(v45)) / denom * 100, 2)


def alert_precision(outcomes: pd.DataFrame) -> tuple[int, float | None]:
    if outcomes is None or outcomes.empty:
        return 0, None
    frame = outcomes.copy()
    entered = frame.get("entered_at_utc", pd.Series("", index=frame.index)).fillna("").astype(str).str.len() > 0
    resolved = frame.get("daily_bars_resolved", pd.Series(0, index=frame.index))
    resolved = pd.to_numeric(resolved, errors="coerce").fillna(0) >= 1
    frame = frame[entered & resolved].copy()
    if frame.empty:
        return 0, None
    hits = frame.get("forward_hit_5pct", pd.Series(False, index=frame.index)).fillna(False)
    hits = hits.astype(str).str.lower().isin({"true", "1", "yes"}) | (hits == True)
    return len(frame), round(float(hits.mean() * 100), 2)


def rollback_drill() -> bool:
    original = {
        "schema_version": CUTOVER_SCHEMA,
        "mode": SHADOW_MODE,
        "active_model_version": "",
        "previous_mode": "",
    }
    before = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    promoted = {**original, "mode": PRIMARY_MODE, "active_model_version": "drill-model"}
    rolled_back = {**promoted, **original}
    after = hashlib.sha256(json.dumps(rolled_back, sort_keys=True).encode()).hexdigest()
    return before == after and rolled_back.get("mode") == SHADOW_MODE


def evaluate_cutover(
    v3_candidates: pd.DataFrame,
    v45_candidates: pd.DataFrame,
    outcomes: pd.DataFrame,
    model_payload: dict[str, Any],
    health_payload: dict[str, Any],
    settings: CutoverSettings | None = None,
) -> CutoverDecision:
    settings = settings or CutoverSettings()
    agreement = shadow_agreement(v3_candidates, v45_candidates, settings.top_k)
    samples, precision = alert_precision(outcomes)
    uptime = health_payload.get("market_hours_uptime_pct")
    lag = health_payload.get("event_lag_p95_ms")
    model_status = str(model_payload.get("promotion_status", ""))
    monitor_status = str(health_payload.get("model_monitor_status", "HEALTHY"))
    evidence_status = str(health_payload.get("evidence_health_status", "HEALTHY"))
    rollback_ok = rollback_drill()
    failed: list[str] = []

    if model_status != settings.require_v45_status:
        failed.append("V4_5_MODEL_STATUS")
    if monitor_status != "HEALTHY":
        failed.append("MODEL_MONITOR")
    if evidence_status != "HEALTHY":
        failed.append("EVIDENCE_PIPELINE")
    if agreement is None or agreement < settings.min_shadow_agreement_pct:
        failed.append("SHADOW_AGREEMENT")
    if samples < settings.min_alert_samples:
        failed.append("ALERT_SAMPLE_SIZE")
    if precision is None or precision < settings.min_alert_precision_pct:
        failed.append("ALERT_PRECISION")
    try:
        uptime_value = float(uptime)
    except (TypeError, ValueError):
        uptime_value = math.nan
    if not math.isfinite(uptime_value) or uptime_value < settings.min_market_uptime_pct:
        failed.append("MARKET_UPTIME")
    try:
        lag_value = float(lag)
    except (TypeError, ValueError):
        lag_value = math.nan
    if not math.isfinite(lag_value) or lag_value > settings.max_event_lag_p95_ms:
        failed.append("EVENT_LAG")
    if not rollback_ok:
        failed.append("ROLLBACK_DRILL")

    eligible = not failed
    return CutoverDecision(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        status="ELIGIBLE_FOR_MANUAL_CUTOVER" if eligible else "HOLD_SHADOW",
        eligible=eligible,
        shadow_agreement_pct=agreement,
        alert_samples=samples,
        alert_precision_pct=precision,
        market_hours_uptime_pct=None if not math.isfinite(uptime_value) else round(uptime_value, 2),
        event_lag_p95_ms=None if not math.isfinite(lag_value) else round(lag_value, 1),
        v45_model_status=model_status,
        model_monitor_status=monitor_status,
        evidence_health_status=evidence_status,
        rollback_drill_passed=rollback_ok,
        failed_gates=tuple(failed),
        settings=asdict(settings),
    )


class CutoverController:
    def __init__(self, namespace: str = "v4_cutover"):
        self.namespace = namespace

    def state(self) -> dict[str, Any]:
        current = read_state(self.namespace, "state", default={}) or {}
        if current:
            return current
        return {
            "schema_version": CUTOVER_SCHEMA,
            "mode": SHADOW_MODE,
            "active_model_version": "",
            "previous_mode": "",
            "updated_at_utc": "",
        }

    def activate(self, decision: CutoverDecision, model_version: str) -> dict[str, Any]:
        if not decision.eligible:
            raise RuntimeError("V4.6 cutover blocked: validation gates have not passed")
        if not model_version:
            raise RuntimeError("V4.6 cutover blocked: model version missing")
        current = self.state()
        value = {
            "schema_version": CUTOVER_SCHEMA,
            "mode": PRIMARY_MODE,
            "active_model_version": model_version,
            "previous_mode": current.get("mode", SHADOW_MODE),
            "activated_at_utc": datetime.now(timezone.utc).isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "decision": decision.to_dict(),
        }
        append_state(self.namespace, "state", value)
        return value

    def rollback(self, reason: str) -> dict[str, Any]:
        current = self.state()
        value = {
            "schema_version": CUTOVER_SCHEMA,
            "mode": SHADOW_MODE,
            "active_model_version": "",
            "previous_mode": current.get("mode", SHADOW_MODE),
            "rollback_reason": str(reason),
            "rolled_back_at_utc": datetime.now(timezone.utc).isoformat(),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        append_state(self.namespace, "state", value)
        return value


def load_cutover_mode(expected_model_version: str = "") -> str:
    state = read_state("v4_cutover", "state", default={}) or {}
    if state.get("mode") != PRIMARY_MODE:
        return SHADOW_MODE
    active = str(state.get("active_model_version", ""))
    if expected_model_version and active != expected_model_version:
        return SHADOW_MODE
    return PRIMARY_MODE


def apply_active_ranking(
    candidates: pd.DataFrame,
    model_payload: dict[str, Any],
) -> tuple[pd.DataFrame, str]:
    if candidates is None or candidates.empty:
        return pd.DataFrame(), SHADOW_MODE
    state = read_state("v4_cutover", "state", default={}) or {}
    if state.get("mode") != PRIMARY_MODE:
        return candidates.copy(), SHADOW_MODE
    if not model_payload:
        return candidates.copy(), SHADOW_MODE
    try:
        from .calibration import CalibratedRankingModel
        model = CalibratedRankingModel(model_payload)
    except Exception:
        return candidates.copy(), SHADOW_MODE
    active_version = str(state.get("active_model_version", ""))
    if not active_version or model.version != active_version:
        return candidates.copy(), SHADOW_MODE
    if model.promotion_status != "VALIDATED_SHADOW":
        return candidates.copy(), SHADOW_MODE
    scored = model.score(candidates)
    scored["v4_active_rank_score"] = scored["v45_calibrated_score"]
    scored["v4_ranking_mode"] = PRIMARY_MODE
    return scored, PRIMARY_MODE
