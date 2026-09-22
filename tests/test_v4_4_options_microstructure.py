from datetime import datetime, timezone

import pandas as pd

from scanner.v4.adapters import PollResult
from scanner.v4.alerting import AlertRouter
from scanner.v4.contracts import EventType
from scanner.v4.engine import MomentumEngine
from scanner.v4.health import HealthRecorder
from scanner.v4.options_microstructure import YahooOptionsMicrostructureAdapter
from scanner.v4.source import CandidateSourceResult
from scanner.v4.store import PostgresEventStore
from scanner.v4.worker import ContinuousMomentumWorker, WorkerSettings
from scanner.v4_worker import build_worker, parser


def sample_candidates():
    return pd.DataFrame([
        {
            "ticker": "AXTI",
            "universal_10pct_gate": True,
            "stage": "ARMED",
            "market_hunt_score": 90,
            "entry_trigger": 10.0,
            "stop": 9.5,
            "effective_target": 11.5,
        },
        {
            "ticker": "CRDO",
            "universal_10pct_gate": True,
            "stage": "ARMED",
            "market_hunt_score": 88,
            "entry_trigger": 20.0,
            "stop": 19.0,
            "effective_target": 23.0,
        },
    ])


class StaticSource:
    def load(self):
        return CandidateSourceResult(
            sample_candidates(), "test-source", datetime.now(timezone.utc).isoformat()
        )


class StaticLiveAdapter:
    def poll(self, frame):
        output = frame.copy()
        output["live_status"] = "LIVE"
        output["live_price"] = output["entry_trigger"] + 0.25
        output["live_bar_at_et"] = datetime.now(timezone.utc).isoformat()
        output["negative_catalyst_risk"] = False
        output["live_trigger_reached"] = True
        output["live_above_vwap"] = True
        output["intraday_rvol"] = 2.0
        output["live_trade_action"] = "BUY / LIVE CONFIRMED"
        now = datetime.now(timezone.utc).isoformat()
        return PollResult(output, "test", now, now, 10, len(output), len(output), 0)


class BrokenEvidenceAdapter:
    def poll(self, _frame):
        raise RuntimeError("provider unavailable")


def test_v44_adapter_emits_provider_neutral_events(monkeypatch):
    adapter = YahooOptionsMicrostructureAdapter(max_workers=2, max_tickers=2)

    def fake_fetch(symbol):
        return {
            "ticker": symbol,
            "options_status": "OK",
            "option_expiry": "2026-09-18",
            "call_volume": 1000,
            "put_volume": 500,
            "call_put_volume_ratio": 2.0,
            "call_open_interest": 3000,
            "put_open_interest": 2500,
            "unusual_call_contracts": 3,
            "unusual_put_contracts": 1,
            "call_implied_volatility": 0.7,
            "put_implied_volatility": 0.8,
            "microstructure_status": "OK",
            "bar_count": 100,
            "volume_acceleration_5m": 2.4,
            "price_pressure_5m_pct": 1.2,
            "last_bar_close_location": 0.9,
            "last_bar_dollar_volume": 250000,
            "options_error": "",
            "microstructure_error": "",
            "provider_duration_ms": 5,
        }

    monkeypatch.setattr(adapter, "_fetch_ticker", fake_fetch)
    result = adapter.poll(sample_candidates())

    assert result.health["status"] == "OK"
    assert result.health["requested"] == 2
    assert result.health["options_available"] == 2
    assert result.health["microstructure_available"] == 2
    assert result.health["execution_grade"] is False
    assert len(result.events) == 4
    assert {event.event_type for event in result.events} == {
        EventType.OPTIONS_FLOW,
        EventType.MICROSTRUCTURE,
    }
    assert set(result.frame["ticker"]) == {"AXTI", "CRDO"}


def test_v44_empty_input_is_explicit_nonfatal_skip():
    result = YahooOptionsMicrostructureAdapter(max_tickers=2).poll(pd.DataFrame())
    assert result.health["status"] == "SKIPPED"
    assert result.health["requested"] == 0
    assert result.events == []


def test_v44_microstructure_uses_postgres_ohlcv(monkeypatch):
    adapter = YahooOptionsMicrostructureAdapter(max_tickers=1)
    bars = pd.DataFrame({
        "High": [10.0, 10.2, 10.4, 10.6, 10.8, 11.0],
        "Low": [9.8, 10.0, 10.2, 10.4, 10.6, 10.8],
        "Close": [9.9, 10.1, 10.3, 10.5, 10.7, 10.9],
        "Volume": [100, 100, 100, 200, 300, 400],
    })
    calls = []
    monkeypatch.setattr(
        "scanner.v4.options_microstructure.warehouse_history",
        lambda symbol, **kwargs: calls.append((symbol, kwargs)) or bars,
    )
    monkeypatch.setattr(adapter, "_options_snapshot", lambda ticker: {
        "options_status": "NO_CHAIN", "option_expiry": "", "call_volume": None,
        "put_volume": None, "call_put_volume_ratio": None, "call_open_interest": None,
        "put_open_interest": None, "unusual_call_contracts": 0,
        "unusual_put_contracts": 0, "call_implied_volatility": None,
        "put_implied_volatility": None,
    })

    result = adapter._fetch_ticker("AXTI")

    assert calls == [("AXTI", {"period": "1d", "interval": "5m", "max_age_minutes": 15})]
    assert result["microstructure_status"] == "OK"
    assert result["bar_count"] == 6


def test_v44_worker_provider_failure_does_not_break_live_cycle(tmp_path, memory_control_plane):
    output = tmp_path / "output"
    state = tmp_path / "state"
    worker = ContinuousMomentumWorker(
        source=StaticSource(),
        adapter=StaticLiveAdapter(),
        engine=MomentumEngine(PostgresEventStore(str(state))),
        alerts=AlertRouter([], namespace=str(state)),
        health=HealthRecorder(namespace=str(output)),
        settings=WorkerSettings(hot_limit=2, warm_limit=0, warm_batch_size=0),
        namespace=str(state),
        options_microstructure_adapter=BrokenEvidenceAdapter(),
    )

    metric = worker.run_cycle()
    health = memory_control_plane["datasets"][(memory_control_plane["run_id"], "v4_options_microstructure_health")].iloc[0].to_dict()

    assert metric.success is True
    assert health["status"] == "FAILED"
    assert health["missing_data_is_nonfatal"] is True
    assert health["execution_grade"] is False
    assert "v4.4_status=FAILED" in metric.detail


def test_v44_cli_is_opt_in_and_bounded(tmp_path):
    args = parser().parse_args([
        "--options-microstructure",
        "--options-limit", "3",
        "--options-workers", "2",
    ])
    worker = build_worker(args)
    assert worker.options_microstructure_adapter is not None
    assert worker.options_microstructure_adapter.max_tickers == 3
    assert worker.options_microstructure_adapter.max_workers == 2
