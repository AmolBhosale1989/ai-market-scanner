from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..v4.calibration import TARGETS
from ..v5.adaptive import AdaptiveRegimeModel, AdaptiveSettings, fit_adaptive_model


V6_SCHEMA = "6.0.0-shadow"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _event_time(frame: pd.DataFrame) -> pd.Series:
    for column in ("entered_at_utc", "first_seen_at_utc", "last_seen_at_utc"):
        if column in frame:
            parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
            if parsed.notna().any():
                return parsed
    return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")


def _brier(actual: pd.Series, probability: pd.Series) -> float:
    usable = probability.notna()
    return float(((probability[usable] - actual[usable]) ** 2).mean()) if usable.any() else math.nan


def _stable_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_payload(item)
            for key, item in value.items()
            if key not in {"created_at_utc", "model_version"}
        }
    if isinstance(value, list):
        return [_stable_payload(item) for item in value]
    return value


def _whole_session_three_way(
    frame: pd.DataFrame,
    calibration_rows: int,
    validation_rows: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if frame.empty or frame["_event_time"].nunique() < 3:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    validation_boundary = frame.iloc[-validation_rows]["_event_time"]
    before_validation = frame[frame["_event_time"] < validation_boundary].copy()
    validation = frame[frame["_event_time"] >= validation_boundary].copy()
    if len(before_validation) <= calibration_rows:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    calibration_boundary = before_validation.iloc[-calibration_rows]["_event_time"]
    train = before_validation[before_validation["_event_time"] < calibration_boundary].copy()
    calibration = before_validation[before_validation["_event_time"] >= calibration_boundary].copy()
    return train, calibration, validation


@dataclass(frozen=True)
class UncertaintySettings:
    min_total_samples: int = 220
    min_calibration_samples: int = 30
    min_validation_samples: int = 30
    calibration_fraction: float = 0.15
    validation_fraction: float = 0.15
    v45_weight: float = 0.40
    v5_weight: float = 0.60
    interval_quantile: float = 0.90
    max_disagreement_pp: float = 18.0
    max_brier_degradation: float = 0.005
    min_interval_coverage: float = 0.75


class UncertaintyEnsembleModel:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.v5_model = AdaptiveRegimeModel(payload["v5_model"]) if payload.get("v5_model") else None

    @property
    def version(self) -> str:
        return str(self.payload.get("model_version", ""))

    @property
    def status(self) -> str:
        return str(self.payload.get("promotion_status", "INSUFFICIENT_DATA"))

    def score(self, candidates: pd.DataFrame) -> pd.DataFrame:
        if candidates is None or candidates.empty or self.v5_model is None:
            return pd.DataFrame()
        out = self.v5_model.score(candidates)
        if out.empty or any(f"v5_{target}_probability" not in out for target in TARGETS):
            return pd.DataFrame()
        settings = self.payload.get("settings", {})
        w45 = float(settings.get("v45_weight", 0.40))
        w5 = float(settings.get("v5_weight", 0.60))
        total = max(w45 + w5, 1e-9)
        disagreements: list[pd.Series] = []
        for target in TARGETS:
            p45 = pd.to_numeric(out[f"v45_{target}_probability"], errors="coerce")
            p5 = pd.to_numeric(out[f"v5_{target}_probability"], errors="coerce")
            ensemble = ((p45 * w45 + p5 * w5) / total).clip(0.5, 99.5)
            disagreement = (p45 - p5).abs()
            margin = float(self.payload.get("targets", {}).get(target, {}).get("interval_margin_pp", 25.0))
            out[f"v6_{target}_probability"] = ensemble.round(1)
            out[f"v6_{target}_lower"] = (ensemble - margin).clip(0.0, 100.0).round(1)
            out[f"v6_{target}_upper"] = (ensemble + margin).clip(0.0, 100.0).round(1)
            out[f"v6_{target}_disagreement_pp"] = disagreement.round(1)
            disagreements.append(disagreement)
        out["v6_max_disagreement_pp"] = pd.concat(disagreements, axis=1).max(axis=1).round(1)
        limit = float(settings.get("max_disagreement_pp", 18.0))
        out["v6_confidence"] = np.select(
            [out["v6_max_disagreement_pp"].le(limit / 2), out["v6_max_disagreement_pp"].le(limit)],
            ["HIGH", "MODERATE"],
            default="LOW",
        )
        out["v6_decision"] = np.where(out["v6_max_disagreement_pp"].gt(limit), "ABSTAIN", "RANK")
        legacy_source = out["market_hunt_score"] if "market_hunt_score" in out else pd.Series(0.0, index=out.index)
        legacy = pd.to_numeric(legacy_source, errors="coerce").fillna(0).clip(0, 100)
        robust_probability = (
            out["v6_p5_lower"] * 0.20
            + out["v6_p10_lower"] * 0.55
            + out["v6_p15_lower"] * 0.25
        )
        out["v6_robust_score"] = (robust_probability * 0.90 + legacy * 0.10).round(2)
        out.loc[out["v6_decision"].eq("ABSTAIN"), "v6_robust_score"] = 0.0
        out["v6_model_version"] = self.version
        out["v6_model_status"] = self.status
        return out.sort_values(
            ["v6_robust_score", "v5_adaptive_score", "market_hunt_score"],
            ascending=[False, False, False],
        ).reset_index(drop=True)


def _insufficient(reason: str, samples: int = 0) -> tuple[UncertaintyEnsembleModel, pd.DataFrame]:
    return UncertaintyEnsembleModel({
        "schema_version": V6_SCHEMA,
        "model_version": "untrained",
        "promotion_status": "INSUFFICIENT_DATA",
        "samples": samples,
        "reason": reason,
    }), pd.DataFrame()


def fit_uncertainty_model(
    outcomes: pd.DataFrame,
    settings: UncertaintySettings | None = None,
) -> tuple[UncertaintyEnsembleModel, pd.DataFrame]:
    settings = settings or UncertaintySettings()
    frame = outcomes.copy() if outcomes is not None else pd.DataFrame()
    if frame.empty:
        return _insufficient("no outcomes")
    frame["_event_time"] = _event_time(frame)
    frame = frame[frame["_event_time"].notna()].sort_values("_event_time").reset_index(drop=True)
    if len(frame) < settings.min_total_samples or any(label not in frame for label in TARGETS.values()):
        return _insufficient(
            f"need >= {settings.min_total_samples} resolved samples and all target labels",
            len(frame),
        )

    validation_size = max(settings.min_validation_samples, int(math.ceil(len(frame) * settings.validation_fraction)))
    calibration_size = max(settings.min_calibration_samples, int(math.ceil(len(frame) * settings.calibration_fraction)))
    train, calibration, validation = _whole_session_three_way(frame, calibration_size, validation_size)
    if len(train) < 100 or len(calibration) < settings.min_calibration_samples or len(validation) < settings.min_validation_samples:
        return _insufficient("need at least 100 earlier training samples after holdouts", len(frame))

    v5_settings = AdaptiveSettings(
        min_total_samples=100,
        min_validation_samples=max(20, min(30, len(train) // 5)),
        min_segment_samples=8,
    )
    v5_model, _ = fit_adaptive_model(train, v5_settings)
    if v5_model.base_model is None:
        return _insufficient("V5 base model unavailable", len(frame))

    draft = {
        "schema_version": V6_SCHEMA,
        "v5_model": v5_model.payload,
        "settings": asdict(settings),
        "targets": {},
    }
    provisional = UncertaintyEnsembleModel(draft)
    calibration_scored = provisional.score(calibration)
    if calibration_scored.empty:
        return _insufficient("unable to score uncertainty calibration holdout", len(frame))
    for target, label in TARGETS.items():
        actual = calibration_scored[label].map(_truthy).astype(float) * 100.0
        predicted = pd.to_numeric(calibration_scored[f"v6_{target}_probability"], errors="coerce")
        errors = (actual - predicted).abs().dropna()
        margin = float(errors.quantile(settings.interval_quantile)) if len(errors) else 25.0
        draft["targets"][target] = {"interval_margin_pp": round(min(49.0, max(3.0, margin)), 3)}

    model = UncertaintyEnsembleModel(draft)
    scored = model.score(validation)
    metrics: list[dict[str, Any]] = []
    passes: list[bool] = []
    for target, label in TARGETS.items():
        actual = scored[label].map(_truthy).astype(float)
        v5_probability = pd.to_numeric(scored[f"v5_{target}_probability"], errors="coerce") / 100.0
        v6_probability = pd.to_numeric(scored[f"v6_{target}_probability"], errors="coerce") / 100.0
        lower = pd.to_numeric(scored[f"v6_{target}_lower"], errors="coerce") / 100.0
        upper = pd.to_numeric(scored[f"v6_{target}_upper"], errors="coerce") / 100.0
        v5_brier = _brier(actual, v5_probability)
        v6_brier = _brier(actual, v6_probability)
        coverage = float(((actual >= lower) & (actual <= upper)).mean())
        passed = (
            math.isfinite(v6_brier)
            and v6_brier <= v5_brier + settings.max_brier_degradation
            and coverage >= settings.min_interval_coverage
        )
        passes.append(passed)
        metrics.append({
            "target": target,
            "validation_samples": len(validation),
            "v5_brier_score": round(v5_brier, 6),
            "v6_brier_score": round(v6_brier, 6),
            "v6_brier_improvement": round(v5_brier - v6_brier, 6),
            "interval_coverage": round(coverage, 4),
            "interval_margin_pp": draft["targets"][target]["interval_margin_pp"],
            "validation_gate": "PASS" if passed else "FAIL",
        })

    core = {
        **draft,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "samples": len(frame),
        "train_samples": len(train),
        "calibration_samples": len(calibration),
        "validation_samples": len(validation),
        "train_end_utc": train["_event_time"].iloc[-1].isoformat(),
        "calibration_start_utc": calibration["_event_time"].iloc[0].isoformat(),
        "calibration_end_utc": calibration["_event_time"].iloc[-1].isoformat(),
        "validation_start_utc": validation["_event_time"].iloc[0].isoformat(),
    }
    version_core = {key: value for key, value in core.items() if key != "created_at_utc"}
    version_core["v5_model"] = _stable_payload(v5_model.payload)
    canonical = json.dumps(version_core, sort_keys=True, separators=(",", ":"), default=str)
    core["model_version"] = "v6-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]
    core["promotion_status"] = "VALIDATED_SHADOW" if all(passes) else "SHADOW_ONLY"
    return UncertaintyEnsembleModel(core), pd.DataFrame(metrics)


def save_model(model: UncertaintyEnsembleModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.payload, indent=2, sort_keys=True, allow_nan=False))
