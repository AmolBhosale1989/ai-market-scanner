from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from .contracts import MarketEvent, SCHEMA_VERSION


class FileEventStore:
    """Small durable shadow store; replaceable by Postgres/Redis adapters later."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.state_file = self.root / "v4_signal_state.json"
        self.event_file = self.root / "v4_events.ndjson"
        self.seen_file = self.root / "v4_seen_events.json"

    def _atomic_json(self, path: Path, value: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=self.root, text=True)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load_states(self) -> dict[str, dict[str, Any]]:
        if not self.state_file.exists():
            return {}
        try:
            value = json.loads(self.state_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return value.get("signals", {}) if isinstance(value, dict) else {}

    def save_states(self, states: dict[str, dict[str, Any]]) -> None:
        self._atomic_json(self.state_file, {"schema_version": SCHEMA_VERSION, "signals": states})

    def _seen(self) -> list[str]:
        if not self.seen_file.exists():
            return []
        try:
            value = json.loads(self.seen_file.read_text())
            return [str(item) for item in value] if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def append_events(self, events: Iterable[MarketEvent]) -> int:
        unique = []
        seen_ordered = self._seen()
        seen = set(seen_ordered)
        for event in events:
            if event.event_id not in seen:
                unique.append(event)
                seen.add(event.event_id)
        if not unique:
            return 0

        self.root.mkdir(parents=True, exist_ok=True)
        with self.event_file.open("a") as handle:
            for event in unique:
                handle.write(json.dumps(event.to_dict(), sort_keys=True, allow_nan=False) + "\n")
        # Bound the idempotency index while preserving insertion order.
        ordered = list(dict.fromkeys([*seen_ordered, *(event.event_id for event in unique)]))
        self._atomic_json(self.seen_file, ordered[-100_000:])
        return len(unique)
