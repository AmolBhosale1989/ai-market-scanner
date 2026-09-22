"""Deterministic, PostgreSQL-derived quant research in shadow mode.

This module cannot publish production decisions or route orders.  It ranks the
daily V3 discovery output, freezes point-in-time cohorts, and measures them on
later warehouse sessions.  Authoritative OHLCV is read only through
``scanner.warehouse``.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .control_plane import append_state, read_dataset, read_state, write_dataset, write_record
from .session_contract import expected_market_data_session
from .warehouse import frames as warehouse_frames


STRATEGIES = (
    "CROSS_SECTIONAL_MOMENTUM",
    "VOLATILITY_CONTRACTION_BREAKOUT",
    "TREND_PULLBACK",
    "RESIDUAL_SECTOR_STRENGTH",
)
ENSEMBLE = "REGIME_ENSEMBLE"
BASELINE = "V3_BASELINE"
ALL_MODELS = STRATEGIES + (ENSEMBLE, BASELINE)
PROVENANCE = "POSTGRESQL_DERIVED_SHADOW"
TOP_K = int(os.getenv("QUANT_SHADOW_TOP_K", "10"))
MIN_CLOSED_TRADES = int(os.getenv("QUANT_SHADOW_MIN_CLOSED", "30"))
TARGET_PCT = 8.0
MAX_HOLD_SESSIONS = 7


def _num(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").fillna(default)


def _bool(frame: pd.DataFrame, name: str, default: bool = False) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[name]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def _rank(values: pd.Series, higher_is_better: bool = True) -> pd.Series:
    values = pd.to_numeric(values, errors="coerce")
    if values.notna().sum() <= 1:
        return pd.Series(50.0, index=values.index)
    return values.rank(pct=True, ascending=higher_is_better).fillna(0.0) * 100.0


def _base(candidates: pd.DataFrame) -> pd.DataFrame:
    data = candidates.copy()
    if "ticker" not in data:
        raise RuntimeError("QUANT_SHADOW_INPUT_INVALID: all_candidates has no ticker")
    data["ticker"] = data["ticker"].astype(str).str.upper().str.strip()
    price = _num(data, "price")
    liquid = _bool(data, "liquidity_gate_passed", True)
    negative = _bool(data, "negative_catalyst_risk", False)
    eligible = (
        data["ticker"].str.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}")
        & price.ge(5.0)
        & _num(data, "median_dollar_volume20").ge(20_000_000)
        & liquid
        & ~negative
        & _num(data, "risk_score", 100).le(70)
    )
    return data.loc[eligible].drop_duplicates("ticker", keep="first").copy()


def _score_rows(data: pd.DataFrame, strategy: str) -> pd.DataFrame:
    price, ema20, ema50, ema200 = (_num(data, x) for x in ("price", "ema20", "ema50", "ema200"))
    rs, rvol, theme = (_num(data, x) for x in ("rs20_vs_spy", "rvol", "theme_score"))
    technical, formation = (_num(data, x) for x in ("technical_score", "formation_score"))
    rsi, adr = (_num(data, x) for x in ("rsi14", "adr20_pct"))
    near_high = _num(data, "distance_to_20d_high_pct", 99).abs()
    extension = _num(data, "extension_above_20d_high_pct", 99)
    retest = _num(data, "retest_quality_score")

    if strategy == "CROSS_SECTIONAL_MOMENTUM":
        mask = (price > ema20) & (ema20 > ema50) & rs.gt(0) & rsi.between(50, 76) & extension.le(5)
        score = .35*_rank(rs) + .20*_rank(rvol) + .15*_rank(theme) + .15*_rank(technical) + .15*_rank(formation)
    elif strategy == "VOLATILITY_CONTRACTION_BREAKOUT":
        mask = (price > ema50) & near_high.le(5) & rsi.between(48, 74) & adr.between(1.5, 8) & extension.le(3)
        score = .25*_rank(-near_high) + .25*_rank(formation) + .20*_rank(technical) + .15*_rank(rvol) + .15*_rank(theme)
    elif strategy == "TREND_PULLBACK":
        ema_distance = ((price / ema20.replace(0, np.nan)) - 1).abs().mul(100)
        mask = (price > ema50) & (ema20 > ema50) & (ema50 > ema200) & ema_distance.le(4) & rsi.between(42, 66)
        structure = _bool(data, "higher_low").astype(float)*50 + _bool(data, "bullish_close").astype(float)*50
        score = .25*_rank(-ema_distance) + .25*_rank(retest) + .20*_rank(structure) + .15*_rank(rs) + .15*_rank(theme)
    elif strategy == "RESIDUAL_SECTOR_STRENGTH":
        sector_ok = _bool(data, "sector_regime_ok", True)
        mask = sector_ok & (price > ema20) & rs.gt(0) & rsi.between(48, 78)
        confidence = _num(data, "theme_match_confidence")
        score = .35*_rank(rs) + .25*_rank(theme) + .15*_rank(confidence) + .15*_rank(technical) + .10*_rank(rvol)
    elif strategy == BASELINE:
        mask = pd.Series(True, index=data.index)
        score = _rank(_num(data, "market_hunt_score", _num(data, "final_score")))
    else:
        raise ValueError(f"unknown quant shadow strategy: {strategy}")

    selected = data.loc[mask].copy()
    selected["strategy"] = strategy
    selected["strategy_score"] = score.loc[mask].round(4)
    return selected.sort_values(["strategy_score", "ticker"], ascending=[False, True])


def _trade_levels(row: pd.Series) -> tuple[float, float, float]:
    entry = float(row["price"])
    existing_stop = pd.to_numeric(pd.Series([row.get("stop")]), errors="coerce").iloc[0]
    atr_pct = float(pd.to_numeric(pd.Series([row.get("atr_pct", 3)]), errors="coerce").fillna(3).iloc[0])
    fallback_risk = min(max(atr_pct * .75, 2.0), 4.0)
    if pd.notna(existing_stop) and 0 < float(existing_stop) < entry:
        risk_pct = (entry - float(existing_stop)) / entry * 100
        stop = float(existing_stop) if .5 <= risk_pct <= 5 else entry * (1-fallback_risk/100)
    else:
        stop = entry * (1-fallback_risk/100)
    return round(entry, 4), round(stop, 4), round(entry*(1+TARGET_PCT/100), 4)


def build_signals(candidates: pd.DataFrame, as_of_session: str, top_k: int = TOP_K) -> pd.DataFrame:
    """Build deterministic strategy cohorts from a completed V3 daily scan."""
    data = _base(candidates)
    batches = [_score_rows(data, name).head(top_k) for name in STRATEGIES]
    votes = pd.concat(batches, ignore_index=True) if batches else pd.DataFrame()
    if not votes.empty:
        regime = votes.get("market_regime_state", pd.Series("NEUTRAL", index=votes.index)).astype(str).str.upper()
        trend_regime = regime.str.contains("STRONG|BULL|RISK_ON|HEALTHY", regex=True)
        weak_regime = regime.str.contains("WEAK|BEAR|RISK_OFF|DEFENSIVE", regex=True)
        trend_model = votes["strategy"].isin({"CROSS_SECTIONAL_MOMENTUM", "VOLATILITY_CONTRACTION_BREAKOUT"})
        pullback_model = votes["strategy"].isin({"TREND_PULLBACK", "RESIDUAL_SECTOR_STRENGTH"})
        # Regime affects ensemble weighting only; every underlying model keeps
        # its independent cohort so this adjustment can be evaluated honestly.
        votes["ensemble_component"] = (
            votes["strategy_score"]
            + (trend_regime & trend_model).astype(float)*3
            + (~trend_regime & ~weak_regime & pullback_model).astype(float)*2
            - weak_regime.astype(float)*8
        )
        ensemble = (votes.groupby("ticker", as_index=False)
                    .agg(vote_count=("strategy", "nunique"), strategy_score=("ensemble_component", "mean")))
        ensemble = ensemble[ensemble["vote_count"] >= 2]
        ensemble["strategy_score"] = ensemble["strategy_score"] + ensemble["vote_count"]*2
        ensemble = ensemble.sort_values(["strategy_score", "ticker"], ascending=[False, True]).head(top_k)
        if not ensemble.empty:
            ensemble = ensemble.merge(data, on="ticker", how="left")
            ensemble["strategy"] = ENSEMBLE
            batches.append(ensemble)
    batches.append(_score_rows(data, BASELINE).head(top_k))
    out = pd.concat(batches, ignore_index=True) if batches else pd.DataFrame()
    if out.empty:
        return out
    levels = out.apply(_trade_levels, axis=1, result_type="expand")
    out["reference_close"], out["stop_price"], out["target_price"] = levels[0], levels[1], levels[2]
    out["as_of_session"] = str(as_of_session)
    out["shadow_status"] = "SHADOW_ONLY"
    out["market_data_source"] = PROVENANCE
    out["broker_execution_enabled"] = False
    out["production_applied"] = False
    out["signal_id"] = out.apply(lambda r: f"{as_of_session}|{r['strategy']}|{r['ticker']}", axis=1)
    columns = [
        "signal_id", "as_of_session", "strategy", "ticker", "strategy_score",
        "reference_close", "stop_price", "target_price", "shadow_status",
        "market_data_source", "broker_execution_enabled", "production_applied",
    ]
    for optional in ("vote_count", "market_regime_state", "theme", "theme_state", "final_decision"):
        if optional in out:
            columns.append(optional)
    return out[columns].sort_values(["strategy", "strategy_score", "ticker"], ascending=[True, False, True]).reset_index(drop=True)


def freeze_cohort(ledger: dict[str, dict[str, Any]], signals: pd.DataFrame, observed_at: str) -> dict[str, dict[str, Any]]:
    """Append unseen signal ids only; same-session reruns cannot rewrite history."""
    result = dict(ledger)
    for row in signals.to_dict("records"):
        signal_id = str(row["signal_id"])
        if signal_id in result:
            continue
        result[signal_id] = {
            **{k: (None if pd.isna(v) else v) for k, v in row.items()},
            "observed_at_utc": observed_at,
            "status": "PENDING_ENTRY",
            "entry_session": None,
            "entry_price": None,
            "exit_session": None,
            "exit_price": None,
            "exit_reason": None,
            "sessions_held": 0,
            "mfe_pct": None,
            "mae_pct": None,
            "return_1d_pct": None,
            "return_3d_pct": None,
            "return_5d_pct": None,
            "return_7d_pct": None,
            "hit_5pct": False,
            "hit_10pct": False,
            "hit_15pct": False,
            "r_multiple": None,
        }
    return result


def _sessions_after(frame: pd.DataFrame, session: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    data = frame.copy()
    dates = pd.to_datetime(data.index, utc=True, errors="coerce").date
    return data.loc[pd.Series(dates, index=data.index).astype(str).gt(str(session))].head(MAX_HOLD_SESSIONS)


def resolve_ledger(ledger: dict[str, dict[str, Any]], histories: Mapping[str, pd.DataFrame]) -> dict[str, dict[str, Any]]:
    """Resolve future sessions only; a same-bar stop/target collision is a stop."""
    result = {key: dict(value) for key, value in ledger.items()}
    for record in result.values():
        if record.get("status") in {"CLOSED", "EXPIRED"}:
            continue
        future = _sessions_after(histories.get(str(record["ticker"]), pd.DataFrame()), str(record["as_of_session"]))
        if future.empty:
            continue
        entry = float(future["Open"].iloc[0])
        stop_reference = float(record["stop_price"])
        reference_close = float(record["reference_close"])
        # Preserve the signal's percentage risk when the actual next-open entry differs.
        risk_pct = max((reference_close-stop_reference)/reference_close, .005)
        stop = entry*(1-risk_pct)
        target = entry*(1+TARGET_PCT/100)
        record["status"] = "OPEN"
        record["entry_session"] = str(pd.Timestamp(future.index[0]).date())
        record["entry_price"] = round(entry, 4)
        highs = pd.to_numeric(future["High"], errors="coerce")
        lows = pd.to_numeric(future["Low"], errors="coerce")
        closes = pd.to_numeric(future["Close"], errors="coerce")
        record["sessions_held"] = int(len(future))
        record["mfe_pct"] = round((highs.max()/entry-1)*100, 4)
        record["mae_pct"] = round((lows.min()/entry-1)*100, 4)
        for horizon in (1, 3, 5, 7):
            if len(future) >= horizon:
                record[f"return_{horizon}d_pct"] = round((closes.iloc[horizon-1]/entry-1)*100, 4)
        record["hit_5pct"] = bool(highs.max() >= entry*1.05)
        record["hit_10pct"] = bool(highs.max() >= entry*1.10)
        record["hit_15pct"] = bool(highs.max() >= entry*1.15)
        for timestamp, bar in future.iterrows():
            # Daily data cannot determine intrabar ordering; conservative stop-first.
            if float(bar["Low"]) <= stop:
                exit_price, reason = stop, "STOP"
            elif float(bar["High"]) >= target:
                exit_price, reason = target, "TARGET_8"
            else:
                continue
            record["status"] = "CLOSED"
            record["exit_session"] = str(pd.Timestamp(timestamp).date())
            record["exit_price"] = round(exit_price, 4)
            record["exit_reason"] = reason
            break
        if record["status"] == "OPEN" and len(future) >= MAX_HOLD_SESSIONS:
            record["status"] = "EXPIRED"
            record["exit_session"] = str(pd.Timestamp(future.index[-1]).date())
            record["exit_price"] = round(float(closes.iloc[-1]), 4)
            record["exit_reason"] = "TIME_7D"
        if record["status"] in {"CLOSED", "EXPIRED"}:
            risk_dollars = entry-stop
            record["r_multiple"] = round((float(record["exit_price"])-entry)/risk_dollars, 4)
    return result


def performance_table(ledger: Mapping[str, Mapping[str, Any]]) -> pd.DataFrame:
    rows = []
    records = list(ledger.values())
    for strategy in ALL_MODELS:
        subset = [r for r in records if r.get("strategy") == strategy]
        closed = [r for r in subset if r.get("status") in {"CLOSED", "EXPIRED"} and r.get("r_multiple") is not None]
        rs = np.array([float(r["r_multiple"]) for r in closed], dtype=float)
        gross_profit = float(rs[rs > 0].sum()) if len(rs) else 0.0
        gross_loss = abs(float(rs[rs < 0].sum())) if len(rs) else 0.0
        pf = gross_profit/gross_loss if gross_loss else (math.inf if gross_profit else 0.0)
        expectancy = float(rs.mean()) if len(rs) else 0.0
        eligible = len(closed) >= MIN_CLOSED_TRADES and expectancy >= .25 and pf >= 1.20
        rows.append({
            "strategy": strategy, "signals": len(subset),
            "pending_or_open": sum(r.get("status") in {"PENDING_ENTRY", "OPEN"} for r in subset),
            "closed": len(closed), "win_rate_pct": round(float((rs > 0).mean()*100), 2) if len(rs) else 0.0,
            "expectancy_r": round(expectancy, 4), "profit_factor": round(pf, 4) if math.isfinite(pf) else "INF",
            "hit_5pct": sum(bool(r.get("hit_5pct")) for r in closed),
            "hit_10pct": sum(bool(r.get("hit_10pct")) for r in closed),
            "hit_15pct": sum(bool(r.get("hit_15pct")) for r in closed),
            "manual_review_eligible": bool(eligible), "production_applied": False,
        })
    return pd.DataFrame(rows)


def run(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    source = read_dataset("all_candidates")
    session = str(expected_market_data_session(pd.Timestamp(now)))
    signals = build_signals(source, session)
    if signals.empty:
        raise RuntimeError("QUANT_SHADOW_NO_ELIGIBLE_SIGNALS")
    stored = read_state("quant_shadow", "ledger", default={}) or {}
    ledger = freeze_cohort(dict(stored.get("records", stored)), signals, now.isoformat())
    # Publication mirrors the frozen cohort, not a same-session rerun's newly
    # calculated ranks.  This preserves the point-in-time evidence contract.
    frozen_rows = [r for r in ledger.values() if str(r.get("as_of_session")) == session]
    frozen_signals = pd.DataFrame(frozen_rows)
    signal_columns = list(signals.columns)
    frozen_signals = frozen_signals.reindex(columns=signal_columns)
    tickers = sorted({str(r["ticker"]) for r in ledger.values() if r.get("status") not in {"CLOSED", "EXPIRED"}})
    histories = warehouse_frames(tickers, period="1mo", interval="1d", require_complete=False) if tickers else {}
    ledger = resolve_ledger(ledger, histories)
    performance = performance_table(ledger)
    payload = {"schema_version": 1, "records": ledger}
    append_state("quant_shadow", "ledger", payload)
    write_dataset("quant_shadow_signals", frozen_signals.sort_values(
        ["strategy", "strategy_score", "ticker"], ascending=[True, False, True]), entity_key="ticker")
    write_dataset("quant_shadow_ledger", pd.DataFrame(ledger.values()).sort_values(
        ["as_of_session", "strategy", "ticker"]), entity_key="signal_id")
    write_dataset("quant_shadow_performance", performance, entity_key="strategy")
    health = {
        "status": "COLLECTING" if not performance["manual_review_eligible"].any() else "REVIEW_REQUIRED",
        "generated_at_utc": now.isoformat(), "as_of_session": session,
        "market_data_source": PROVENANCE, "shadow_only": True,
        "broker_execution_enabled": False, "production_applied": False,
        "v3_production_primary": True, "strategies": list(STRATEGIES)+[ENSEMBLE],
        "signals_this_session": int(len(frozen_signals)), "ledger_records": int(len(ledger)),
        "closed_records": int(performance["closed"].sum()),
        "minimum_closed_per_strategy": MIN_CLOSED_TRADES,
        "automatic_promotion_allowed": False,
        "promotion_contract": "manual review only after >=30 closed, expectancy >=0.25R, profit factor >=1.20",
    }
    write_record("quant_shadow_health", health)
    return health


def main() -> None:
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
