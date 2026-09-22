from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable, Mapping

import pandas as pd

from ..control_plane import append_state, read_state, write_dataset

from .contracts import EventType, MarketEvent, SCHEMA_VERSION
from .engine import Transition
from .health import parse_utc


ENTRY_STATES = {"TRIGGERED", "LIVE_CONFIRMED"}
TERMINAL_STATES = {"TARGET_HIT", "FAILED_BREAKOUT", "INVALIDATED"}
HORIZONS = (1, 3, 5, 10)


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _return_pct(price: float | None, entry: float | None) -> float | None:
    if price is None or entry is None or entry <= 0:
        return None
    return round((price / entry - 1) * 100, 3)


def classify_failure(payload: Mapping, state: str) -> str:
    if state == "TARGET_HIT":
        return "TARGET_HIT"
    if _truthy(payload.get("negative_catalyst_risk", False)):
        return "NEGATIVE_CATALYST"
    price, stop = _number(payload.get("live_price")), _number(payload.get("stop"))
    if price is not None and stop is not None and price <= stop:
        return "STOP_BREACH"
    if state == "FAILED_BREAKOUT":
        if not _truthy(payload.get("live_trigger_reached", False)):
            return "LOST_TRIGGER"
        if not _truthy(payload.get("live_above_vwap", False)):
            return "LOST_VWAP"
        rvol = _number(payload.get("intraday_rvol"))
        if rvol is not None and rvol < 0.8:
            return "VOLUME_COLLAPSE"
        return "FAILED_BREAKOUT_OTHER"
    return "INVALIDATED_OTHER" if state == "INVALIDATED" else ""


@dataclass(frozen=True)
class FillModel:
    entry_slippage_bps: float = 10.0
    exit_slippage_bps: float = 10.0

    def long_entry(self, trigger: float) -> float:
        return trigger * (1 + self.entry_slippage_bps / 10_000)

    def long_exit(self, price: float) -> float:
        return price * (1 - self.exit_slippage_bps / 10_000)


class SignalOutcomeLedger:
    """Evidence ledger driven only by observations available at each timestamp."""

    def __init__(
        self,
        namespace: str = "v4_outcomes",
        fill_model: FillModel | None = None,
    ):
        self.namespace = namespace
        self.fill_model = fill_model or FillModel()

    def _load(self) -> dict[str, dict]:
        value = read_state(self.namespace, "signals", default={}) or {}
        return value.get("signals", {}) if isinstance(value, dict) else {}

    def load_records(self) -> dict[str, dict]:
        return self._load()

    def _atomic_write(self, records: dict[str, dict]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "signals": records,
        }
        append_state(self.namespace, "signals", payload)

    @staticmethod
    def _new_record(event: MarketEvent) -> dict:
        payload = event.payload
        return {
            "signal_id": event.signal_id,
            "ticker": event.ticker,
            "first_seen_at_utc": event.observed_at_utc,
            "last_seen_at_utc": event.observed_at_utc,
            "entered_at_utc": "",
            "entry_session": "",
            "entry_trigger": _number(payload.get("entry_trigger")),
            "assumed_entry_price": None,
            "stop": _number(payload.get("stop")),
            "effective_target": _number(payload.get("effective_target")),
            "entry_slippage_bps": None,
            "exit_slippage_bps": None,
            "stage": str(payload.get("stage", "")),
            "market_hunt_score": _number(payload.get("market_hunt_score")),
            "technical_score": _number(payload.get("technical_score")),
            "catalyst_score": _number(payload.get("catalyst_score")),
            "theme": str(payload.get("theme", "")),
            "market_regime_state": str(payload.get("market_regime_state", "")),
            "entry_model": str(payload.get("entry_model", "")),
            "current_state": "WATCH",
            "latest_price": None,
            "samples_after_entry": 0,
            "max_price_after_entry": None,
            "min_price_after_entry": None,
            "mfe_pct": None,
            "mae_pct": None,
            "hit_5pct": False,
            "hit_10pct": False,
            "hit_15pct": False,
            "forward_5d_mfe_pct": None,
            "forward_5d_mae_pct": None,
            "forward_hit_5pct": False,
            "forward_hit_10pct": False,
            "forward_hit_15pct": False,
            "return_30m_pct": None,
            "return_1d_pct": None,
            "return_2d_pct": None,
            "return_3d_pct": None,
            "return_5d_pct": None,
            "return_10d_pct": None,
            "terminal_at_utc": "",
            "outcome": "",
            "failure_reason": "",
            "exit_price": None,
            "return_pct": None,
            "r_multiple": None,
            "daily_bars_resolved": 0,
        }

    def observe_many(
        self,
        observations: Iterable[tuple[MarketEvent, Transition | None]],
    ) -> pd.DataFrame:
        records = self._load()
        for event, transition in observations:
            if event.event_type is not EventType.CANDIDATE_SNAPSHOT:
                continue
            record = records.setdefault(event.signal_id, self._new_record(event))
            payload = event.payload
            record["last_seen_at_utc"] = event.observed_at_utc
            record["latest_price"] = _number(payload.get("live_price"))
            for name in ("market_hunt_score", "technical_score", "catalyst_score"):
                value = _number(payload.get(name))
                if value is not None:
                    record[name] = value
            if payload.get("theme"):
                record["theme"] = str(payload.get("theme"))
            if payload.get("market_regime_state"):
                record["market_regime_state"] = str(payload.get("market_regime_state"))

            if transition is not None:
                record["current_state"] = transition.current_state
                if transition.current_state in ENTRY_STATES and not record["entered_at_utc"]:
                    trigger = _number(record.get("entry_trigger"))
                    if trigger is not None:
                        record["entered_at_utc"] = event.observed_at_utc
                        session = str(payload.get("live_session_date", ""))
                        record["entry_session"] = session
                        record["assumed_entry_price"] = round(self.fill_model.long_entry(trigger), 6)
                        record["entry_slippage_bps"] = self.fill_model.entry_slippage_bps
                        record["exit_slippage_bps"] = self.fill_model.exit_slippage_bps
                if transition.current_state in TERMINAL_STATES and not record["terminal_at_utc"]:
                    record["terminal_at_utc"] = event.observed_at_utc
                    record["outcome"] = transition.current_state
                    record["failure_reason"] = classify_failure(payload, transition.current_state)
                    raw_exit = _number(payload.get("live_price"))
                    if transition.current_state == "TARGET_HIT":
                        raw_exit = _number(record.get("effective_target")) or raw_exit
                    elif transition.current_state == "INVALIDATED":
                        raw_exit = _number(record.get("stop")) or raw_exit
                    if raw_exit is not None:
                        record["exit_price"] = round(self.fill_model.long_exit(raw_exit), 6)
                        record["return_pct"] = _return_pct(record["exit_price"], record["assumed_entry_price"])
                        risk = None
                        entry = _number(record.get("assumed_entry_price"))
                        stop = _number(record.get("stop"))
                        if entry is not None and stop is not None and entry > stop:
                            risk = entry - stop
                        if risk:
                            record["r_multiple"] = round((record["exit_price"] - entry) / risk, 3)

            entered = parse_utc(record.get("entered_at_utc", ""))
            observed = parse_utc(event.observed_at_utc)
            terminal = parse_utc(record.get("terminal_at_utc", ""))
            entry = _number(record.get("assumed_entry_price"))
            if entered and observed and observed >= entered and entry is not None:
                latest = _number(payload.get("live_price"))
                # Trade-path statistics stop at the terminal event. Later
                # snapshots are forward evidence and must not improve a closed
                # trade's MFE/MAE or threshold-hit result.
                if terminal is None or observed <= terminal:
                    # The trigger bar has ambiguous intrabar ordering. Use only
                    # its observed close; later bars may contribute high/low.
                    if observed == entered:
                        high = low = latest
                    else:
                        high = _number(payload.get("live_bar_high")) or latest
                        low = _number(payload.get("live_bar_low")) or latest
                    if latest is not None:
                        record["samples_after_entry"] = int(record.get("samples_after_entry", 0)) + 1
                    if high is not None:
                        previous = _number(record.get("max_price_after_entry"))
                        record["max_price_after_entry"] = max(high, previous) if previous is not None else high
                    if low is not None:
                        previous = _number(record.get("min_price_after_entry"))
                        record["min_price_after_entry"] = min(low, previous) if previous is not None else low
                    record["mfe_pct"] = _return_pct(_number(record.get("max_price_after_entry")), entry)
                    record["mae_pct"] = _return_pct(_number(record.get("min_price_after_entry")), entry)
                    record["hit_5pct"] = bool(record["hit_5pct"] or (record["mfe_pct"] is not None and record["mfe_pct"] >= 5))
                    record["hit_10pct"] = bool(record["hit_10pct"] or (record["mfe_pct"] is not None and record["mfe_pct"] >= 10))
                    record["hit_15pct"] = bool(record["hit_15pct"] or (record["mfe_pct"] is not None and record["mfe_pct"] >= 15))
                # The 30-minute mark measures the signal's forward return, not
                # the closed trade, so it remains eligible after an early exit.
                if record["return_30m_pct"] is None and (observed - entered).total_seconds() >= 1800:
                    record["return_30m_pct"] = _return_pct(latest, entry)

        self._save_and_export(records)
        return self.to_frame(records)

    def resolve_daily_histories(self, histories: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
        records = self._load()
        for record in records.values():
            entered = parse_utc(record.get("entered_at_utc", ""))
            if entered is None:
                continue
            history = histories.get(str(record.get("ticker", "")))
            if history is None or history.empty:
                continue
            frame = history.copy()
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = [column[0] for column in frame.columns]
            if not {"High", "Low", "Close"}.issubset(frame.columns):
                continue
            entry_session = record.get("entry_session") or entered.date().isoformat()
            session_dates = pd.DatetimeIndex(frame.index).date
            future = frame[session_dates > pd.Timestamp(entry_session).date()].sort_index()
            available = min(len(future), max(HORIZONS))
            record["daily_bars_resolved"] = max(int(record.get("daily_bars_resolved", 0)), available)
            entry = _number(record.get("assumed_entry_price"))
            if entry is None:
                continue
            for horizon in HORIZONS:
                key = f"return_{horizon}d_pct"
                if record.get(key) is None and len(future) >= horizon:
                    record[key] = _return_pct(_number(future.iloc[horizon - 1]["Close"]), entry)

            window = future.head(5)
            if not window.empty:
                high = _number(pd.to_numeric(window["High"], errors="coerce").max())
                low = _number(pd.to_numeric(window["Low"], errors="coerce").min())
                record["forward_5d_mfe_pct"] = _return_pct(high, entry)
                record["forward_5d_mae_pct"] = _return_pct(low, entry)
                for threshold in (5, 10, 15):
                    key = f"forward_hit_{threshold}pct"
                    forward_mfe = record["forward_5d_mfe_pct"]
                    record[key] = bool(forward_mfe is not None and forward_mfe >= threshold)

            if not record.get("outcome"):
                stop = _number(record.get("stop"))
                target = _number(record.get("effective_target"))
                for timestamp, row in future.head(5).iterrows():
                    low, high = _number(row["Low"]), _number(row["High"])
                    stop_hit = stop is not None and low is not None and low <= stop
                    target_hit = target is not None and high is not None and high >= target
                    if stop_hit:
                        # With daily bars, a same-bar high cannot be assumed to
                        # precede the stop. Freeze favorable excursion at prior
                        # evidence and cap adverse excursion at the modeled exit.
                        previous = _number(record.get("min_price_after_entry"))
                        record["min_price_after_entry"] = min(stop, previous) if previous is not None else stop
                        reason = "STOP_AMBIGUOUS_SAME_BAR" if target_hit else "STOP_BREACH"
                        exit_price = self.fill_model.long_exit(stop)
                        record.update({
                            "current_state": "INVALIDATED",
                            "outcome": "INVALIDATED",
                            "failure_reason": reason,
                            "terminal_at_utc": pd.Timestamp(timestamp).isoformat(),
                            "exit_price": round(exit_price, 6),
                            "return_pct": _return_pct(exit_price, entry),
                        })
                        risk = entry - stop if stop is not None and entry > stop else None
                        if risk:
                            record["r_multiple"] = round((exit_price - entry) / risk, 3)
                        break
                    if target_hit:
                        previous_high = _number(record.get("max_price_after_entry"))
                        record["max_price_after_entry"] = max(target, previous_high) if previous_high is not None else target
                        if low is not None:
                            previous_low = _number(record.get("min_price_after_entry"))
                            record["min_price_after_entry"] = min(low, previous_low) if previous_low is not None else low
                        exit_price = self.fill_model.long_exit(target)
                        record.update({
                            "current_state": "TARGET_HIT",
                            "outcome": "TARGET_HIT",
                            "failure_reason": "TARGET_HIT",
                            "terminal_at_utc": pd.Timestamp(timestamp).isoformat(),
                            "exit_price": round(exit_price, 6),
                            "return_pct": _return_pct(exit_price, entry),
                        })
                        risk = entry - stop if stop is not None and entry > stop else None
                        if risk:
                            record["r_multiple"] = round((exit_price - entry) / risk, 3)
                        break
                    if high is not None:
                        previous = _number(record.get("max_price_after_entry"))
                        record["max_price_after_entry"] = max(high, previous) if previous is not None else high
                    if low is not None:
                        previous = _number(record.get("min_price_after_entry"))
                        record["min_price_after_entry"] = min(low, previous) if previous is not None else low
                record["mfe_pct"] = _return_pct(_number(record.get("max_price_after_entry")), entry)
                record["mae_pct"] = _return_pct(_number(record.get("min_price_after_entry")), entry)
                for threshold in (5, 10, 15):
                    key = f"hit_{threshold}pct"
                    record[key] = bool(record[key] or (record["mfe_pct"] is not None and record["mfe_pct"] >= threshold))

        self._save_and_export(records)
        return self.to_frame(records)

    @staticmethod
    def to_frame(records: dict[str, dict]) -> pd.DataFrame:
        return pd.DataFrame(records.values()).sort_values("first_seen_at_utc") if records else pd.DataFrame()

    def summary(self, records: dict[str, dict] | None = None) -> pd.DataFrame:
        frame = self.to_frame(records if records is not None else self._load())
        entered = frame[frame["entered_at_utc"].fillna("").astype(str).str.len() > 0] if not frame.empty else frame
        closed = entered[entered["outcome"].fillna("").astype(str).str.len() > 0] if not entered.empty else entered
        mature = entered[pd.to_numeric(entered.get("return_5d_pct"), errors="coerce").notna()] if not entered.empty else entered
        row = {
            "signals_seen": len(frame),
            "signals_entered": len(entered),
            "signals_closed": len(closed),
            "five_day_samples": len(mature),
            "target_hit_rate_pct": round(float(closed["outcome"].eq("TARGET_HIT").mean() * 100), 1) if len(closed) else None,
            "forward_hit_5pct_rate": round(float(mature["forward_hit_5pct"].astype(bool).mean() * 100), 1) if len(mature) else None,
            "forward_hit_10pct_rate": round(float(mature["forward_hit_10pct"].astype(bool).mean() * 100), 1) if len(mature) else None,
            "forward_hit_15pct_rate": round(float(mature["forward_hit_15pct"].astype(bool).mean() * 100), 1) if len(mature) else None,
            "avg_mfe_pct": round(float(pd.to_numeric(mature.get("mfe_pct"), errors="coerce").mean()), 2) if len(mature) else None,
            "avg_mae_pct": round(float(pd.to_numeric(mature.get("mae_pct"), errors="coerce").mean()), 2) if len(mature) else None,
            "avg_forward_5d_mfe_pct": round(float(pd.to_numeric(mature.get("forward_5d_mfe_pct"), errors="coerce").mean()), 2) if len(mature) else None,
            "avg_forward_5d_mae_pct": round(float(pd.to_numeric(mature.get("forward_5d_mae_pct"), errors="coerce").mean()), 2) if len(mature) else None,
        }
        for horizon in ("30m", "1d", "3d", "5d", "10d"):
            values = pd.to_numeric(entered.get(f"return_{horizon}_pct"), errors="coerce").dropna() if len(entered) else pd.Series(dtype=float)
            row[f"avg_return_{horizon}_pct"] = round(float(values.mean()), 3) if len(values) else None
        return pd.DataFrame([row])

    def _save_and_export(self, records: dict[str, dict]) -> None:
        self._atomic_write(records)
        frame = self.to_frame(records)
        write_dataset("v4_outcomes", frame, entity_key="signal_id")
        summary = self.summary(records)
        write_dataset("v4_outcome_summary", summary, entity_key=None)
