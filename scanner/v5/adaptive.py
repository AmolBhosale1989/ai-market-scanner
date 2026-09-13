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

from ..v4.calibration import CalibratedRankingModel, FitSettings, fit_model


V5_SCHEMA = "5.0.0-shadow"
TARGETS = {"p5": "forward_hit_5pct", "p10": "forward_hit_10pct", "p15": "forward_hit_15pct"}
SEGMENT_FEATURES = ("market_regime_state", "theme", "catalyst_type", "entry_model")


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


def _segment(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text else "__MISSING__"


def _brier(actual: pd.Series, probability: pd.Series) -> float:
    usable = probability.notna()
    return float(((probability[usable] - actual[usable]) ** 2).mean()) if usable.any() else math.nan


@dataclass(frozen=True)
class AdaptiveSettings:
    min_total_samples: int = 100
    min_validation_samples: int = 25
    min_segment_samples: int = 8
    validation_fraction: float = 0.30
    prior_strength: float = 20.0
    max_brier_degradation: float = 0.005


class AdaptiveRegimeModel:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.base_model = CalibratedRankingModel(payload["base_model"]) if payload.get("base_model") else None

    @property
    def version(self) -> str:
        return str(self.payload.get("model_version", ""))

    @property
    def status(self) -> str:
        return str(self.payload.get("promotion_status", "INSUFFICIENT_DATA"))

    def score(self, candidates: pd.DataFrame) -> pd.DataFrame:
        if candidates is None or candidates.empty or self.base_model is None:
            return candidates.copy() if candidates is not None else pd.DataFrame()
        out = self.base_model.score(candidates)
        for target in TARGETS:
            base_column = f"v45_{target}_probability"
            probabilities = []
            for _, row in out.iterrows():
                base = float(row.get(base_column, 50.0)) / 100.0
                weighted, total = base, 1.0
                for feature, mapping in self.payload.get("segments", {}).get(target, {}).items():
                    record = mapping.get(_segment(row.get(feature)))
                    if not record:
                        continue
                    support = float(record["samples"])
                    weight = min(2.5, math.log1p(support) / 2.0)
                    weighted += float(record["probability"]) * weight
                    total += weight
                probabilities.append(round(min(99.5, max(0.5, weighted / total * 100.0)), 1))
            out[f"v5_{target}_probability"] = probabilities
        legacy = pd.to_numeric(out.get("market_hunt_score", 0), errors="coerce").fillna(0).clip(0, 100)
        out["v5_adaptive_score"] = (
            out["v5_p5_probability"] * 0.20
            + out["v5_p10_probability"] * 0.55
            + out["v5_p15_probability"] * 0.25
            + legacy * 0.15
        ).round(2)
        out["v5_model_version"] = self.version
        out["v5_model_status"] = self.status
        return out.sort_values(["v5_adaptive_score", "market_hunt_score"], ascending=[False, False]).reset_index(drop=True)


def fit_adaptive_model(
    outcomes: pd.DataFrame,
    settings: AdaptiveSettings | None = None,
) -> tuple[AdaptiveRegimeModel, pd.DataFrame]:
    settings = settings or AdaptiveSettings()
    frame = outcomes.copy() if outcomes is not None else pd.DataFrame()
    if frame.empty:
        return AdaptiveRegimeModel({"schema_version": V5_SCHEMA, "model_version": "untrained", "promotion_status": "INSUFFICIENT_DATA", "reason": "no outcomes"}), pd.DataFrame()
    frame["_event_time"] = _event_time(frame)
    frame = frame[frame["_event_time"].notna()].sort_values("_event_time").reset_index(drop=True)
    if len(frame) < settings.min_total_samples or any(column not in frame for column in TARGETS.values()):
        payload = {"schema_version": V5_SCHEMA, "model_version": "untrained", "promotion_status": "INSUFFICIENT_DATA", "samples": len(frame), "reason": f"need >= {settings.min_total_samples} resolved samples and all target labels"}
        return AdaptiveRegimeModel(payload), pd.DataFrame()

    split = max(settings.min_validation_samples, int(math.ceil(len(frame) * settings.validation_fraction)))
    split = min(split, len(frame) - 1)
    train, validation = frame.iloc[:-split].copy(), frame.iloc[-split:].copy()
    base_settings = FitSettings(
        min_total_samples=max(30, min(60, len(train) // 2)),
        min_validation_samples=max(8, min(15, len(train) // 5)),
        min_positive_samples=4,
        validation_fraction=0.25,
    )
    base_model, _ = fit_model(train, base_settings)
    if not base_model.payload.get("targets"):
        payload = {"schema_version": V5_SCHEMA, "model_version": "untrained", "promotion_status": "INSUFFICIENT_DATA", "samples": len(frame), "reason": "V4.5 base model unavailable"}
        return AdaptiveRegimeModel(payload), pd.DataFrame()

    segments: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for target, label in TARGETS.items():
        segments[target] = {}
        base_rate = float(train[label].map(_truthy).mean())
        for feature in SEGMENT_FEATURES:
            if feature not in train:
                continue
            mapping: dict[str, dict[str, Any]] = {}
            keys = train[feature].map(_segment)
            for key, indices in keys.groupby(keys).groups.items():
                labels = train.loc[indices, label].map(_truthy).astype(int)
                if len(labels) < settings.min_segment_samples:
                    continue
                probability = (labels.sum() + base_rate * settings.prior_strength) / (len(labels) + settings.prior_strength)
                mapping[key] = {"samples": len(labels), "wins": int(labels.sum()), "probability": round(float(probability), 6)}
            segments[target][feature] = mapping

    draft = {"schema_version": V5_SCHEMA, "base_model": base_model.payload, "segments": segments}
    model = AdaptiveRegimeModel(draft)
    base_validation = base_model.score(validation)
    adaptive_validation = model.score(validation)
    metrics = []
    passes = []
    for target, label in TARGETS.items():
        # score() ranks rows, so each model's labels must come from its own
        # ranked frame to preserve prediction/label alignment.
        actual_base = base_validation[label].map(_truthy).astype(float)
        actual_adaptive = adaptive_validation[label].map(_truthy).astype(float)
        base_probability = pd.to_numeric(base_validation[f"v45_{target}_probability"], errors="coerce") / 100.0
        adaptive_probability = pd.to_numeric(adaptive_validation[f"v5_{target}_probability"], errors="coerce") / 100.0
        base_brier = _brier(actual_base, base_probability)
        adaptive_brier = _brier(actual_adaptive, adaptive_probability)
        passed = math.isfinite(adaptive_brier) and adaptive_brier <= base_brier + settings.max_brier_degradation
        passes.append(passed)
        metrics.append({
            "target": target,
            "validation_samples": len(validation),
            "base_v45_brier_score": round(base_brier, 6),
            "v5_brier_score": round(adaptive_brier, 6),
            "v5_brier_improvement": round(base_brier - adaptive_brier, 6),
            "validation_gate": "PASS" if passed else "FAIL",
        })

    core = {
        **draft,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "samples": len(frame),
        "train_samples": len(train),
        "validation_samples": len(validation),
        "train_end_utc": train["_event_time"].iloc[-1].isoformat(),
        "validation_start_utc": validation["_event_time"].iloc[0].isoformat(),
        "settings": asdict(settings),
    }
    version_core = {key: value for key, value in core.items() if key != "created_at_utc"}
    version_core["base_model"] = {
        key: value for key, value in base_model.payload.items()
        if key not in {"created_at_utc", "model_version"}
    }
    canonical = json.dumps(version_core, sort_keys=True, separators=(",", ":"), default=str)
    core["model_version"] = "v5-" + hashlib.sha256(canonical.encode()).hexdigest()[:12]
    core["promotion_status"] = "VALIDATED_SHADOW" if all(passes) else "SHADOW_ONLY"
    return AdaptiveRegimeModel(core), pd.DataFrame(metrics)


def save_model(model: AdaptiveRegimeModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.payload, indent=2, sort_keys=True, allow_nan=False))
