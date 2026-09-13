from __future__ import annotations

import math
from typing import Any, Mapping

from .contracts import CandidateState, TERMINAL_STATES


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def initial_state(snapshot: Mapping[str, Any]) -> CandidateState:
    stage = str(snapshot.get("stage", "")).upper()
    if stage in {"ARMED", "CONFIRMED"}:
        return CandidateState.ARMED
    if stage == "DISCOVER":
        return CandidateState.DISCOVER
    return CandidateState.WATCH


def next_state(previous: CandidateState | None, snapshot: Mapping[str, Any]) -> CandidateState:
    """Pure, deterministic V4 lifecycle transition.

    Terminal states are sticky for a signal. A changed entry/stop produces a new
    signal_id and therefore a fresh lifecycle.
    """
    if previous in TERMINAL_STATES:
        return previous

    live_status = str(snapshot.get("live_status", ""))
    if live_status != "LIVE":
        return previous or initial_state(snapshot)

    price = _number(snapshot.get("live_price"))
    stop = _number(snapshot.get("stop"))
    target = _number(snapshot.get("effective_target"))
    negative = _truthy(snapshot.get("negative_catalyst_risk", False))
    trigger = _truthy(snapshot.get("live_trigger_reached", False))
    above_vwap = _truthy(snapshot.get("live_above_vwap", False))
    rvol = _number(snapshot.get("intraday_rvol"))
    action = str(snapshot.get("live_trade_action", ""))

    if negative or (math.isfinite(price) and math.isfinite(stop) and price <= stop):
        return CandidateState.INVALIDATED
    if previous in {CandidateState.TRIGGERED, CandidateState.LIVE_CONFIRMED}:
        if math.isfinite(price) and math.isfinite(target) and price >= target:
            return CandidateState.TARGET_HIT
        if (not trigger) or (not above_vwap) or (math.isfinite(rvol) and rvol < 0.8):
            return CandidateState.FAILED_BREAKOUT
    if action.startswith("BUY / LIVE CONFIRMED"):
        return CandidateState.LIVE_CONFIRMED
    if trigger:
        return CandidateState.TRIGGERED
    return initial_state(snapshot)
