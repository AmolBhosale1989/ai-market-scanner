from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from typing import Protocol

import requests

from ..control_plane import append_events, append_state, read_state
from .engine import Transition


ACTIONABLE_STATES = {
    "TRIGGERED",
    "LIVE_CONFIRMED",
    "TARGET_HIT",
    "FAILED_BREAKOUT",
    "INVALIDATED",
}


@dataclass(frozen=True)
class Alert:
    alert_id: str
    ticker: str
    signal_id: str
    previous_state: str
    current_state: str
    observed_at_utc: str
    created_at_utc: str
    severity: str
    live_price: float | None = None
    entry_trigger: float | None = None
    stop: float | None = None
    effective_target: float | None = None
    intraday_rvol: float | None = None
    market_hunt_score: float | None = None

    @classmethod
    def from_transition(cls, transition: Transition) -> "Alert":
        severity = "ACTION" if transition.current_state in {"TRIGGERED", "LIVE_CONFIRMED"} else "OUTCOME"
        return cls(
            alert_id=f"transition:{transition.event_id}:{transition.current_state}",
            ticker=transition.ticker,
            signal_id=transition.signal_id,
            previous_state=transition.previous_state,
            current_state=transition.current_state,
            observed_at_utc=transition.observed_at_utc,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            severity=severity,
            live_price=transition.live_price,
            entry_trigger=transition.entry_trigger,
            stop=transition.stop,
            effective_target=transition.effective_target,
            intraday_rvol=transition.intraday_rvol,
            market_hunt_score=transition.market_hunt_score,
        )

    def message(self) -> str:
        context = (
            f"Price {self.live_price} | Entry {self.entry_trigger} | Stop {self.stop}\n"
            f"Target {self.effective_target} | RVOL {self.intraday_rvol} | Score {self.market_hunt_score}"
        )
        return (
            f"Market Hunt V4 {self.severity}\n"
            f"{self.ticker}: {self.previous_state} → {self.current_state}\n"
            f"{context}\nSignal: {self.signal_id}"
        )


class AlertSink(Protocol):
    name: str
    def send(self, alert: Alert) -> None: ...


class DatabaseAlertSink:
    name = "audit-database"

    def send(self, alert: Alert) -> None:
        append_events("v4.alerts", [asdict(alert)], key_field="alert_id", observed_field="observed_at_utc")


class TelegramAlertSink:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, timeout_seconds: float = 15.0):
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "TelegramAlertSink | None":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        return cls(token, chat_id) if token and chat_id else None

    def send(self, alert: Alert) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            json={"chat_id": self.chat_id, "text": alert.message()},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()


class AlertRouter:
    def __init__(self, sinks: list[AlertSink], namespace: str = "v4"):
        self.sinks = sinks
        self.namespace = namespace

    def _load(self) -> dict[str, list[str]]:
        value = read_state(self.namespace, "alert_dispatch", default={}) or {}
        return value if isinstance(value, dict) else {}

    def _save(self, value: dict[str, list[str]]) -> None:
        append_state(self.namespace, "alert_dispatch", value)

    def route(self, transitions: list[Transition]) -> tuple[int, list[str]]:
        delivered = self._load()
        sent = 0
        failures = []
        for transition in transitions:
            if transition.current_state not in ACTIONABLE_STATES:
                continue
            alert = Alert.from_transition(transition)
            successful_sinks = set(delivered.get(alert.alert_id, []))
            for sink in self.sinks:
                if sink.name in successful_sinks:
                    continue
                try:
                    sink.send(alert)
                    successful_sinks.add(sink.name)
                    sent += 1
                except Exception as exc:
                    failures.append(f"{sink.name}:{type(exc).__name__}")
            delivered[alert.alert_id] = sorted(successful_sinks)
        # Bound state while retaining the newest insertion-order entries.
        delivered = dict(list(delivered.items())[-20_000:])
        self._save(delivered)
        return sent, failures
