from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import math

import numpy as np
import pandas as pd
import yfinance as yf

from .config import OUTPUT_DIR

ET = ZoneInfo("America/New_York")
LEDGER = OUTPUT_DIR / "order_flow_strategy_journal.csv"
SUMMARY = OUTPUT_DIR / "order_flow_strategy_performance.csv"


def _read(name: str) -> pd.DataFrame:
    p = OUTPUT_DIR / name
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _num(v, default=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _signal_id(row) -> str:
    ticker = str(row.get("ticker", ""))
    entry = _num(row.get("strategy_entry"))
    stop = _num(row.get("strategy_stop"))
    return f"{ticker}|{entry:.4f}|{stop:.4f}" if np.isfinite(entry) and np.isfinite(stop) else ticker


def _update_open(row: pd.Series, now_et: datetime) -> pd.Series:
    if str(row.get("status", "OPEN")) != "OPEN":
        return row

    ticker = str(row["ticker"])
    entry = _num(row.get("entry_price"))
    stop = _num(row.get("stop_price"))
    target1 = _num(row.get("target1_price"))
    target2 = _num(row.get("target2_price"))
    selected_at = pd.to_datetime(row.get("signal_at_et"), errors="coerce", utc=True)

    if not np.isfinite(entry) or entry <= 0:
        return row

    try:
        hist = yf.download(
            ticker, period="10d", interval="5m", auto_adjust=False,
            progress=False, prepost=True, threads=False
        )
    except Exception:
        hist = pd.DataFrame()

    if hist is None or hist.empty:
        return row

    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)

    idx = pd.DatetimeIndex(hist.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    hist = hist.copy()
    hist.index = idx

    if pd.notna(selected_at):
        hist = hist[hist.index >= selected_at]

    if hist.empty or "High" not in hist or "Low" not in hist:
        return row

    highs = pd.to_numeric(hist["High"], errors="coerce")
    lows = pd.to_numeric(hist["Low"], errors="coerce")

    max_seen = float(highs.max()) if highs.notna().any() else entry
    min_seen = float(lows.min()) if lows.notna().any() else entry
    row["max_price_seen"] = round(max_seen, 4)
    row["min_price_seen"] = round(min_seen, 4)
    row["mfe_pct"] = round((max_seen / entry - 1) * 100, 2)
    row["mae_pct"] = round((min_seen / entry - 1) * 100, 2)

    target1_hit = np.isfinite(target1) and bool((highs >= target1).any())
    target2_hit = np.isfinite(target2) and bool((highs >= target2).any())
    stop_hit = np.isfinite(stop) and bool((lows <= stop).any())

    row["target1_hit"] = bool(row.get("target1_hit", False) or target1_hit)
    row["target2_hit"] = bool(row.get("target2_hit", False) or target2_hit)

    t_stop = hist.index[lows <= stop][0] if stop_hit else None
    t_t2 = hist.index[highs >= target2][0] if target2_hit else None

    # Conservative outcome: stop wins if stop and T2 are reached in same/earlier bar.
    if stop_hit and (not target2_hit or t_stop <= t_t2):
        row["status"] = "STOP_HIT"
        row["outcome_at_et"] = str(t_stop)
        row["exit_price"] = round(stop, 4)
    elif target2_hit:
        row["status"] = "TARGET2_HIT"
        row["outcome_at_et"] = str(t_t2)
        row["exit_price"] = round(target2, 4)
    else:
        signal_date = pd.to_datetime(row.get("signal_date_et"), errors="coerce")
        today = pd.Timestamp(now_et.date())
        if pd.notna(signal_date):
            business_days = int(np.busday_count(signal_date.date(), today.date()))
            row["business_days_open"] = max(0, business_days)
            if business_days >= 7:
                close = pd.to_numeric(hist.get("Close"), errors="coerce").dropna()
                exit_px = float(close.iloc[-1]) if len(close) else entry
                row["status"] = "EXPIRED"
                row["outcome_at_et"] = now_et.isoformat()
                row["exit_price"] = round(exit_px, 4)

    if str(row.get("status")) != "OPEN":
        exit_px = _num(row.get("exit_price"))
        ret = (exit_px / entry - 1) * 100 if np.isfinite(exit_px) else np.nan
        risk = entry - stop if np.isfinite(stop) else np.nan
        row["return_pct"] = round(ret, 2) if np.isfinite(ret) else np.nan
        row["r_multiple"] = round((exit_px - entry) / risk, 2) if np.isfinite(risk) and risk > 0 and np.isfinite(exit_px) else np.nan

    return row


def run() -> pd.DataFrame:
    now_et = datetime.now(timezone.utc).astimezone(ET)
    signals = _read("order_flow_strategy.csv")
    journal = _read("order_flow_strategy_journal.csv")

    if journal.empty:
        journal = pd.DataFrame(columns=[
            "signal_id","signal_date_et","signal_at_et","ticker","theme","source_signal",
            "entry_price","stop_price","target1_price","target2_price","risk_pct","rr_to_t2",
            "order_flow_strategy_score","order_flow_score","order_flow_state","buy_pressure_pct",
            "sell_pressure_pct","volume_imbalance_proxy","intraday_rvol","theme_rotation_score",
            "rel_vs_spy_pct","reason","status","outcome_at_et","exit_price","return_pct",
            "r_multiple","target1_hit","target2_hit","max_price_seen","min_price_seen","mfe_pct",
            "mae_pct","business_days_open"
        ])

    if not journal.empty:
        journal = pd.DataFrame([_update_open(r.copy(), now_et) for _, r in journal.iterrows()])

    existing_ids = set(journal.get("signal_id", pd.Series(dtype=str)).astype(str))

    if not signals.empty and "order_flow_strategy_signal" in signals.columns:
        buys = signals[signals["order_flow_strategy_signal"].astype(str).eq("ORDER FLOW BUY")].copy()
        for _, r in buys.iterrows():
            sid = _signal_id(r)
            if sid in existing_ids:
                continue
            row = {
                "signal_id": sid,
                "signal_date_et": now_et.date().isoformat(),
                "signal_at_et": now_et.isoformat(timespec="seconds"),
                "ticker": str(r.get("ticker","")),
                "theme": str(r.get("theme","")),
                "source_signal": "ORDER FLOW BUY",
                "entry_price": _num(r.get("strategy_entry")),
                "stop_price": _num(r.get("strategy_stop")),
                "target1_price": _num(r.get("strategy_target_1")),
                "target2_price": _num(r.get("strategy_target_2")),
                "risk_pct": _num(r.get("strategy_risk_pct")),
                "rr_to_t2": _num(r.get("strategy_rr_to_t2")),
                "order_flow_strategy_score": _num(r.get("order_flow_strategy_score")),
                "order_flow_score": _num(r.get("order_flow_score")),
                "order_flow_state": str(r.get("order_flow_state","")),
                "buy_pressure_pct": _num(r.get("buy_pressure_pct")),
                "sell_pressure_pct": _num(r.get("sell_pressure_pct")),
                "volume_imbalance_proxy": _num(r.get("volume_imbalance_proxy")),
                "intraday_rvol": _num(r.get("intraday_rvol")),
                "theme_rotation_score": _num(r.get("theme_rotation_score")),
                "rel_vs_spy_pct": _num(r.get("rel_vs_spy_pct")),
                "reason": str(r.get("order_flow_strategy_reason","")),
                "status": "OPEN",
                "outcome_at_et": "",
                "exit_price": np.nan,
                "return_pct": np.nan,
                "r_multiple": np.nan,
                "target1_hit": False,
                "target2_hit": False,
                "max_price_seen": _num(r.get("strategy_entry")),
                "min_price_seen": _num(r.get("strategy_entry")),
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "business_days_open": 0,
            }
            journal = pd.concat([journal, pd.DataFrame([row])], ignore_index=True)
            existing_ids.add(sid)

    journal = journal.sort_values(["signal_date_et","signal_at_et"], ascending=[False,False])
    journal.to_csv(LEDGER, index=False)

    closed = journal[journal["status"].astype(str).isin(["TARGET2_HIT","STOP_HIT","EXPIRED"])] if not journal.empty else pd.DataFrame()
    wins = closed["status"].astype(str).eq("TARGET2_HIT") if not closed.empty else pd.Series(dtype=bool)

    perf = pd.DataFrame([{
        "signals": len(journal),
        "open_signals": int(journal["status"].astype(str).eq("OPEN").sum()) if not journal.empty else 0,
        "closed_signals": len(closed),
        "target1_hit_rate_pct": round(float(closed["target1_hit"].fillna(False).astype(bool).mean()*100),1) if len(closed) else np.nan,
        "target2_hit_rate_pct": round(float(closed["target2_hit"].fillna(False).astype(bool).mean()*100),1) if len(closed) else np.nan,
        "stop_rate_pct": round(float(closed["status"].astype(str).eq("STOP_HIT").mean()*100),1) if len(closed) else np.nan,
        "win_rate_pct": round(float(wins.mean()*100),1) if len(closed) else np.nan,
        "avg_return_pct": round(float(pd.to_numeric(closed["return_pct"], errors="coerce").mean()),2) if len(closed) else np.nan,
        "avg_r_multiple": round(float(pd.to_numeric(closed["r_multiple"], errors="coerce").mean()),2) if len(closed) else np.nan,
        "avg_mfe_pct": round(float(pd.to_numeric(closed["mfe_pct"], errors="coerce").mean()),2) if len(closed) else np.nan,
        "avg_mae_pct": round(float(pd.to_numeric(closed["mae_pct"], errors="coerce").mean()),2) if len(closed) else np.nan,
        "updated_at_et": now_et.isoformat(timespec="seconds"),
        "mode": "ORDER_FLOW_FORWARD_VALIDATION",
    }])
    perf.to_csv(SUMMARY, index=False)
    return journal


if __name__ == "__main__":
    j = run()
    print(j.head(20).to_string(index=False) if not j.empty else "No order-flow BUY signals logged yet.")
