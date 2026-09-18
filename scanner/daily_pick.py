from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from .warehouse import DataRequirement, provide

from .config import OUTPUT_DIR

ET = ZoneInfo("America/New_York")
LOG_PATH = OUTPUT_DIR / "daily_top_pick_log.csv"
SUMMARY_PATH = OUTPUT_DIR / "daily_top_pick_summary.csv"

LOG_COLUMNS = [
    "selection_date_et","selected_at_et","ticker","source","selection_score","theme",
    "entry_price","stop_price","target_price","target_pct","risk_pct",
    "market_hunt_score","live_confirmation_score","theme_rotation_score",
    "rel_vs_spy_pct","intraday_rvol","day_change_pct","reason",
    "status","outcome_at_et","exit_price","return_pct","r_multiple",
    "max_price_seen","min_price_seen","mfe_pct","mae_pct","business_days_open",
    "target_hit","stop_hit","expired"
]


def _num(value, default=np.nan):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read(name: str) -> pd.DataFrame:
    path = OUTPUT_DIR / name
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def _pick_candidate(momentum: pd.DataFrame, live: pd.DataFrame) -> dict | None:
    rows = []

    if not momentum.empty and "ticker" in momentum.columns:
        m = momentum.copy()
        signal = m.get("signal", pd.Series("", index=m.index)).astype(str)
        m = m[signal.eq("MOMENTUM BUY")].copy()
        if not m.empty:
            m["price_num"] = pd.to_numeric(m.get("price"), errors="coerce")
            m["rvol_num"] = pd.to_numeric(m.get("intraday_rvol"), errors="coerce").fillna(0)
            m["rel_num"] = pd.to_numeric(m.get("rel_vs_spy_pct"), errors="coerce").fillna(0)
            m["rotation_num"] = pd.to_numeric(m.get("theme_rotation_score"), errors="coerce").fillna(0)
            m["day_num"] = pd.to_numeric(m.get("day_change_pct"), errors="coerce").fillna(0)
            m["risk_num"] = pd.to_numeric(m.get("risk_pct"), errors="coerce")
            m = m[m["price_num"].gt(0) & m["day_num"].lt(8.0)]
            for _, r in m.iterrows():
                score = (
                    min(_num(r.get("rotation_num"), 0), 100) * 0.35
                    + min(max(_num(r.get("rel_num"), 0), 0), 10) * 3.0
                    + min(max(_num(r.get("rvol_num"), 0), 0), 5) * 5.0
                    + 20
                )
                risk = _num(r.get("risk_num"))
                if np.isfinite(risk) and risk > 4.0:
                    score -= 15
                rows.append({
                    "ticker": str(r["ticker"]),
                    "source": "FAST_MOMENTUM",
                    "selection_score": score,
                    "theme": str(r.get("theme", "")),
                    "entry_price": _num(r.get("price")),
                    "stop_price": _num(r.get("stop")),
                    "target_price": _num(r.get("target_8pct")),
                    "market_hunt_score": np.nan,
                    "live_confirmation_score": np.nan,
                    "theme_rotation_score": _num(r.get("theme_rotation_score")),
                    "rel_vs_spy_pct": _num(r.get("rel_vs_spy_pct")),
                    "intraday_rvol": _num(r.get("intraday_rvol")),
                    "day_change_pct": _num(r.get("day_change_pct")),
                    "reason": str(r.get("reason", "Momentum BUY + sector rotation + VWAP/ORB/RVOL")),
                })

    if not live.empty and "ticker" in live.columns:
        l = live.copy()
        l["price_num"] = pd.to_numeric(l.get("live_price", l.get("price")), errors="coerce")
        l["confirm_num"] = pd.to_numeric(l.get("live_confirmation_score"), errors="coerce").fillna(0)
        l["score_num"] = pd.to_numeric(l.get("market_hunt_score"), errors="coerce").fillna(0)
        l["rvol_num"] = pd.to_numeric(l.get("intraday_rvol"), errors="coerce").fillna(0)
        state = l.get("monitor_state", pd.Series("", index=l.index)).astype(str)
        action = l.get("live_trade_action", pd.Series("", index=l.index)).astype(str)
        l = l[(state.eq("LIVE_CONFIRMED") | action.str.startswith("BUY / LIVE CONFIRMED")) & l["price_num"].gt(0)]
        for _, r in l.iterrows():
            score = (
                min(_num(r.get("confirm_num"), 0), 100) * 0.45
                + min(_num(r.get("score_num"), 0), 100) * 0.35
                + min(max(_num(r.get("rvol_num"), 0), 0), 5) * 4
            )
            rows.append({
                "ticker": str(r["ticker"]),
                "source": "LIVE_MONITOR",
                "selection_score": score,
                "theme": str(r.get("theme", "")),
                "entry_price": _num(r.get("live_price", r.get("price"))),
                "stop_price": _num(r.get("stop")),
                "target_price": _num(r.get("target_8", r.get("effective_target"))),
                "market_hunt_score": _num(r.get("market_hunt_score")),
                "live_confirmation_score": _num(r.get("live_confirmation_score")),
                "theme_rotation_score": _num(r.get("theme_score")),
                "rel_vs_spy_pct": _num(r.get("rs20_vs_spy")),
                "intraday_rvol": _num(r.get("intraday_rvol")),
                "day_change_pct": np.nan,
                "reason": str(r.get("live_trade_action", "Live-confirmed setup")),
            })

    if not rows:
        return None

    ranked = pd.DataFrame(rows)
    ranked = ranked.sort_values(["selection_score","intraday_rvol"], ascending=[False,False])
    ranked = ranked.drop_duplicates("ticker", keep="first")
    return ranked.iloc[0].to_dict()


def _business_days_open(selection_date: str, today_et: str) -> int:
    try:
        a = np.datetime64(selection_date)
        b = np.datetime64(today_et)
        return max(0, int(np.busday_count(a, b)))
    except Exception:
        return 0


def _monitor_row(row: pd.Series, now_et: datetime) -> pd.Series:
    if str(row.get("status", "OPEN")) != "OPEN":
        return row

    ticker = str(row["ticker"])
    entry = _num(row.get("entry_price"))
    stop = _num(row.get("stop_price"))
    target = _num(row.get("target_price"))
    selected_at = pd.to_datetime(row.get("selected_at_et"), errors="coerce", utc=True)

    if not np.isfinite(entry) or entry <= 0:
        return row

    try:
        view=provide(DataRequirement(consumer="daily_pick_monitor",tickers=(ticker,),interval="5m",period="10d",max_age_minutes=10,view_name="daily_pick_monitor_5m"))
        hist=view.frame.copy()
        hist["bar_timestamp"]=pd.to_datetime(hist["bar_timestamp"],utc=True,errors="coerce")
        hist=hist.dropna(subset=["bar_timestamp"]).set_index("bar_timestamp")
    except Exception:
        hist=pd.DataFrame()


    if hist is not None and not hist.empty:
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        idx = hist.index
        try:
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            else:
                idx = idx.tz_convert("UTC")
            hist = hist.copy()
            hist.index = idx
        except Exception:
            pass
        if pd.notna(selected_at):
            try:
                hist = hist[hist.index >= selected_at]
            except Exception:
                pass

    days_open = _business_days_open(str(row.get("selection_date_et")), now_et.date().isoformat())
    row["business_days_open"] = days_open

    if hist is None or hist.empty or "High" not in hist or "Low" not in hist:
        if days_open >= 7:
            row["status"] = "EXPIRED"
            row["expired"] = True
            row["outcome_at_et"] = now_et.isoformat()
        return row

    highs = pd.to_numeric(hist["High"], errors="coerce")
    lows = pd.to_numeric(hist["Low"], errors="coerce")
    max_seen = float(highs.max()) if highs.notna().any() else entry
    min_seen = float(lows.min()) if lows.notna().any() else entry
    row["max_price_seen"] = round(max_seen, 4)
    row["min_price_seen"] = round(min_seen, 4)
    row["mfe_pct"] = round((max_seen / entry - 1) * 100, 2)
    row["mae_pct"] = round((min_seen / entry - 1) * 100, 2)

    target_hit = np.isfinite(target) and bool((highs >= target).any())
    stop_hit = np.isfinite(stop) and bool((lows <= stop).any())

    exit_price = np.nan
    status = "OPEN"
    outcome_time = ""

    if target_hit or stop_hit:
        target_times = hist.index[highs >= target] if target_hit else []
        stop_times = hist.index[lows <= stop] if stop_hit else []
        t_target = target_times[0] if target_hit else None
        t_stop = stop_times[0] if stop_hit else None

        # Conservative tie handling: if both occur in the same/unknown bar, stop wins.
        if stop_hit and (not target_hit or t_stop <= t_target):
            status = "STOP_HIT"
            exit_price = stop
            outcome_time = str(t_stop)
        else:
            status = "TARGET_HIT"
            exit_price = target
            outcome_time = str(t_target)
    elif days_open >= 7:
        status = "EXPIRED"
        exit_price = _num(pd.to_numeric(hist["Close"], errors="coerce").dropna().iloc[-1], entry)
        outcome_time = now_et.isoformat()

    row["target_hit"] = status == "TARGET_HIT"
    row["stop_hit"] = status == "STOP_HIT"
    row["expired"] = status == "EXPIRED"

    if status != "OPEN":
        row["status"] = status
        row["outcome_at_et"] = outcome_time
        row["exit_price"] = round(exit_price, 4) if np.isfinite(exit_price) else np.nan
        ret = (exit_price / entry - 1) * 100 if np.isfinite(exit_price) else np.nan
        row["return_pct"] = round(ret, 2) if np.isfinite(ret) else np.nan
        risk_dollars = entry - stop if np.isfinite(stop) else np.nan
        row["r_multiple"] = round((exit_price - entry) / risk_dollars, 2) if np.isfinite(risk_dollars) and risk_dollars > 0 and np.isfinite(exit_price) else np.nan

    return row


def run() -> pd.DataFrame:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now_et = _now_et()
    today = now_et.date().isoformat()

    log = _read("daily_top_pick_log.csv")
    if log.empty:
        log = pd.DataFrame(columns=LOG_COLUMNS)

    # First update any existing open picks.
    if not log.empty:
        updated = []
        for _, row in log.iterrows():
            updated.append(_monitor_row(row.copy(), now_et))
        log = pd.DataFrame(updated)

    # Exactly one immutable selection per ET trading date.
    already_selected = (
        not log.empty
        and "selection_date_et" in log.columns
        and log["selection_date_et"].astype(str).eq(today).any()
    )

    # Select only on weekdays and during/after regular-session open.
    is_weekday = now_et.weekday() < 5
    selection_window_open = (now_et.hour, now_et.minute) >= (9, 45)

    if not already_selected and is_weekday and selection_window_open:
        momentum = _read("momentum_signals.csv")
        live = _read("intraday_live.csv")
        candidate = _pick_candidate(momentum, live)
        if candidate:
            entry = _num(candidate["entry_price"])
            stop = _num(candidate.get("stop_price"))
            if not np.isfinite(stop) or stop <= 0 or stop >= entry:
                stop = entry * 0.97
            target = _num(candidate.get("target_price"))
            if not np.isfinite(target) or target <= entry:
                target = entry * 1.08
            risk_pct = (entry - stop) / entry * 100
            row = {
                **candidate,
                "selection_date_et": today,
                "selected_at_et": now_et.isoformat(),
                "entry_price": round(entry, 4),
                "stop_price": round(stop, 4),
                "target_price": round(target, 4),
                "target_pct": round((target / entry - 1) * 100, 2),
                "risk_pct": round(risk_pct, 2),
                "status": "OPEN",
                "outcome_at_et": "",
                "exit_price": np.nan,
                "return_pct": np.nan,
                "r_multiple": np.nan,
                "max_price_seen": entry,
                "min_price_seen": entry,
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "business_days_open": 0,
                "target_hit": False,
                "stop_hit": False,
                "expired": False,
            }
            log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)

    for col in LOG_COLUMNS:
        if col not in log.columns:
            log[col] = np.nan
    log = log[LOG_COLUMNS].sort_values(["selection_date_et","selected_at_et"], ascending=[False,False])
    log.to_csv(LOG_PATH, index=False)

    closed = log[log["status"].astype(str).isin(["TARGET_HIT","STOP_HIT","EXPIRED"])] if not log.empty else pd.DataFrame()
    summary = pd.DataFrame([{
        "total_daily_picks": len(log),
        "open_picks": int(log["status"].astype(str).eq("OPEN").sum()) if not log.empty else 0,
        "closed_picks": len(closed),
        "target_hits": int(closed["status"].astype(str).eq("TARGET_HIT").sum()) if not closed.empty else 0,
        "stop_hits": int(closed["status"].astype(str).eq("STOP_HIT").sum()) if not closed.empty else 0,
        "expired": int(closed["status"].astype(str).eq("EXPIRED").sum()) if not closed.empty else 0,
        "win_rate_pct": round(float(closed["status"].astype(str).eq("TARGET_HIT").mean() * 100), 1) if len(closed) else np.nan,
        "avg_return_pct": round(float(pd.to_numeric(closed["return_pct"], errors="coerce").mean()), 2) if len(closed) else np.nan,
        "avg_r_multiple": round(float(pd.to_numeric(closed["r_multiple"], errors="coerce").mean()), 2) if len(closed) else np.nan,
        "updated_at_et": now_et.isoformat(),
        "selection_policy": "ONE_TOP_CONVICTION_PAPER_PICK_PER_ET_TRADING_DAY",
    }])
    summary.to_csv(SUMMARY_PATH, index=False)
    return log


if __name__ == "__main__":
    frame = run()
    if frame.empty:
        print("No daily top-conviction paper pick selected yet.")
    else:
        print(frame.head(10).to_string(index=False))
