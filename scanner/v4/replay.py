from __future__ import annotations

import hashlib
import json
import uuid

from ..control_plane import read_events
from .contracts import EventType, MarketEvent
from .engine import MomentumEngine, Transition
from .store import PostgresEventStore


def load_snapshot_events() -> list[MarketEvent]:
    events = [MarketEvent.from_dict(row) for row in read_events("v4.events")]
    return sorted((event for event in events if event.event_type is EventType.CANDIDATE_SNAPSHOT),
                  key=lambda event: (event.observed_at_utc, event.event_id))


def replay_events(events: list[MarketEvent]) -> tuple[list[Transition], str]:
    namespace = "v4_replay_" + uuid.uuid4().hex
    transitions = MomentumEngine(PostgresEventStore(namespace)).process_many(events)
    canonical = json.dumps([item.to_dict() for item in transitions], sort_keys=True, separators=(",", ":"))
    return transitions, hashlib.sha256(canonical.encode()).hexdigest()


def verify_deterministic_replay() -> dict:
    events = load_snapshot_events()
    first, first_hash = replay_events(events)
    _, second_hash = replay_events(events)
    return {
        "events_replayed": len(events),
        "transitions_replayed": len(first),
        "first_checksum": first_hash,
        "second_checksum": second_hash,
        "deterministic": first_hash == second_hash,
    }
