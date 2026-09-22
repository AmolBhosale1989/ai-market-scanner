from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..control_plane import append_state, read_state, write_dataset, write_record


SHADOW_VALIDATION_SCHEMA = "4.shadow.1"
TARGET_THRESHOLDS = (5, 10, 15)
RETURN_HORIZONS = (1, 3, 5, 10)
BREAKDOWN_COLUMNS = ("theme", "catalyst_type", "market_regime_state")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _pct(exit_price: float | None, entry_price: float | None) -> float | None:
    if exit_price is None or entry_price is None or entry_price <= 0:
        return None
    return round((exit_price / entry_price - 1.0) * 100.0, 4)


def _value(row: Mapping[str, Any], *columns: str) -> Any:
    for column in columns:
        value = row.get(column)
        if value is not None and not (isinstance(value, float) and math.isnan(value)):
            return value
    return None


def _rank(frame: pd.DataFrame, score_column: str) -> pd.DataFrame:
    if frame is None or frame.empty or "ticker" not in frame or score_column not in frame:
        return pd.DataFrame()
    out = frame.copy()
    out["ticker"] = out["ticker"].astype(str).str.strip().str.upper()
    out["_shadow_score"] = pd.to_numeric(out[score_column], errors="coerce")
    out = out[(out["ticker"] != "") & out["_shadow_score"].notna()]
    out = out.sort_values(["_shadow_score", "ticker"], ascending=[False, True])
    out = out.drop_duplicates("ticker").reset_index(drop=True)
    out["_shadow_rank"] = np.arange(1, len(out) + 1)
    return out


def infer_as_of_session(candidates: pd.DataFrame, source_timestamp_utc: str = "") -> str:
    if candidates is not None and not candidates.empty and "live_session_date" in candidates:
        sessions = pd.to_datetime(candidates["live_session_date"], errors="coerce").dropna()
        if not sessions.empty:
            return sessions.dt.date.mode().iloc[0].isoformat()
    parsed = pd.to_datetime(source_timestamp_utc, errors="coerce", utc=True)
    if not pd.isna(parsed):
        return parsed.date().isoformat()
    return datetime.now(timezone.utc).date().isoformat()


@dataclass(frozen=True)
class ShadowValidationSettings:
    top_k: int = 20
    forward_sessions: int = 5


class ShadowValidationLedger:
    """Point-in-time V3/V4 ranking snapshots and forward outcome evidence."""

    def __init__(
        self,
        namespace: str = "v4_shadow_validation",
        settings: ShadowValidationSettings | None = None,
    ):
        self.namespace = namespace
        self.settings = settings or ShadowValidationSettings()

    def _load(self) -> dict[str, dict[str, Any]]:
        payload = read_state(self.namespace, "observations", default={}) or {}
        observations = payload.get("observations", {}) if isinstance(payload, dict) else {}
        return observations if isinstance(observations, dict) else {}

    def load_frame(self) -> pd.DataFrame:
        return self._frame(self._load())

    def _save(self, observations: dict[str, dict[str, Any]]) -> None:
        payload = {
            "schema_version": SHADOW_VALIDATION_SCHEMA,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "observations": observations,
        }
        append_state(self.namespace, "observations", payload)

    @staticmethod
    def _frame(observations: dict[str, dict[str, Any]]) -> pd.DataFrame:
        if not observations:
            return pd.DataFrame()
        return pd.DataFrame(observations.values()).sort_values(
            ["as_of_session", "ticker"], ascending=[True, True]
        ).reset_index(drop=True)

    def record_snapshot(
        self,
        v3_candidates: pd.DataFrame,
        v45_candidates: pd.DataFrame,
        as_of_session: str,
        observed_at_utc: str = "",
        model_payload: Mapping[str, Any] | None = None,
        source_name: str = "",
        v5_candidates: pd.DataFrame | None = None,
        v6_candidates: pd.DataFrame | None = None,
        v7_candidates: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        observations = self._load()
        # A market-session snapshot is an immutable cohort. A retry may export
        # or later resolve it, but must not append newly ranked symbols.
        if any(str(record.get("as_of_session", "")) == str(as_of_session) for record in observations.values()):
            self._export(observations)
            return self._frame(observations)
        v3 = _rank(v3_candidates, "market_hunt_score")
        v45 = _rank(v45_candidates, "v45_calibrated_score")
        v3_top = v3.head(self.settings.top_k)
        v45_top = v45.head(self.settings.top_k)
        v5_top = _rank(v5_candidates, "v5_adaptive_score").head(self.settings.top_k)
        v6_top = _rank(v6_candidates, "v6_robust_score").head(self.settings.top_k)
        v7_top = _rank(v7_candidates, "v6_robust_score").head(self.settings.top_k)
        v3_rows = {row["ticker"]: row for _, row in v3_top.iterrows()}
        v45_rows = {row["ticker"]: row for _, row in v45_top.iterrows()}
        v5_rows = {row["ticker"]: row for _, row in v5_top.iterrows()}
        v6_rows = {row["ticker"]: row for _, row in v6_top.iterrows()}
        v7_rows = {row["ticker"]: row for _, row in v7_top.iterrows()}
        captured_at = observed_at_utc or datetime.now(timezone.utc).isoformat()
        model_payload = dict(model_payload or {})

        for ticker in sorted(set(v3_rows) | set(v45_rows) | set(v5_rows) | set(v6_rows) | set(v7_rows)):
            key = f"{as_of_session}|{ticker}"
            v3_row = v3_rows.get(ticker)
            v45_row = v45_rows.get(ticker)
            v5_row = v5_rows.get(ticker)
            v6_row = v6_rows.get(ticker)
            v7_row = v7_rows.get(ticker)
            source_row = next(row for row in (v3_row, v45_row, v5_row, v6_row, v7_row) if row is not None)
            assert source_row is not None
            record = {
                "observation_id": key,
                "as_of_session": as_of_session,
                "observed_at_utc": captured_at,
                "source_name": source_name,
                "ticker": ticker,
                "baseline_price": _number(_value(source_row, "price", "live_price")),
                "selected_v3": v3_row is not None,
                "selected_v45": v45_row is not None,
                "selected_v5": v5_row is not None,
                "selected_v6": v6_row is not None,
                "selected_v7": v7_row is not None,
                "v3_rank": int(v3_row["_shadow_rank"]) if v3_row is not None else None,
                "v45_rank": int(v45_row["_shadow_rank"]) if v45_row is not None else None,
                "v5_rank": int(v5_row["_shadow_rank"]) if v5_row is not None else None,
                "v6_rank": int(v6_row["_shadow_rank"]) if v6_row is not None else None,
                "v7_rank": int(v7_row["_shadow_rank"]) if v7_row is not None else None,
                "market_hunt_score": _number(_value(source_row, "market_hunt_score")),
                "technical_score": _number(_value(source_row, "technical_score")),
                "formation_score": _number(_value(source_row, "formation_score")),
                "catalyst_score": _number(_value(source_row, "catalyst_score")),
                "intraday_rvol": _number(_value(source_row, "intraday_rvol")),
                "avg_share_volume20": _number(_value(source_row, "avg_share_volume20")),
                "avg_dollar_volume": _number(_value(source_row, "avg_dollar_volume")),
                "median_dollar_volume20": _number(_value(source_row, "median_dollar_volume20")),
                "adr20_pct": _number(_value(source_row, "adr20_pct")),
                "atr_pct": _number(_value(source_row, "atr_pct")),
                "runway_to_next_resistance_pct": _number(_value(source_row, "runway_to_next_resistance_pct")),
                "max_up_day_30d_pct": _number(_value(source_row, "max_up_day_30d_pct")),
                "rs20_vs_spy": _number(_value(source_row, "rs20_vs_spy")),
                "v45_calibrated_score": _number(_value(v45_row, "v45_calibrated_score")) if v45_row is not None else None,
                "v45_p5_probability": _number(_value(v45_row, "v45_p5_probability")) if v45_row is not None else None,
                "v45_p10_probability": _number(_value(v45_row, "v45_p10_probability")) if v45_row is not None else None,
                "v45_p15_probability": _number(_value(v45_row, "v45_p15_probability")) if v45_row is not None else None,
                "v5_adaptive_score": _number(_value(v5_row, "v5_adaptive_score")) if v5_row is not None else None,
                "v5_p5_probability": _number(_value(v5_row, "v5_p5_probability")) if v5_row is not None else None,
                "v5_p10_probability": _number(_value(v5_row, "v5_p10_probability")) if v5_row is not None else None,
                "v5_p15_probability": _number(_value(v5_row, "v5_p15_probability")) if v5_row is not None else None,
                "v6_robust_score": _number(_value(v6_row, "v6_robust_score")) if v6_row is not None else None,
                "v6_p5_probability": _number(_value(v6_row, "v6_p5_probability")) if v6_row is not None else None,
                "v6_p10_probability": _number(_value(v6_row, "v6_p10_probability")) if v6_row is not None else None,
                "v6_p15_probability": _number(_value(v6_row, "v6_p15_probability")) if v6_row is not None else None,
                "v6_confidence": str(_value(v6_row, "v6_confidence") or "") if v6_row is not None else "",
                "v6_decision": str(_value(v6_row, "v6_decision") or "") if v6_row is not None else "",
                "v45_model_version": str(model_payload.get("model_version", "")),
                "v45_model_status": str(model_payload.get("promotion_status", "")),
                "stage": str(_value(source_row, "stage") or ""),
                "theme": str(_value(source_row, "theme") or "UNCLASSIFIED"),
                "catalyst_type": str(_value(source_row, "catalyst_type", "catalyst_status") or "NONE"),
                "market_regime_state": str(_value(source_row, "market_regime_state") or "UNKNOWN"),
                "entry_model": str(_value(source_row, "entry_model") or ""),
                "stop": _number(_value(source_row, "stop")),
                "effective_target": _number(_value(source_row, "effective_target")),
                "effective_rr": _number(_value(source_row, "effective_rr")),
                "daily_bars_resolved": 0,
                "forward_5d_mfe_pct": None,
                "forward_5d_mae_pct": None,
                "false_breakout": None,
                "r_multiple_5d": None,
            }
            for horizon in RETURN_HORIZONS:
                record[f"return_{horizon}d_pct"] = None
            for threshold in TARGET_THRESHOLDS:
                record[f"forward_hit_{threshold}pct"] = None
            observations[key] = record

        self._save(observations)
        self._export(observations)
        return self._frame(observations)

    def unresolved_tickers(self) -> list[str]:
        frame = self.load_frame()
        if frame.empty:
            return []
        resolved = pd.to_numeric(frame.get("daily_bars_resolved"), errors="coerce").fillna(0)
        return sorted(frame.loc[resolved.lt(max(RETURN_HORIZONS)), "ticker"].astype(str).unique())

    def resolve_histories(self, histories: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
        observations = self._load()
        for record in observations.values():
            ticker = str(record.get("ticker", ""))
            history = histories.get(ticker)
            entry = _number(record.get("baseline_price"))
            if history is None or history.empty or entry is None:
                continue
            frame = history.copy()
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = [column[0] for column in frame.columns]
            if not {"High", "Low", "Close"}.issubset(frame.columns):
                continue
            session = pd.Timestamp(record["as_of_session"]).date()
            future = frame[pd.DatetimeIndex(frame.index).date > session].sort_index()
            available = min(len(future), max(RETURN_HORIZONS))
            record["daily_bars_resolved"] = max(int(record.get("daily_bars_resolved", 0)), available)
            for horizon in RETURN_HORIZONS:
                if len(future) >= horizon:
                    record[f"return_{horizon}d_pct"] = _pct(
                        _number(future.iloc[horizon - 1]["Close"]), entry
                    )
            if available < self.settings.forward_sessions:
                continue

            window = future.head(self.settings.forward_sessions)
            high = _number(pd.to_numeric(window["High"], errors="coerce").max())
            low = _number(pd.to_numeric(window["Low"], errors="coerce").min())
            record["forward_5d_mfe_pct"] = _pct(high, entry)
            record["forward_5d_mae_pct"] = _pct(low, entry)
            for threshold in TARGET_THRESHOLDS:
                mfe = _number(record.get("forward_5d_mfe_pct"))
                record[f"forward_hit_{threshold}pct"] = bool(mfe is not None and mfe >= threshold)

            stop = _number(record.get("stop"))
            false_breakout = False
            threshold_price = entry * 1.05
            for _, bar in window.iterrows():
                bar_low, bar_high = _number(bar["Low"]), _number(bar["High"])
                # Same-day stop/target ordering is unknowable in daily bars, so
                # count it as a failure rather than manufacture a favorable path.
                if stop is not None and bar_low is not None and bar_low <= stop:
                    false_breakout = True
                    break
                if bar_high is not None and bar_high >= threshold_price:
                    break
            record["false_breakout"] = false_breakout

            close_5d = _number(window.iloc[-1]["Close"])
            risk = entry - stop if stop is not None and entry > stop else None
            if close_5d is not None and risk:
                record["r_multiple_5d"] = round((close_5d - entry) / risk, 4)

        self._save(observations)
        self._export(observations)
        return self._frame(observations)

    def _strategy_summary(self, frame: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        strategies = (
            ("V3", "selected_v3", None),
            ("V4_5", "selected_v45", "v45"),
            ("V5", "selected_v5", "v5"),
            ("V6", "selected_v6", "v6"),
            ("V7_PAPER", "selected_v7", "v6"),
        )
        for strategy, selector, probability_prefix in strategies:
            selected_flag = frame.get(selector, pd.Series(False, index=frame.index))
            selected = frame[selected_flag.map(_truthy)].copy() if not frame.empty else frame
            mature = selected[
                pd.to_numeric(selected.get("daily_bars_resolved"), errors="coerce")
                .fillna(0).ge(self.settings.forward_sessions)
            ].copy() if not selected.empty else selected
            row: dict[str, Any] = {
                "strategy": strategy,
                "top_k": self.settings.top_k,
                "observation_days": int(selected["as_of_session"].nunique()) if len(selected) else 0,
                "selected_candidates": len(selected),
                "mature_candidates": len(mature),
            }
            if mature.empty:
                for threshold in TARGET_THRESHOLDS:
                    row[f"forward_hit_{threshold}pct_rate"] = None
                    row[f"p{threshold}_brier_score"] = None
                row.update({
                    "win_rate_5d_pct": None,
                    "avg_return_5d_pct": None,
                    "median_return_5d_pct": None,
                    "avg_forward_5d_mfe_pct": None,
                    "avg_forward_5d_mae_pct": None,
                    "avg_r_multiple_5d": None,
                    "false_breakout_rate_pct": None,
                })
                rows.append(row)
                continue
            for threshold in TARGET_THRESHOLDS:
                values = mature.get(f"forward_hit_{threshold}pct", pd.Series(dtype=object)).map(_truthy)
                row[f"forward_hit_{threshold}pct_rate"] = round(float(values.mean() * 100), 2) if len(values) else None
            returns = pd.to_numeric(mature.get("return_5d_pct"), errors="coerce").dropna()
            row["win_rate_5d_pct"] = round(float(returns.gt(0).mean() * 100), 2) if len(returns) else None
            row["avg_return_5d_pct"] = round(float(returns.mean()), 4) if len(returns) else None
            row["median_return_5d_pct"] = round(float(returns.median()), 4) if len(returns) else None
            for name in ("forward_5d_mfe_pct", "forward_5d_mae_pct", "r_multiple_5d"):
                values = pd.to_numeric(mature.get(name), errors="coerce").dropna()
                row[f"avg_{name}"] = round(float(values.mean()), 4) if len(values) else None
            failures = mature.get("false_breakout", pd.Series(dtype=object)).map(_truthy)
            row["false_breakout_rate_pct"] = round(float(failures.mean() * 100), 2) if len(failures) else None
            for target in TARGET_THRESHOLDS:
                probability_column = f"{probability_prefix}_p{target}_probability" if probability_prefix else ""
                probability_source = mature.get(probability_column, pd.Series(index=mature.index, dtype=float))
                probability = pd.to_numeric(probability_source, errors="coerce") / 100.0
                actual = mature.get(f"forward_hit_{target}pct", pd.Series(index=mature.index, dtype=object)).map(_truthy).astype(float)
                usable = probability.notna() & actual.notna()
                row[f"p{target}_brier_score"] = round(float(((probability[usable] - actual[usable]) ** 2).mean()), 6) if usable.any() else None
            rows.append(row)
        return pd.DataFrame(rows)

    def _daily(self, frame: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for session, group in frame.groupby("as_of_session") if not frame.empty else []:
            v3 = set(group.loc[group["selected_v3"].map(_truthy), "ticker"])
            v45 = set(group.loc[group["selected_v45"].map(_truthy), "ticker"])
            denominator = min(self.settings.top_k, len(v3), len(v45))
            mature = pd.to_numeric(group["daily_bars_resolved"], errors="coerce").fillna(0).ge(self.settings.forward_sessions)
            rows.append({
                "as_of_session": session,
                "v3_candidates": len(v3),
                "v45_candidates": len(v45),
                "v5_candidates": int(group.get("selected_v5", pd.Series(False, index=group.index)).map(_truthy).sum()),
                "v6_candidates": int(group.get("selected_v6", pd.Series(False, index=group.index)).map(_truthy).sum()),
                "v7_paper_candidates": int(group.get("selected_v7", pd.Series(False, index=group.index)).map(_truthy).sum()),
                "overlap_candidates": len(v3 & v45),
                "top_k_agreement_pct": round(len(v3 & v45) / denominator * 100, 2) if denominator else None,
                "mature_candidates": int(mature.sum()),
                "model_version": str(group["v45_model_version"].iloc[0]),
                "model_status": str(group["v45_model_status"].iloc[0]),
            })
        return pd.DataFrame(rows)

    def _breakdowns(self, frame: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for strategy, selector in (
            ("V3", "selected_v3"),
            ("V4_5", "selected_v45"),
            ("V5", "selected_v5"),
            ("V6", "selected_v6"),
            ("V7_PAPER", "selected_v7"),
        ):
            selected_flag = frame.get(selector, pd.Series(False, index=frame.index))
            selected = frame[selected_flag.map(_truthy)].copy() if not frame.empty else frame
            mature = selected[
                pd.to_numeric(selected.get("daily_bars_resolved"), errors="coerce")
                .fillna(0).ge(self.settings.forward_sessions)
            ].copy() if not selected.empty else selected
            for dimension in BREAKDOWN_COLUMNS:
                if dimension not in mature:
                    continue
                for label, group in mature.groupby(dimension, dropna=False):
                    returns = pd.to_numeric(group.get("return_5d_pct"), errors="coerce").dropna()
                    rows.append({
                        "strategy": strategy,
                        "dimension": dimension,
                        "segment": str(label or "UNKNOWN"),
                        "samples": len(group),
                        "hit_5pct_rate": round(float(group["forward_hit_5pct"].map(_truthy).mean() * 100), 2),
                        "hit_10pct_rate": round(float(group["forward_hit_10pct"].map(_truthy).mean() * 100), 2),
                        "hit_15pct_rate": round(float(group["forward_hit_15pct"].map(_truthy).mean() * 100), 2),
                        "avg_return_5d_pct": round(float(returns.mean()), 4) if len(returns) else None,
                        "avg_r_multiple_5d": round(float(pd.to_numeric(group["r_multiple_5d"], errors="coerce").mean()), 4),
                    })
        return pd.DataFrame(rows)

    def _export(self, observations: dict[str, dict[str, Any]]) -> None:
        frame = self._frame(observations)
        summary = self._strategy_summary(frame)
        daily = self._daily(frame)
        breakdowns = self._breakdowns(frame)
        for name, output, key in (
            ("v4_shadow_observations", frame, "observation_id"),
            ("v4_shadow_strategy_summary", summary, "strategy"),
            ("v4_shadow_daily_comparison", daily, "as_of_session"),
            ("v4_shadow_breakdowns", breakdowns, None),
        ):
            write_dataset(name, output, entity_key=key)
        mature = pd.to_numeric(frame.get("daily_bars_resolved"), errors="coerce").fillna(0).ge(
            self.settings.forward_sessions
        ) if not frame.empty else pd.Series(dtype=bool)
        health = {
            "schema_version": SHADOW_VALIDATION_SCHEMA,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "EVIDENCE_AVAILABLE" if mature.any() else "COLLECTING",
            "observation_days": int(frame["as_of_session"].nunique()) if len(frame) else 0,
            "observations": len(frame),
            "mature_observations": int(mature.sum()),
            "top_k": self.settings.top_k,
            "forward_sessions": self.settings.forward_sessions,
            "maximum_resolution_sessions": max(RETURN_HORIZONS),
        }
        write_record("v4_shadow_validation_health", health)
