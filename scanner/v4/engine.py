from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping

from .contracts import CandidateState, EventType, MarketEvent
from .state_machine import next_state
from .store import PostgresEventStore


@dataclass(frozen=True)
class Transition:
    signal_id: str
    ticker: str
    previous_state: str
    current_state: str
    event_id: str
    observed_at_utc: str
    live_price: float | None = None
    entry_trigger: float | None = None
    stop: float | None = None
    effective_target: float | None = None
    intraday_rvol: float | None = None
    market_hunt_score: float | None = None

    def to_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


class MomentumEngine:
    """Processes normalized events without knowing the market-data provider."""

    def __init__(self, store: PostgresEventStore):
        self.store = store

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def process(self, event: MarketEvent) -> Transition | None:
        self.store.append_events([event])
        if event.event_type is not EventType.CANDIDATE_SNAPSHOT:
            return None

        states = self.store.load_states()
        prior_record = states.get(event.signal_id, {})
        prior_value = prior_record.get("state")
        prior = CandidateState(prior_value) if prior_value else None
        current = next_state(prior, event.payload)
        states[event.signal_id] = {
            "ticker": event.ticker,
            "state": current.value,
            "last_event_id": event.event_id,
            "updated_at_utc": event.observed_at_utc,
            "entry_trigger": event.payload.get("entry_trigger"),
            "stop": event.payload.get("stop"),
            "effective_target": event.payload.get("effective_target"),
        }
        self.store.save_states(states)

        previous_value = prior.value if prior else "NEW"
        if previous_value == current.value:
            return None
        transition = Transition(
            signal_id=event.signal_id,
            ticker=event.ticker,
            previous_state=previous_value,
            current_state=current.value,
            event_id=event.event_id,
            observed_at_utc=event.observed_at_utc,
            live_price=self._number(event.payload.get("live_price")),
            entry_trigger=self._number(event.payload.get("entry_trigger")),
            stop=self._number(event.payload.get("stop")),
            effective_target=self._number(event.payload.get("effective_target")),
            intraday_rvol=self._number(event.payload.get("intraday_rvol")),
            market_hunt_score=self._number(event.payload.get("market_hunt_score")),
        )
        transition_event = MarketEvent(
            event_type=EventType.SIGNAL_TRANSITION,
            ticker=event.ticker,
            signal_id=event.signal_id,
            observed_at_utc=event.observed_at_utc,
            source="v4-momentum-engine",
            payload=transition.to_dict(),
        )
        self.store.append_events([transition_event])
        return transition

    def process_many(self, events: Iterable[MarketEvent]) -> list[Transition]:
        transitions = []
        for event in events:
            transition = self.process(event)
            if transition is not None:
                transitions.append(transition)
        return transitions

    @staticmethod
    def snapshot_event(row: Mapping[str, Any], observed_at_utc: str | None = None) -> MarketEvent:
        kwargs = {"observed_at_utc": observed_at_utc} if observed_at_utc else {}
        return MarketEvent(
            event_type=EventType.CANDIDATE_SNAPSHOT,
            ticker=str(row.get("ticker", "")),
            payload=dict(row),
            source="v3-live-shadow-adapter",
            **kwargs,
        )
