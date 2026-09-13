from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CycleMetric:
    cycle_started_at_utc: str
    cycle_completed_at_utc: str
    success: bool
    market_open: bool
    source_name: str
    source_degraded: bool
    source_age_seconds: float | None
    candidates: int
    polled: int
    received: int
    provider_coverage_pct: float | None
    provider_errors: int
    provider_duration_ms: int
    event_lag_p95_ms: float | None
    transitions: int
    alert_deliveries: int
    alert_failures: int
    detail: str = ""


def parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def age_seconds(value: str, now: datetime | None = None) -> float | None:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - parsed).total_seconds())


class HealthRecorder:
    def __init__(self, cycles_file: Path, summary_file: Path, max_cycles: int = 2000):
        self.cycles_file = Path(cycles_file)
        self.summary_file = Path(summary_file)
        self.max_cycles = max_cycles

    def record(self, metric: CycleMetric) -> dict:
        self.cycles_file.parent.mkdir(parents=True, exist_ok=True)
        if self.cycles_file.exists():
            try:
                frame = pd.read_csv(self.cycles_file)
            except Exception:
                frame = pd.DataFrame()
        else:
            frame = pd.DataFrame()
        frame = pd.concat([frame, pd.DataFrame([asdict(metric)])], ignore_index=True).tail(self.max_cycles)
        frame.to_csv(self.cycles_file, index=False)

        market = frame[frame["market_open"].fillna(False).astype(bool)]
        uptime = float(market["success"].fillna(False).astype(bool).mean() * 100) if len(market) else None
        lag = pd.to_numeric(frame["event_lag_p95_ms"], errors="coerce").dropna()
        durations = pd.to_numeric(frame["provider_duration_ms"], errors="coerce").dropna()
        latest = frame.iloc[-1]
        if not bool(latest["success"]):
            status = "FAILED"
        elif bool(latest["source_degraded"]) or int(latest["provider_errors"]) > 0:
            status = "DEGRADED"
        elif int(latest["candidates"]) == 0:
            status = "NO_CANDIDATES"
        else:
            status = "HEALTHY"
        summary = {
            "status": status,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "cycles_recorded": len(frame),
            "market_open_cycles": len(market),
            "market_hours_uptime_pct": round(uptime, 2) if uptime is not None else None,
            "event_lag_p95_ms": round(float(np.percentile(lag, 95)), 1) if len(lag) else None,
            "provider_duration_p95_ms": round(float(np.percentile(durations, 95)), 1) if len(durations) else None,
            "last_cycle": {
                key: (None if isinstance(value, float) and not math.isfinite(value) else value)
                for key, value in asdict(metric).items()
            },
        }
        self.summary_file.write_text(json.dumps(summary, indent=2, allow_nan=False))
        return summary
