import pandas as pd

from scanner.v4.contracts import CandidateState, EventType, MarketEvent, build_signal_id
from scanner.v4.engine import MomentumEngine
from scanner.v4.shortlist import build_monitor_shortlist
from scanner.v4.state_machine import next_state
from scanner.v4.store import PostgresEventStore


def snapshot(**overrides):
    value = {
        "ticker": "AXTI",
        "stage": "ARMED",
        "live_status": "LIVE",
        "live_price": 10.5,
        "entry_trigger": 10.0,
        "stop": 9.5,
        "effective_target": 11.5,
        "negative_catalyst_risk": False,
        "live_trigger_reached": True,
        "live_above_vwap": True,
        "intraday_rvol": 2.0,
        "live_trade_action": "BUY / LIVE CONFIRMED + CATALYST",
    }
    value.update(overrides)
    return value


def test_signal_id_changes_when_trade_plan_changes():
    assert build_signal_id("axti", 10, 9.5) == "AXTI|10.0000|9.5000"
    assert build_signal_id("AXTI", 10.1, 9.5) != build_signal_id("AXTI", 10, 9.5)


def test_state_machine_confirms_and_keeps_terminal_state():
    assert next_state(CandidateState.ARMED, snapshot()) is CandidateState.LIVE_CONFIRMED
    hit = next_state(CandidateState.LIVE_CONFIRMED, snapshot(live_price=12.0))
    assert hit is CandidateState.TARGET_HIT
    assert next_state(hit, snapshot(live_price=10.0)) is CandidateState.TARGET_HIT


def test_negative_catalyst_vetoes_live_setup():
    state = next_state(None, snapshot(negative_catalyst_risk=True))
    assert state is CandidateState.INVALIDATED


def test_engine_persists_versioned_state_and_deduplicates_events(tmp_path):
    store = PostgresEventStore(str(tmp_path))
    engine = MomentumEngine(store)
    event = MarketEvent(
        event_type=EventType.CANDIDATE_SNAPSHOT,
        ticker="AXTI",
        payload=snapshot(),
        observed_at_utc="2026-09-13T14:00:00+00:00",
    )
    transition = engine.process(event)
    assert transition.current_state == "LIVE_CONFIRMED"
    assert engine.process(event) is None

    assert len(store.load_events()) == 2  # one input event plus one transition event
    assert store.load_states()[event.signal_id]["state"] == "LIVE_CONFIRMED"


def test_shortlist_requires_explosive_gate_and_assigns_tiers():
    frame = pd.DataFrame([
        {"ticker": "AXTI", "universal_10pct_gate": True, "stage": "ARMED", "market_hunt_score": 90},
        {"ticker": "CRDO", "universal_10pct_gate": True, "stage": "FORMING", "market_hunt_score": 80},
        {"ticker": "SLOW", "universal_10pct_gate": False, "stage": "CONFIRMED", "market_hunt_score": 99},
    ])
    result = build_monitor_shortlist(frame, hot_limit=1, warm_limit=1)
    assert result["ticker"].tolist() == ["AXTI", "CRDO"]
    assert result["monitor_tier"].tolist() == ["HOT", "WARM"]
