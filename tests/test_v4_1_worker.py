from datetime import datetime, timezone
import json

import pandas as pd

from scanner.v4.adapters import PollResult, YahooPollingAdapter
from scanner.v4.alerting import AlertRouter
from scanner.v4.engine import MomentumEngine, Transition
from scanner.v4.health import CycleMetric, HealthRecorder
from scanner.v4.source import CandidateSourceResult, FallbackCandidateSource
from scanner.v4.store import FileEventStore
from scanner.v4.worker import ContinuousMomentumWorker, WorkerSettings, select_poll_batch


def candidates():
    return pd.DataFrame([
        {
            "ticker": ticker,
            "universal_10pct_gate": True,
            "stage": "ARMED" if index < 2 else "FORMING",
            "market_hunt_score": 90 - index,
            "entry_trigger": 10.0 + index,
            "stop": 9.5 + index,
            "effective_target": 11.5 + index,
        }
        for index, ticker in enumerate(["AXTI", "CRDO", "INTC", "AMD", "IOVA"])
    ])


class StaticSource:
    def load(self):
        return CandidateSourceResult(candidates(), "test-source", "2026-09-13T10:00:00+00:00")


class BrokenSource:
    def load(self):
        raise RuntimeError("unavailable")


class StaticAdapter:
    def poll(self, frame):
        output = frame.copy()
        output["live_status"] = "LIVE"
        output["live_price"] = output["entry_trigger"] + 0.5
        output["live_bar_at_et"] = datetime.now(timezone.utc).isoformat()
        output["negative_catalyst_risk"] = False
        output["live_trigger_reached"] = True
        output["live_above_vwap"] = True
        output["intraday_rvol"] = 2.2
        output["live_trade_action"] = "BUY / LIVE CONFIRMED + CATALYST"
        now = datetime.now(timezone.utc).isoformat()
        return PollResult(output, "test", now, now, 20, len(output), len(output), 0)


class RecordingSink:
    name = "recording"

    def __init__(self):
        self.alerts = []

    def send(self, alert):
        self.alerts.append(alert)


def test_poll_batch_checks_hot_every_cycle_and_rotates_warm():
    frame = candidates().copy()
    frame["monitor_tier"] = ["HOT", "HOT", "WARM", "WARM", "WARM"]
    settings = WorkerSettings(hot_limit=2, warm_limit=3, warm_batch_size=1, warm_every_cycles=2)
    assert select_poll_batch(frame, 1, settings)["ticker"].tolist() == ["AXTI", "CRDO"]
    assert select_poll_batch(frame, 2, settings)["ticker"].tolist() == ["AXTI", "CRDO", "AMD"]
    assert select_poll_batch(frame, 4, settings)["ticker"].tolist() == ["AXTI", "CRDO", "IOVA"]


def test_source_fallback_marks_degraded():
    result = FallbackCandidateSource(BrokenSource(), StaticSource()).load()
    assert result.degraded is True
    assert result.source_name == "test-source"
    assert "RuntimeError" in result.detail


def test_yahoo_adapter_counts_empty_responses_as_provider_errors(monkeypatch):
    monkeypatch.setattr(
        YahooPollingAdapter,
        "_analyze",
        staticmethod(lambda _row: {"live_status": "NO INTRADAY DATA"}),
    )
    result = YahooPollingAdapter(max_workers=2).poll(candidates().head(2))
    assert result.requested == 2
    assert result.received == 0
    assert result.errors == 2


def test_alert_router_deduplicates_per_sink(tmp_path):
    sink = RecordingSink()
    router = AlertRouter(tmp_path / "dispatch.json", [sink])
    transition = Transition(
        signal_id="AXTI|10.0000|9.5000",
        ticker="AXTI",
        previous_state="ARMED",
        current_state="LIVE_CONFIRMED",
        event_id="event-1",
        observed_at_utc="2026-09-13T14:00:00+00:00",
        live_price=10.5,
        entry_trigger=10.0,
        stop=9.5,
        effective_target=11.5,
        intraday_rvol=2.2,
        market_hunt_score=91.0,
    )
    assert router.route([transition]) == (1, [])
    assert router.route([transition]) == (0, [])
    assert len(sink.alerts) == 1
    assert "RVOL 2.2" in sink.alerts[0].message()


def test_health_recorder_calculates_market_hours_uptime(tmp_path):
    recorder = HealthRecorder(tmp_path / "cycles.csv", tmp_path / "summary.json")
    base = dict(
        cycle_started_at_utc="2026-09-13T14:00:00+00:00",
        cycle_completed_at_utc="2026-09-13T14:00:01+00:00",
        market_open=True,
        source_name="test",
        source_degraded=False,
        source_age_seconds=10.0,
        candidates=10,
        polled=5,
        received=5,
        provider_coverage_pct=100.0,
        provider_errors=0,
        provider_duration_ms=100,
        event_lag_p95_ms=1000.0,
        transitions=1,
        alert_deliveries=1,
        alert_failures=0,
    )
    recorder.record(CycleMetric(success=True, **base))
    summary = recorder.record(CycleMetric(success=False, **base))
    assert summary["market_hours_uptime_pct"] == 50.0
    assert summary["status"] == "FAILED"


def test_worker_cycle_writes_health_state_and_deduplicated_alerts(tmp_path):
    sink = RecordingSink()
    state = tmp_path / "state"
    output = tmp_path / "output"
    worker = ContinuousMomentumWorker(
        source=StaticSource(),
        adapter=StaticAdapter(),
        engine=MomentumEngine(FileEventStore(state)),
        alerts=AlertRouter(state / "dispatch.json", [sink]),
        health=HealthRecorder(output / "cycles.csv", output / "health.json"),
        settings=WorkerSettings(hot_limit=2, warm_limit=2, warm_batch_size=0),
        output_dir=output,
    )
    first = worker.run_cycle()
    second = worker.run_cycle()
    assert first.success and second.success
    assert first.transitions == 2
    assert second.transitions == 0
    assert len(sink.alerts) == 2
    assert (output / "v4_live_snapshot.csv").exists()
    assert json.loads((output / "health.json").read_text())["cycles_recorded"] == 2


def test_stop_request_prevents_new_worker_cycle(tmp_path):
    sink = RecordingSink()
    worker = ContinuousMomentumWorker(
        source=StaticSource(),
        adapter=StaticAdapter(),
        engine=MomentumEngine(FileEventStore(tmp_path / "state")),
        alerts=AlertRouter(tmp_path / "dispatch.json", [sink]),
        health=HealthRecorder(tmp_path / "cycles.csv", tmp_path / "health.json"),
        output_dir=tmp_path / "output",
    )
    worker.request_stop()
    assert worker.run_forever(max_cycles=1) == 0


def test_worker_uses_bounded_exponential_backoff_after_failures(tmp_path):
    sink = RecordingSink()
    worker = ContinuousMomentumWorker(
        source=StaticSource(),
        adapter=StaticAdapter(),
        engine=MomentumEngine(FileEventStore(tmp_path / "state")),
        alerts=AlertRouter(tmp_path / "dispatch.json", [sink]),
        health=HealthRecorder(tmp_path / "cycles.csv", tmp_path / "health.json"),
        settings=WorkerSettings(
            market_interval_seconds=60,
            failure_backoff_initial_seconds=300,
            failure_backoff_max_seconds=600,
        ),
        output_dir=tmp_path / "output",
    )
    base = dict(
        cycle_started_at_utc="2026-09-13T14:00:00+00:00",
        cycle_completed_at_utc="2026-09-13T14:00:01+00:00",
        market_open=True,
        source_name="test",
        source_degraded=False,
        source_age_seconds=1.0,
        candidates=2,
        polled=2,
        received=0,
        provider_coverage_pct=0.0,
        provider_errors=2,
        provider_duration_ms=10,
        event_lag_p95_ms=None,
        transitions=0,
        alert_deliveries=0,
        alert_failures=0,
    )
    failed = CycleMetric(success=False, **base)
    healthy = CycleMetric(success=True, **base)
    assert worker.next_interval(failed) == 300
    assert worker.next_interval(failed) == 600
    assert worker.next_interval(failed) == 600
    assert worker.next_interval(healthy) == 60
