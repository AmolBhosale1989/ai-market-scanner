from __future__ import annotations

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from .control_plane import read_dataset, write_dataset

NY = ZoneInfo("America/New_York")


def _read(name: str) -> pd.DataFrame:
    return read_dataset(name,required=False)


def run() -> pd.DataFrame:
    src = _read("momentum_signals")
    momentum_health = _read("momentum_health")
    session_date = ""
    expected_inputs = 0
    if not momentum_health.empty:
        session_date = str(momentum_health.iloc[-1].get("session_date", "")).strip()
        parsed_inputs = pd.to_numeric(momentum_health.iloc[-1].get("leaders_evaluated", 0), errors="coerce")
        expected_inputs = int(parsed_inputs) if pd.notna(parsed_inputs) else 0
    now = datetime.now(NY)
    if src.empty:
        write_dataset("order_flow_strategy",pd.DataFrame())
        health=pd.DataFrame([{
            "updated_at_et": now.isoformat(timespec="seconds"),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_date": session_date,
            "expected_inputs": expected_inputs,
            "evaluated": 0,
            "buy_signals": 0,
            "watch_signals": 0,
            "avoid_signals": 0,
            "mode": "ORDER_FLOW_PROXY_STRATEGY",
        }])
        write_dataset("order_flow_strategy_health",health,entity_key=None)
        return pd.DataFrame()

    d = src.copy()
    for c in [
        "price","day_change_pct","rel_vs_spy_pct","theme_rotation_score","intraday_rvol",
        "vwap","opening_range_high","stop","risk_pct","order_flow_score","buy_pressure_pct",
        "sell_pressure_pct","volume_imbalance_proxy","volume_impulse","vwap_pressure"
    ]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")

    price = d["price"]
    flow = d.get("order_flow_score", pd.Series(0, index=d.index)).fillna(0)
    buy_pressure = d.get("buy_pressure_pct", pd.Series(50, index=d.index)).fillna(50)
    imbalance = d.get("volume_imbalance_proxy", pd.Series(0, index=d.index)).fillna(0)
    impulse = d.get("volume_impulse", pd.Series(1, index=d.index)).fillna(1)
    vwap_pressure = d.get("vwap_pressure", pd.Series(0, index=d.index)).fillna(0)
    rvol = d.get("intraday_rvol", pd.Series(0, index=d.index)).fillna(0)
    rel = d.get("rel_vs_spy_pct", pd.Series(0, index=d.index)).fillna(0)
    rotation = d.get("theme_rotation_score", pd.Series(0, index=d.index)).fillna(0)
    broad_score = d.get("broad_breakout_score", pd.Series(0, index=d.index)).fillna(0)
    source = d.get("candidate_source", pd.Series("THEME_ROTATION", index=d.index)).astype(str)
    day = d.get("day_change_pct", pd.Series(0, index=d.index)).fillna(0)
    above_vwap = d.get("above_vwap", pd.Series(False, index=d.index)).astype(bool)
    above_or = d.get("above_or_high", pd.Series(False, index=d.index)).astype(bool)
    risk = d.get("risk_pct", pd.Series(math.nan, index=d.index))

    accumulation = (
        flow.ge(65)
        & buy_pressure.ge(58)
        & imbalance.ge(5)
        & rvol.ge(1.25)
        & above_vwap
    )
    themed_breakout_confirmation = (
        flow.ge(70)
        & buy_pressure.ge(60)
        & rvol.ge(1.5)
        & rel.ge(1.0)
        & rotation.ge(70)
        & (above_or | vwap_pressure.ge(0.4))
    )
    broad_breakout_confirmation = (
        source.eq("BROAD_BREAKOUT")
        & broad_score.ge(65)
        & flow.ge(72)
        & buy_pressure.ge(60)
        & rvol.ge(1.5)
        & rel.ge(1.5)
        & (above_or | vwap_pressure.ge(0.5))
    )
    breakout_confirmation = themed_breakout_confirmation | broad_breakout_confirmation
    absorption_watch = (
        flow.between(55, 69.999)
        & buy_pressure.ge(55)
        & impulse.ge(1.1)
        & above_vwap
        & rel.ge(0.5)
    )
    distribution = (
        flow.le(38)
        | buy_pressure.le(42)
        | imbalance.le(-10)
    )
    extended = day.gt(8.0)
    risk_ok = risk.between(0.3, 4.0)

    score = (
        flow * 0.38
        + buy_pressure.clip(0, 100) * 0.16
        + rotation.clip(0, 100) * 0.12
        + broad_score.clip(0, 100) * 0.06
        + rel.clip(lower=0, upper=10) * 1.5
        + rvol.clip(lower=0, upper=5) * 4
        + impulse.clip(lower=0, upper=3) * 3
    ).clip(0, 100)

    signal = pd.Series("NO TRADE", index=d.index, dtype=object)
    setup = pd.Series("NONE", index=d.index, dtype=object)
    reason = pd.Series("Order-flow conditions incomplete", index=d.index, dtype=object)

    signal.loc[distribution] = "AVOID / SELLING PRESSURE"
    setup.loc[distribution] = "DISTRIBUTION"
    reason.loc[distribution] = "Selling pressure / negative imbalance dominates"

    watch_mask = ~distribution & absorption_watch
    signal.loc[watch_mask] = "WATCH / ACCUMULATION"
    setup.loc[watch_mask] = "ACCUMULATION"
    reason.loc[watch_mask] = "Positive pressure developing above VWAP with volume participation"

    buy_mask = ~distribution & breakout_confirmation & accumulation & risk_ok & ~extended
    signal.loc[buy_mask] = "ORDER FLOW BUY"
    setup.loc[buy_mask] = "BUYING PRESSURE BREAKOUT"
    reason.loc[buy_mask] = "Strong buying pressure + RVOL + relative strength + theme rotation or broad-breakout confirmation + VWAP/ORB confirmation"

    ext_mask = ~distribution & breakout_confirmation & extended
    signal.loc[ext_mask] = "EXTENDED / WAIT RETEST"
    setup.loc[ext_mask] = "EXTENDED BUYING PRESSURE"
    reason.loc[ext_mask] = "Strong order flow but price already extended >8%; wait for controlled retest"

    out = d.copy()
    out["order_flow_strategy_score"] = score.round(1)
    out["order_flow_strategy_signal"] = signal
    out["order_flow_setup"] = setup
    out["order_flow_strategy_reason"] = reason

    out["strategy_entry"] = price.round(2)
    stop = pd.to_numeric(out.get("stop"), errors="coerce")
    fallback_stop = price * 0.97
    valid_stop = stop.where(stop.gt(0) & stop.lt(price), fallback_stop)
    out["strategy_stop"] = valid_stop.round(2)
    out["strategy_target_1"] = (price * 1.05).round(2)
    out["strategy_target_2"] = (price * 1.08).round(2)
    out["strategy_risk_pct"] = ((price / valid_stop - 1) * 100).round(2)
    out["strategy_rr_to_t2"] = (((price * 1.08) - price) / (price - valid_stop)).replace([math.inf, -math.inf], math.nan).round(2)
    out["order_flow_mode"] = out.get("order_flow_mode", "BAR_PROXY")

    rank = {
        "ORDER FLOW BUY": 0,
        "WATCH / ACCUMULATION": 1,
        "EXTENDED / WAIT RETEST": 2,
        "NO TRADE": 3,
        "AVOID / SELLING PRESSURE": 4,
    }
    out["_rank"] = out["order_flow_strategy_signal"].map(rank).fillna(9)
    out = out.sort_values(
        ["_rank", "order_flow_strategy_score", "theme_rotation_score", "rel_vs_spy_pct"],
        ascending=[True, False, False, False],
    ).drop(columns=["_rank"])

    write_dataset("order_flow_strategy",out)
    health=pd.DataFrame([{
        "updated_at_et": now.isoformat(timespec="seconds"),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_date": session_date,
        "expected_inputs": expected_inputs,
        "evaluated": len(out),
        "buy_signals": int(out["order_flow_strategy_signal"].eq("ORDER FLOW BUY").sum()),
        "watch_signals": int(out["order_flow_strategy_signal"].eq("WATCH / ACCUMULATION").sum()),
        "extended_waits": int(out["order_flow_strategy_signal"].eq("EXTENDED / WAIT RETEST").sum()),
        "avoid_signals": int(out["order_flow_strategy_signal"].eq("AVOID / SELLING PRESSURE").sum()),
        "mode": "ORDER_FLOW_PROXY_STRATEGY",
    }])
    write_dataset("order_flow_strategy_health",health,entity_key=None)
    return out


if __name__ == "__main__":
    frame = run()
    if frame.empty:
        print("No order-flow strategy rows available.")
    else:
        cols = [
            "ticker","order_flow_strategy_signal","order_flow_strategy_score","order_flow_setup",
            "price","order_flow_score","buy_pressure_pct","volume_imbalance_proxy","intraday_rvol",
            "theme_rotation_score","rel_vs_spy_pct","strategy_entry","strategy_stop",
            "strategy_target_1","strategy_target_2","strategy_rr_to_t2"
        ]
        print(frame[[c for c in cols if c in frame.columns]].head(30).to_string(index=False))
