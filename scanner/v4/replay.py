from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from .contracts import EventType, MarketEvent
from .engine import MomentumEngine, Transition
from .store import FileEventStore


def load_snapshot_events(path: Path) -> list[MarketEvent]:
    events = []
    if not Path(path).exists():
        return events
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        event = MarketEvent.from_dict(json.loads(line))
        if event.event_type is EventType.CANDIDATE_SNAPSHOT:
            events.append(event)
    return sorted(events, key=lambda event: (event.observed_at_utc, event.event_id))


def replay_events(events: list[MarketEvent]) -> tuple[list[Transition], str]:
    with tempfile.TemporaryDirectory(prefix="market-hunt-v4-replay-") as root:
        engine = MomentumEngine(FileEventStore(Path(root)))
        transitions = engine.process_many(events)
    canonical = json.dumps([item.to_dict() for item in transitions], sort_keys=True, separators=(",", ":"))
    return transitions, hashlib.sha256(canonical.encode()).hexdigest()


def verify_deterministic_replay(path: Path) -> dict:
    events = load_snapshot_events(path)
    first, first_hash = replay_events(events)
    second, second_hash = replay_events(events)
    return {
        "events_replayed": len(events),
        "transitions_replayed": len(first),
        "first_checksum": first_hash,
        "second_checksum": second_hash,
        "deterministic": first_hash == second_hash,
    }
