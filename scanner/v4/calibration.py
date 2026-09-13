from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


MODEL_SCHEMA = "4.5.0"
TARGETS = {
    "p5": "forward_hit_5pct",
    "p10": "forward_hit_10pct",
    "p15": "forward_hit_15pct",
}
NUMERIC_SPECS = {
    "market_hunt_score": [-1e9, 50, 60, 70, 80, 90, 1e9],
    "technical_score": [-1e9, 45, 60, 75, 85, 1e9],
    "catalyst_score": [-1e9, 20, 40, 60, 80, 1e9],
    "effective_rr": [-1e9, 2.0, 2.5, 3.0, 4.0, 1e9],
    "intraday_rvol": [-1e9, 0.8, 1.2, 1.8, 2.5, 4.0, 1e9],
    "theme_score": [-1e9, 40, 55, 70, 85, 1e9],
}
CATEGORICAL_FEATURES = (
    "stage",
    "market_regime_state",
    "entry_model",
    "theme",
)


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


def _bucket_numeric(value: Any, bins: list[float]) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "__MISSING__"
    if not math.isfinite(number):
        return "__MISSING__"
    for left, right in zip(bins[:-1], bins[1:]):
        if left <= number < right:
            return f"[{left:g},{right:g})"
    return "__MISSING__"


def _bucket(feature: str, value: Any) -> str:
    if feature in NUMERIC_SPECS:
        return _bucket_numeric(value, NUMERIC_SPECS[feature])
    text = str(value or "").strip().upper()
    return text if text else "__MISSING__"


def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(y) else math.nan


@dataclass(frozen=True)
class FitSettings:
    min_total_samples: int = 60
    min_validation_samples: int = 15
    min_positive_samples: int = 8
    validation_fraction: float = 0.30
    prior_strength: float = 12.0
    max_brier_degradation: float = 0.01


class CalibratedRankingModel:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    @property
    def version(self) -> str:
        return str(self.payload.get("model_version", ""))

    @property
    def promotion_status(self) -> str:
        return str(self.payload.get("promotion_status", "INSUFFICIENT_DATA"))

    def _predict_target(self, row: pd.Series, target: str) -> float:
        model = self.payload["targets"][target]
        base = float(model["global_probability"])
        weighted = base
        weight_total = 1.0
        for feature, mapping in model.get("features", {}).items():
            key = _bucket(feature, row.get(feature))
            record = mapping.get(key)
            if not record:
                continue
            support = float(record.get("samples", 0))
            probability = float(record["probability"])
            weight = min(2.0, math.log1p(support) / 2.0)
            weighted += probability * weight
            weight_total += weight
        return min(0.995, max(0.005, weighted / weight_total))

    def score(self, candidates: pd.DataFrame) -> pd.DataFrame:
        if candidates is None or candidates.empty:
            return pd.DataFrame()
        out = candidates.copy()
        for target in TARGETS:
            out[f"v45_{target}_probability"] = [
                round(self._predict_target(row, target) * 100, 1)
                for _, row in out.iterrows()
            ]
        legacy_source = out["market_hunt_score"] if "market_hunt_score" in out else pd.Series(0.0, index=out.index)
        legacy = pd.to_numeric(legacy_source, errors="coerce").fillna(0)
        out["v45_calibrated_score"] = (
            out["v45_p5_probability"] * 0.20
            + out["v45_p10_probability"] * 0.55
            + out["v45_p15_probability"] * 0.25
            + legacy.clip(0, 100) * 0.15
        ).round(2)
        out["v45_model_version"] = self.version
        out["v45_model_status"] = self.promotion_status
        return out.sort_values(
            ["v45_calibrated_score", "market_hunt_score"],
            ascending=[False, False],
        ).reset_index(drop=True)


def _fit_target(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    label_col: str,
    settings: FitSettings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    y_train = train[label_col].map(_truthy).astype(int)
    y_val = validation[label_col].map(_truthy).astype(int)
    positives = int(y_train.sum())
    base = float((positives + 2) / (len(train) + 4))

    feature_maps: dict[str, dict[str, dict[str, Any]]] = {}
    features = [
        feature for feature in (*NUMERIC_SPECS.keys(), *CATEGORICAL_FEATURES)
        if feature in train.columns
    ]
    for feature in features:
        grouped: dict[str, list[int]] = {}
        for raw, label in zip(train[feature].tolist(), y_train.tolist()):
            grouped.setdefault(_bucket(feature, raw), []).append(int(label))
        mapped: dict[str, dict[str, Any]] = {}
        for key, labels in grouped.items():
            n = len(labels)
            wins = sum(labels)
            probability = (
                wins + base * settings.prior_strength
            ) / (n + settings.prior_strength)
            mapped[key] = {
                "samples": n,
                "wins": wins,
                "probability": round(float(probability), 6),
            }
        feature_maps[feature] = mapped

    target_payload = {
        "label": label_col,
        "train_samples": len(train),
        "train_positives": positives,
        "global_probability": round(base, 6),
        "features": feature_maps,
    }
    probabilities = []
    for _, row in validation.iterrows():
        model = target_payload
        weighted = base
        weight_total = 1.0
        for feature, mapping in model["features"].items():
            record = mapping.get(_bucket(feature, row.get(feature)))
            if record:
                weight = min(2.0, math.log1p(float(record["samples"])) / 2.0)
                weighted += float(record["probability"]) * weight
                weight_total += weight
        probabilities.append(min(0.995, max(0.005, weighted / weight_total)))

    pred = np.asarray(probabilities, dtype=float)
    y = y_val.to_numpy(dtype=float)
    baseline = np.full(len(y), base, dtype=float)
    metrics = {
        "validation_samples": len(validation),
        "validation_positives": int(y_val.sum()),
        "brier_score": round(_brier(y, pred), 6),
        "baseline_brier_score": round(_brier(y, baseline), 6),
    }
    metrics["brier_improvement"] = round(
        metrics["baseline_brier_score"] - metrics["brier_score"], 6
    )
    return target_payload, metrics


def fit_model(outcomes: pd.DataFrame, settings: FitSettings | None = None) -> tuple[CalibratedRankingModel, pd.DataFrame]:
    settings = settings or FitSettings()
    if outcomes is None or outcomes.empty:
        payload = {
            "schema_version": MODEL_SCHEMA,
            "model_version": "untrained",
            "promotion_status": "INSUFFICIENT_DATA",
            "reason": "no outcomes",
            "targets": {},
        }
        return CalibratedRankingModel(payload), pd.DataFrame()

    frame = outcomes.copy()
    frame["_event_time"] = _event_time(frame)
    frame = frame[frame["_event_time"].notna()].sort_values("_event_time").reset_index(drop=True)
    required_targets = [column for column in TARGETS.values() if column in frame.columns]
    if len(frame) < settings.min_total_samples or len(required_targets) < len(TARGETS):
        payload = {
            "schema_version": MODEL_SCHEMA,
            "model_version": "untrained",
            "promotion_status": "INSUFFICIENT_DATA",
            "reason": f"need >= {settings.min_total_samples} resolved samples and all target labels",
            "samples": len(frame),
            "targets": {},
        }
        return CalibratedRankingModel(payload), pd.DataFrame()

    split = max(
        settings.min_validation_samples,
        int(math.ceil(len(frame) * settings.validation_fraction)),
    )
    split = min(split, len(frame) - 1)
    train, validation = frame.iloc[:-split].copy(), frame.iloc[-split:].copy()

    target_payloads: dict[str, Any] = {}
    metrics_rows: list[dict[str, Any]] = []
    target_passes = []
    for target, label in TARGETS.items():
        fitted, metrics = _fit_target(train, validation, label, settings)
        target_payloads[target] = fitted
        passes = (
            fitted["train_positives"] >= settings.min_positive_samples
            and metrics["validation_samples"] >= settings.min_validation_samples
            and metrics["brier_score"]
            <= metrics["baseline_brier_score"] + settings.max_brier_degradation
        )
        target_passes.append(passes)
        metrics_rows.append({
            "target": target,
            **metrics,
            "train_samples": fitted["train_samples"],
            "train_positives": fitted["train_positives"],
            "validation_gate": "PASS" if passes else "FAIL",
        })

    core = {
        "schema_version": MODEL_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "train_start_utc": train["_event_time"].iloc[0].isoformat(),
        "train_end_utc": train["_event_time"].iloc[-1].isoformat(),
        "validation_start_utc": validation["_event_time"].iloc[0].isoformat(),
        "validation_end_utc": validation["_event_time"].iloc[-1].isoformat(),
        "samples": len(frame),
        "train_samples": len(train),
        "validation_samples": len(validation),
        "targets": target_payloads,
        "settings": settings.__dict__,
    }
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str)
    version = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    core["model_version"] = f"v4.5-{version}"
    core["promotion_status"] = "VALIDATED_SHADOW" if all(target_passes) else "SHADOW_ONLY"
    core["promotion_reason"] = (
        "all target calibration gates passed"
        if all(target_passes)
        else "one or more target calibration gates failed"
    )
    return CalibratedRankingModel(core), pd.DataFrame(metrics_rows)


def save_model(model: CalibratedRankingModel, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.payload, indent=2, sort_keys=True, allow_nan=False))


def load_model(path: Path) -> CalibratedRankingModel:
    return CalibratedRankingModel(json.loads(Path(path).read_text()))
