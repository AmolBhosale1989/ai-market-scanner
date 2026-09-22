from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any

import pandas as pd

from ..control_plane import read_dataset


EVIDENCE_SCHEMA = "7.1.0"
LEDGERS = {
    "v4_shadow_observations": ("DAILY_SNAPSHOT", "observation_id"),
    "v4_outcomes": ("INTRADAY_TRIGGER", "signal_id"),
}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return not (isinstance(value, str) and not value.strip())


def _resolution(record: dict[str, Any]) -> int:
    try:
        value = float(record.get("daily_bars_resolved", 0) or 0)
        return int(value) if math.isfinite(value) else 0
    except (TypeError, ValueError):
        return 0


def _merge_record(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    first_resolution = _resolution(first)
    second_resolution = _resolution(second)
    primary, fallback = (second, first) if second_resolution >= first_resolution else (first, second)
    merged = dict(fallback)
    for key, value in primary.items():
        if _present(value) or key not in merged:
            merged[key] = value
    # Capture time defines the point-in-time cohort. Resolution updates may add
    # forward labels, but they must never move a record into a later snapshot.
    capture_times = [
        str(record.get("observed_at_utc", "")).strip()
        for record in (first, second)
        if _present(record.get("observed_at_utc"))
    ]
    if capture_times:
        merged["observed_at_utc"] = min(capture_times)
    return merged


def _freeze_earliest_daily_cohorts(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Discard candidates appended by a later rerun of an existing session."""
    earliest_by_session: dict[str, str] = {}
    for record in records.values():
        session = str(record.get("as_of_session", "")).strip()
        captured_at = str(record.get("observed_at_utc", "")).strip()
        if not session or not captured_at:
            continue
        earliest_by_session[session] = min(earliest_by_session.get(session, captured_at), captured_at)

    frozen: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        session = str(record.get("as_of_session", "")).strip()
        captured_at = str(record.get("observed_at_utc", "")).strip()
        earliest = earliest_by_session.get(session)
        if earliest and captured_at and captured_at != earliest:
            continue
        frozen[key] = record
    return frozen


def training_outcomes() -> pd.DataFrame:
    """Return only forward-mature, point-in-time observations for model fitting."""
    rows: list[dict[str, Any]] = []
    for dataset_name, (source, id_column) in LEDGERS.items():
        frame = read_dataset(dataset_name, required=False)
        for record in frame.to_dict(orient="records"):
            key = str(record.get(id_column, ""))
            resolution = _resolution(record)
            labels = [record.get(f"forward_hit_{threshold}pct") for threshold in (5, 10, 15)]
            if resolution < 5 or not all(_present(value) for value in labels):
                continue
            item = dict(record)
            item["evidence_id"] = f"{source}|{key}"
            item["evidence_source"] = source
            item["evidence_session"] = (
                item.get("as_of_session")
                or item.get("entry_session")
                or str(item.get("entered_at_utc", ""))[:10]
            )
            item["entered_at_utc"] = (
                item.get("entered_at_utc")
                or item.get("observed_at_utc")
                or f"{item.get('as_of_session', '')}T20:00:00+00:00"
            )
            rows.append(item)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).drop_duplicates("evidence_id", keep="last")
    frame["_source_priority"] = frame["evidence_source"].map({"DAILY_SNAPSHOT": 0, "INTRADAY_TRIGGER": 1}).fillna(2)
    frame = frame.sort_values(["_source_priority", "entered_at_utc"])
    frame = frame.drop_duplicates(["evidence_session", "ticker"], keep="first").drop(columns="_source_priority")
    return frame.sort_values("entered_at_utc").reset_index(drop=True)


@dataclass(frozen=True)
class EvidenceHealthSettings:
    maturity_sessions: int = 5
    minimum_daily_candidates: int = 10
    stale_after_calendar_days: int = 4
    resolution_stalled_after_days: int = 15


def evaluate_evidence_health(
    observations: pd.DataFrame,
    now: datetime | None = None,
    settings: EvidenceHealthSettings | None = None,
) -> dict[str, Any]:
    settings = settings or EvidenceHealthSettings()
    now = now or datetime.now(timezone.utc)
    frame = observations.copy() if observations is not None else pd.DataFrame()
    health: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA,
        "generated_at_utc": now.isoformat(),
        "status": "COLLECTING",
        "failed_checks": [],
        "settings": asdict(settings),
        "observations": len(frame),
        "observation_days": 0,
        "mature_training_samples": 0,
        "milestones": {"monitoring_30": False, "v5_100": False, "v6_220": False},
        "block_model_promotion": True,
    }
    failed: list[str] = []
    if frame.empty or "as_of_session" not in frame:
        health.update({"status": "DEGRADED", "detail": "no point-in-time evidence snapshots"})
        health["failed_checks"] = ["NO_SNAPSHOTS"]
        return health

    sessions = pd.to_datetime(frame["as_of_session"], errors="coerce", utc=True)
    valid_sessions = sessions.dropna()
    health["observation_days"] = int(valid_sessions.dt.date.nunique())
    if valid_sessions.empty:
        failed.append("INVALID_SESSION_TIMESTAMPS")
    else:
        latest = valid_sessions.max()
        age_days = max(0, (now.date() - latest.date()).days)
        health["latest_snapshot_session"] = latest.date().isoformat()
        health["snapshot_age_calendar_days"] = age_days
        if age_days > settings.stale_after_calendar_days:
            failed.append("SNAPSHOT_STALE")

    daily_counts = frame.assign(_session=sessions.dt.date).groupby("_session").size()
    health["latest_snapshot_candidates"] = int(daily_counts.iloc[-1]) if len(daily_counts) else 0
    if len(daily_counts) and daily_counts.iloc[-1] < settings.minimum_daily_candidates:
        failed.append("DAILY_CAPTURE_INCOMPLETE")

    resolved = pd.to_numeric(frame.get("daily_bars_resolved"), errors="coerce").fillna(0)
    mature = resolved.ge(settings.maturity_sessions)
    mature_count = int(mature.sum())
    health["mature_training_samples"] = mature_count
    health["unresolved_observations"] = int((~mature).sum())
    health["milestones"] = {
        "monitoring_30": mature_count >= 30,
        "v5_100": mature_count >= 100,
        "v6_220": mature_count >= 220,
    }
    if valid_sessions.notna().any():
        ages = (pd.Timestamp(now).normalize() - sessions.dt.normalize()).dt.days
        stalled = (~mature) & ages.gt(settings.resolution_stalled_after_days)
        health["resolution_stalled_observations"] = int(stalled.sum())
        if stalled.any():
            failed.append("FORWARD_RESOLUTION_STALLED")

    health["failed_checks"] = sorted(set(failed))
    health["status"] = "DEGRADED" if failed else ("HEALTHY" if mature_count >= 30 else "COLLECTING")
    health["block_model_promotion"] = health["status"] != "HEALTHY"
    health["detail"] = (
        "evidence pipeline requires attention" if failed
        else "forward evidence is accumulating normally" if mature_count < 30
        else "evidence collection is healthy"
    )
    return health
