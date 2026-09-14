from datetime import datetime, timedelta, timezone

import pandas as pd

from scanner.v4.contracts import EventType, MarketEvent
from scanner.v4.engine import MomentumEngine
from scanner.v4.outcomes import SignalOutcomeLedger, classify_failure
from scanner.v4.replay import verify_deterministic_replay
from scanner.v4.store import FileEventStore


def payload(**overrides):
    value = {
        "ticker": "AXTI",
        "stage": "ARMED",
        "live_status": "LIVE",
        "live_session_date": "2026-09-14",
        "live_price": 10.5,
        "live_bar_high": 10.6,
        "live_bar_low": 10.2,
        "entry_trigger": 10.0,
        "stop": 8.0,
        "effective_target": 20.0,
        "negative_catalyst_risk": False,
        "live_trigger_reached": True,
        "live_above_vwap": True,
        "intraday_rvol": 2.0,
        "live_trade_action": "BUY / LIVE CONFIRMED + CATALYST",
        "market_hunt_score": 91.0,
    }
    value.update(overrides)
    return value


def event(at, **overrides):
    return MarketEvent(
        event_type=EventType.CANDIDATE_SNAPSHOT,
        ticker="AXTI",
        observed_at_utc=at.isoformat(),
        payload=payload(**overrides),
    )


def test_ledger_uses_close_only_on_ambiguous_entry_bar_and_captures_30m(tmp_path):
    engine = MomentumEngine(FileEventStore(tmp_path / "events"))
    ledger = SignalOutcomeLedger(tmp_path / "outcomes.json", tmp_path / "outcomes.csv")
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    first = event(start, live_price=10.5, live_bar_high=14.0, live_bar_low=7.0)
    first_transition = engine.process(first)
    ledger.observe_many([(first, first_transition)])

    second = event(
        start + timedelta(minutes=31),
        live_price=11.0,
        live_bar_high=11.2,
        live_bar_low=10.4,
    )
    ledger.observe_many([(second, engine.process(second))])
    record = next(iter(ledger._load().values()))
    assert record["assumed_entry_price"] == 10.01
    assert record["max_price_after_entry"] == 11.2
    assert record["min_price_after_entry"] == 10.4
    assert record["return_30m_pct"] == round((11.0 / 10.01 - 1) * 100, 3)


def test_daily_horizons_use_only_first_five_future_sessions(tmp_path):
    ledger = SignalOutcomeLedger(tmp_path / "outcomes.json")
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    first = event(start)
    engine = MomentumEngine(FileEventStore(tmp_path / "events"))
    ledger.observe_many([(first, engine.process(first))])

    dates = pd.date_range("2026-09-15", periods=6, freq="B")
    history = pd.DataFrame({
        "Open": [11, 12, 13, 14, 15, 100],
        "High": [11.5, 12.5, 13.5, 14.5, 15.5, 200],
        "Low": [10.5, 11.5, 12.5, 13.5, 14.5, 1],
        "Close": [11, 12, 13, 14, 15, 100],
        "Volume": [1_000_000] * 6,
    }, index=dates)
    ledger.resolve_daily_histories({"AXTI": history})
    record = next(iter(ledger._load().values()))
    assert record["return_1d_pct"] == round((11 / 10.01 - 1) * 100, 3)
    assert record["return_5d_pct"] == round((15 / 10.01 - 1) * 100, 3)
    assert record["max_price_after_entry"] == 15.5
    assert record["min_price_after_entry"] == 10.5
    assert record["forward_5d_mfe_pct"] == round((15.5 / 10.01 - 1) * 100, 3)
    assert record["forward_5d_mae_pct"] == round((10.5 / 10.01 - 1) * 100, 3)
    assert record["daily_bars_resolved"] == 6


def test_daily_bar_uses_conservative_stop_first_when_target_and_stop_both_touch(tmp_path):
    ledger = SignalOutcomeLedger(tmp_path / "outcomes.json")
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    first = event(start, stop=9.0, effective_target=11.0)
    engine = MomentumEngine(FileEventStore(tmp_path / "events"))
    ledger.observe_many([(first, engine.process(first))])
    history = pd.DataFrame(
        {"High": [12.0], "Low": [8.0], "Close": [10.0]},
        index=pd.to_datetime(["2026-09-15"]),
    )
    ledger.resolve_daily_histories({"AXTI": history})
    record = next(iter(ledger._load().values()))
    assert record["outcome"] == "INVALIDATED"
    assert record["failure_reason"] == "STOP_AMBIGUOUS_SAME_BAR"
    assert record["current_state"] == "INVALIDATED"
    assert record["max_price_after_entry"] == 10.5
    assert record["min_price_after_entry"] == 9.0
    assert record["r_multiple"] is not None


def test_post_terminal_snapshot_cannot_improve_trade_path_but_can_resolve_30m(tmp_path):
    ledger = SignalOutcomeLedger(tmp_path / "outcomes.json")
    engine = MomentumEngine(FileEventStore(tmp_path / "events"))
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    first = event(start, live_price=10.5)
    ledger.observe_many([(first, engine.process(first))])

    stopped = event(
        start + timedelta(minutes=5),
        live_price=7.9,
        live_bar_high=10.6,
        live_bar_low=7.8,
    )
    ledger.observe_many([(stopped, engine.process(stopped))])
    closed = next(iter(ledger.load_records().values()))
    closed_mfe = closed["mfe_pct"]
    closed_samples = closed["samples_after_entry"]

    after_exit = event(
        start + timedelta(minutes=31),
        live_price=15.0,
        live_bar_high=16.0,
        live_bar_low=7.0,
    )
    ledger.observe_many([(after_exit, engine.process(after_exit))])
    record = next(iter(ledger.load_records().values()))
    assert record["mfe_pct"] == closed_mfe
    assert record["samples_after_entry"] == closed_samples
    assert record["return_30m_pct"] == round((15.0 / 10.01 - 1) * 100, 3)


def test_failure_taxonomy_is_explainable():
    assert classify_failure(payload(live_above_vwap=False), "FAILED_BREAKOUT") == "LOST_VWAP"
    assert classify_failure(payload(intraday_rvol=0.5), "FAILED_BREAKOUT") == "VOLUME_COLLAPSE"
    assert classify_failure(payload(negative_catalyst_risk=True), "INVALIDATED") == "NEGATIVE_CATALYST"


def test_event_replay_is_deterministic(tmp_path):
    store = FileEventStore(tmp_path / "source")
    start = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)
    store.append_events([
        event(start, live_trade_action="WAIT / LIVE CONFIRMATION"),
        event(start + timedelta(minutes=5)),
    ])
    report = verify_deterministic_replay(store.event_file)
    assert report["events_replayed"] == 2
    assert report["deterministic"] is True
    assert report["first_checksum"] == report["second_checksum"]
