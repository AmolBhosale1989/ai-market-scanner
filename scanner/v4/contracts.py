from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping


SCHEMA_VERSION = "4.0.0"


class EventType(str, Enum):
    CANDIDATE_SNAPSHOT = "candidate.snapshot"
    PRICE_BAR = "market.bar"
    QUOTE = "market.quote"
    CATALYST = "catalyst.detected"
    OPTIONS_FLOW = "options.flow"
    MICROSTRUCTURE = "microstructure.snapshot"
    SIGNAL_TRANSITION = "signal.transition"
    OUTCOME = "signal.outcome"


class CandidateState(str, Enum):
    DISCOVER = "DISCOVER"
    WATCH = "WATCH"
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    LIVE_CONFIRMED = "LIVE_CONFIRMED"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    INVALIDATED = "INVALIDATED"
    TARGET_HIT = "TARGET_HIT"


TERMINAL_STATES = {
    CandidateState.FAILED_BREAKOUT,
    CandidateState.INVALIDATED,
    CandidateState.TARGET_HIT,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def build_signal_id(ticker: str, entry_trigger: Any, stop: Any) -> str:
    """Identify a setup, not merely a ticker, so old state cannot leak forward."""
    try:
        entry = float(entry_trigger)
        stop_value = float(stop)
    except (TypeError, ValueError):
        entry = stop_value = math.nan
    normalized = str(ticker or "").strip().upper()
    if math.isfinite(entry) and math.isfinite(stop_value):
        return f"{normalized}|{entry:.4f}|{stop_value:.4f}"
    return normalized


@dataclass(frozen=True)
class MarketEvent:
    event_type: EventType
    ticker: str
    payload: Mapping[str, Any]
    signal_id: str = ""
    observed_at_utc: str = field(default_factory=utc_now)
    source: str = "market-hunt"
    schema_version: str = SCHEMA_VERSION
    event_id: str = ""

    def __post_init__(self):
        ticker = self.ticker.strip().upper()
        if not ticker:
            raise ValueError("ticker is required")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "payload", _json_safe(dict(self.payload)))
        if not self.signal_id:
            object.__setattr__(
                self,
                "signal_id",
                build_signal_id(ticker, self.payload.get("entry_trigger"), self.payload.get("stop")),
            )
        if not self.event_id:
            canonical = json.dumps(
                {
                    "event_type": self.event_type.value,
                    "ticker": ticker,
                    "signal_id": self.signal_id,
                    "observed_at_utc": self.observed_at_utc,
                    "payload": self.payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            object.__setattr__(self, "event_id", hashlib.sha256(canonical.encode()).hexdigest()[:24])

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["event_type"] = self.event_type.value
        return _json_safe(result)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MarketEvent":
        return cls(
            event_type=EventType(raw["event_type"]),
            ticker=str(raw["ticker"]),
            payload=raw.get("payload", {}),
            signal_id=str(raw.get("signal_id", "")),
            observed_at_utc=str(raw.get("observed_at_utc", utc_now())),
            source=str(raw.get("source", "market-hunt")),
            schema_version=str(raw.get("schema_version", SCHEMA_VERSION)),
            event_id=str(raw.get("event_id", "")),
        )
