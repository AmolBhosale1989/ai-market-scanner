from __future__ import annotations

from typing import Any, Iterable

from ..control_plane import append_events, append_state, read_events, read_state
from .contracts import MarketEvent, SCHEMA_VERSION


class PostgresEventStore:
    """Durable V4 state and immutable events in the PostgreSQL control plane."""

    def __init__(self, namespace: str = "v4"):
        self.namespace = str(namespace)

    def load_states(self) -> dict[str, dict[str, Any]]:
        value = read_state(self.namespace, "signal_state", default={}) or {}
        return value.get("signals", {}) if isinstance(value, dict) else {}

    def save_states(self, states: dict[str, dict[str, Any]]) -> None:
        append_state(self.namespace, "signal_state", {
            "schema_version": SCHEMA_VERSION,
            "signals": states,
        })

    def append_events(self, events: Iterable[MarketEvent]) -> int:
        return append_events(
            f"{self.namespace}.events",
            (event.to_dict() for event in events),
        )

    def load_events(self, limit: int | None = None) -> list[dict]:
        return read_events(f"{self.namespace}.events", limit=limit)
