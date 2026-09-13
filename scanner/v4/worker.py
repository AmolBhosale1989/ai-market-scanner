from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import signal
import threading
import time

import numpy as np
import pandas as pd

from ..config import OUTPUT_DIR
from ..live import NY, _market_state
from .adapters import LiveMarketAdapter
from .alerting import AlertRouter
from .engine import MomentumEngine
from .health import CycleMetric, HealthRecorder, age_seconds, parse_utc
from .shortlist import build_monitor_shortlist
from .source import CandidateSource


@dataclass(frozen=True)
class WorkerSettings:
    hot_limit: int = 20
    warm_limit: int = 80
    warm_batch_size: int = 10
    warm_every_cycles: int = 5
    market_interval_seconds: int = 60
    off_hours_interval_seconds: int = 900
    max_source_age_seconds: int = 43_200
    min_provider_coverage: float = 0.80
    failure_backoff_initial_seconds: int = 300
    failure_backoff_max_seconds: int = 1800


def select_poll_batch(shortlist: pd.DataFrame, cycle_index: int, settings: WorkerSettings) -> pd.DataFrame:
    if shortlist is None or shortlist.empty:
        return pd.DataFrame()
    hot = shortlist[shortlist["monitor_tier"].eq("HOT")]
    if settings.warm_batch_size <= 0 or cycle_index % max(settings.warm_every_cycles, 1) != 0:
        return hot.reset_index(drop=True)
    warm = shortlist[shortlist["monitor_tier"].eq("WARM")].reset_index(drop=True)
    if warm.empty:
        return hot.reset_index(drop=True)
    batches = max(1, math.ceil(len(warm) / settings.warm_batch_size))
    batch_number = (cycle_index // max(settings.warm_every_cycles, 1)) % batches
    start = batch_number * settings.warm_batch_size
    selected = warm.iloc[start : start + settings.warm_batch_size]
    return pd.concat([hot, selected], ignore_index=True).drop_duplicates("ticker")


class ContinuousMomentumWorker:
    def __init__(
        self,
        source: CandidateSource,
        adapter: LiveMarketAdapter,
        engine: MomentumEngine,
        alerts: AlertRouter,
        health: HealthRecorder,
        settings: WorkerSettings | None = None,
        output_dir: Path = OUTPUT_DIR,
    ):
        self.source = source
        self.adapter = adapter
        self.engine = engine
        self.alerts = alerts
        self.health = health
        self.settings = settings or WorkerSettings()
        self.output_dir = Path(output_dir)
        self.stop_requested = threading.Event()
        self.cycle_index = 0
        self.consecutive_failures = 0

    def request_stop(self, *_args) -> None:
        self.stop_requested.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    @staticmethod
    def _event_time(row: dict, fallback: str) -> str:
        value = str(row.get("live_bar_at_et", "") or "")
        parsed = parse_utc(value)
        return parsed.isoformat() if parsed else fallback

    def run_cycle(self) -> CycleMetric:
        cycle_started = datetime.now(timezone.utc)
        source_name = "unknown"
        source_degraded = False
        source_age = None
        candidates = polled = received = provider_errors = provider_duration = 0
        transitions_count = alert_deliveries = 0
        alert_failures: list[str] = []
        detail = ""
        success = False
        event_lags = []
        market_open = _market_state(datetime.now(NY)) == "LIVE"

        try:
            source_result = self.source.load()
            source_name = source_result.source_name
            source_degraded = source_result.degraded
            source_age = age_seconds(source_result.source_timestamp_utc, cycle_started)
            if source_age is not None and source_age > self.settings.max_source_age_seconds:
                source_degraded = True
            detail = source_result.detail
            if source_degraded and source_age is not None and source_age > self.settings.max_source_age_seconds:
                detail = f"{detail}; stale scan source".strip("; ")
            shortlist = build_monitor_shortlist(
                source_result.frame,
                hot_limit=self.settings.hot_limit,
                warm_limit=self.settings.warm_limit,
            )
            candidates = len(shortlist)
            batch = select_poll_batch(shortlist, self.cycle_index, self.settings)
            poll = self.adapter.poll(batch)
            polled, received = poll.requested, poll.received
            provider_errors, provider_duration = poll.errors, poll.duration_ms

            events = []
            processed_at = datetime.now(timezone.utc)
            for row in poll.frame.to_dict(orient="records"):
                observed = self._event_time(row, poll.completed_at_utc)
                parsed = parse_utc(observed)
                if parsed and str(row.get("live_status", "")) == "LIVE":
                    event_lags.append(max(0.0, (processed_at - parsed).total_seconds() * 1000))
                events.append(self.engine.snapshot_event(row, observed))
            transitions = self.engine.process_many(events)
            transitions_count = len(transitions)
            alert_deliveries, alert_failures = self.alerts.route(transitions)

            self.output_dir.mkdir(parents=True, exist_ok=True)
            shortlist.to_csv(self.output_dir / "v4_monitor_shortlist.csv", index=False)
            poll.frame.to_csv(self.output_dir / "v4_live_snapshot.csv", index=False)
            pd.DataFrame([item.to_dict() for item in transitions]).to_csv(
                self.output_dir / "v4_transitions.csv", index=False
            )
            coverage = (received / polled) if polled else 1.0
            success = coverage >= self.settings.min_provider_coverage
        except Exception as exc:
            detail = f"{detail}; cycle failed: {type(exc).__name__}".strip("; ")

        completed = datetime.now(timezone.utc)
        lag_p95 = float(np.percentile(event_lags, 95)) if event_lags else None
        metric = CycleMetric(
            cycle_started_at_utc=cycle_started.isoformat(),
            cycle_completed_at_utc=completed.isoformat(),
            success=success,
            market_open=market_open,
            source_name=source_name,
            source_degraded=source_degraded,
            source_age_seconds=round(source_age, 1) if source_age is not None else None,
            candidates=candidates,
            polled=polled,
            received=received,
            provider_coverage_pct=round(received / polled * 100, 1) if polled else None,
            provider_errors=provider_errors,
            provider_duration_ms=provider_duration,
            event_lag_p95_ms=round(lag_p95, 1) if lag_p95 is not None else None,
            transitions=transitions_count,
            alert_deliveries=alert_deliveries,
            alert_failures=len(alert_failures),
            detail=detail or ",".join(alert_failures),
        )
        self.health.record(metric)
        self.cycle_index += 1
        return metric

    def next_interval(self, metric: CycleMetric) -> int:
        if metric.success:
            self.consecutive_failures = 0
            return (
                self.settings.market_interval_seconds
                if metric.market_open
                else self.settings.off_hours_interval_seconds
            )
        self.consecutive_failures += 1
        backoff = self.settings.failure_backoff_initial_seconds * (2 ** (self.consecutive_failures - 1))
        return min(backoff, self.settings.failure_backoff_max_seconds)

    def run_forever(self, max_cycles: int | None = None) -> int:
        self.install_signal_handlers()
        completed = 0
        while not self.stop_requested.is_set():
            metric = self.run_cycle()
            completed += 1
            print(
                f"V4.1 cycle={completed} success={metric.success} candidates={metric.candidates} "
                f"polled={metric.polled} transitions={metric.transitions} "
                f"provider_ms={metric.provider_duration_ms}"
            )
            if max_cycles is not None and completed >= max_cycles:
                break
            interval = self.next_interval(metric)
            self.stop_requested.wait(max(1, interval))
        return completed
