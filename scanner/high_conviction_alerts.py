from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from .config import OUTPUT_DIR

NY = ZoneInfo("America/New_York")
LOG = OUTPUT_DIR / "high_conviction_alert_log.csv"
HEALTH = OUTPUT_DIR / "high_conviction_alert_health.csv"


def _read(name: str) -> pd.DataFrame:
    p = OUTPUT_DIR / name
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _num(series, index=None, default=0.0):
    if series is None:
        return pd.Series(default, index=index)
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _send_telegram(message: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return False, "NOT_CONFIGURED"
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": message},
            timeout=15,
        )
        return bool(r.ok), "SENT" if r.ok else f"HTTP_{r.status_code}"
    except Exception as exc:
        return False, type(exc).__name__


def _candidate_rows() -> pd.DataFrame:
    momentum = _read("momentum_signals.csv")
    live = _read("intraday_live.csv")
    rows = []

    if not momentum.empty and "ticker" in momentum.columns:
        m = momentum.copy()
        idx = m.index
        signal = m.get("signal", pd.Series("", index=idx)).astype(str)
        rotation = _num(m.get("theme_rotation_score"), idx)
        rel = _num(m.get("rel_vs_spy_pct"), idx)
        rvol = _num(m.get("intraday_rvol"), idx)
        day = _num(m.get("day_change_pct"), idx)
        price = _num(m.get("price"), idx, np.nan)
        stop = _num(m.get("stop"), idx, np.nan)

        mask = (
            signal.eq("MOMENTUM BUY")
            & rotation.ge(80)
            & rel.ge(1.5)
            & rvol.ge(1.5)
            & day.ge(1.0)
            & day.lt(8.0)
            & price.gt(0)
        )
        for i, r in m[mask].iterrows():
            entry = float(price.loc[i])
            stop_px = float(stop.loc[i]) if np.isfinite(stop.loc[i]) and 0 < stop.loc[i] < entry else entry * 0.97
            target = entry * 1.08
            score = min(float(rotation.loc[i]), 100) * 0.35 + min(float(rel.loc[i]), 10) * 3 + min(float(rvol.loc[i]), 5) * 5 + 20
            rows.append({
                "ticker": str(r.get("ticker", "")),
                "source": "FAST_MOMENTUM",
                "conviction_score": round(score, 1),
                "price": round(entry, 4),
                "entry": round(entry, 4),
                "stop": round(stop_px, 4),
                "target": round(target, 4),
                "theme": str(r.get("theme", "")),
                "rotation_score": round(float(rotation.loc[i]), 1),
                "rel_vs_spy_pct": round(float(rel.loc[i]), 2),
                "intraday_rvol": round(float(rvol.loc[i]), 2),
                "day_change_pct": round(float(day.loc[i]), 2),
                "state": "STRONG BUY",
                "reason": "Momentum BUY + leading rotation + strong relative strength + RVOL",
            })

    if not live.empty and "ticker" in live.columns:
        l = live.copy()
        idx = l.index
        state = l.get("monitor_state", pd.Series("", index=idx)).astype(str)
        action = l.get("live_trade_action", pd.Series("", index=idx)).astype(str)
        conf = _num(l.get("live_confirmation_score"), idx)
        score = _num(l.get("market_hunt_score"), idx)
        rvol = _num(l.get("intraday_rvol"), idx)
        price = _num(l.get("live_price"), idx, np.nan)
        stop = _num(l.get("stop"), idx, np.nan)
        target = _num(l.get("effective_target"), idx, np.nan)

        mask = (
            (state.eq("LIVE_CONFIRMED") | action.str.startswith("BUY / LIVE CONFIRMED"))
            & conf.ge(80)
            & score.ge(75)
            & rvol.ge(1.2)
            & price.gt(0)
        )
        for i, r in l[mask].iterrows():
            entry = float(price.loc[i])
            stop_px = float(stop.loc[i]) if np.isfinite(stop.loc[i]) and 0 < stop.loc[i] < entry else entry * 0.97
            target_px = float(target.loc[i]) if np.isfinite(target.loc[i]) and target.loc[i] > entry else entry * 1.08
            conviction = float(conf.loc[i]) * 0.55 + min(float(score.loc[i]), 100) * 0.35 + min(float(rvol.loc[i]), 5) * 2
            rows.append({
                "ticker": str(r.get("ticker", "")),
                "source": "LIVE_MONITOR",
                "conviction_score": round(conviction, 1),
                "price": round(entry, 4),
                "entry": round(entry, 4),
                "stop": round(stop_px, 4),
                "target": round(target_px, 4),
                "theme": str(r.get("theme", "")),
                "rotation_score": float(pd.to_numeric(pd.Series([r.get("theme_score")]), errors="coerce").fillna(0).iloc[0]),
                "rel_vs_spy_pct": float(pd.to_numeric(pd.Series([r.get("rs20_vs_spy")]), errors="coerce").fillna(0).iloc[0]),
                "intraday_rvol": round(float(rvol.loc[i]), 2),
                "day_change_pct": np.nan,
                "state": "STRONG BUY",
                "reason": str(r.get("live_trade_action", "Live-confirmed high-conviction setup")),
            })

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    return out.sort_values(["conviction_score", "intraday_rvol"], ascending=[False, False]).drop_duplicates("ticker", keep="first")


def run() -> pd.DataFrame:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(NY)
    session_date = now.date().isoformat()
    candidates = _candidate_rows()

    existing = _read("high_conviction_alert_log.csv")
    if existing.empty:
        existing = pd.DataFrame(columns=[
            "session_date_et","alerted_at_et","ticker","source","conviction_score","price","entry","stop","target",
            "theme","rotation_score","rel_vs_spy_pct","intraday_rvol","day_change_pct","state","reason","telegram_sent","telegram_status"
        ])

    sent_count = 0
    new_rows = []
    seen = set(
        zip(
            existing.get("session_date_et", pd.Series(dtype=str)).astype(str),
            existing.get("ticker", pd.Series(dtype=str)).astype(str),
        )
    )

    for _, row in candidates.iterrows():
        key = (session_date, str(row["ticker"]))
        if key in seen:
            continue

        rr = (float(row["target"]) - float(row["entry"])) / max(float(row["entry"]) - float(row["stop"]), 0.0001)
        message = (
            "🚨 MARKET HUNT — HIGH CONVICTION\n\n"
            f"$" + str(row["ticker"]) + f"  |  {row['state']}\n"
            f"Price/Entry: $" + f"{float(row['entry']):.2f}\n"
            f"Stop: $" + f"{float(row['stop']):.2f}\n"
            f"Target: $" + f"{float(row['target']):.2f}\n"
            f"R/R: {rr:.2f}R\n\n"
            f"Theme: {row.get('theme','')}\n"
            f"Rotation: {float(row.get('rotation_score',0)):.0f}/100\n"
            f"Rel vs SPY: {float(row.get('rel_vs_spy_pct',0)):+.2f}%\n"
            f"RVOL: {float(row.get('intraday_rvol',0)):.2f}x\n"
            f"Conviction: {float(row.get('conviction_score',0)):.1f}\n\n"
            f"Why: {row.get('reason','')}\n\n"
            "Paper-validation alert. Research only — not financial advice."
        )
        sent, status = _send_telegram(message)
        sent_count += int(sent)
        rec = row.to_dict()
        rec.update({
            "session_date_et": session_date,
            "alerted_at_et": now.isoformat(timespec="seconds"),
            "telegram_sent": sent,
            "telegram_status": status,
        })
        new_rows.append(rec)
        seen.add(key)

    if new_rows:
        existing = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True, sort=False)

    existing.to_csv(LOG, index=False)
    configured = bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())
    pd.DataFrame([{
        "checked_at_et": now.isoformat(timespec="seconds"),
        "session_date_et": session_date,
        "high_conviction_candidates": len(candidates),
        "new_alerts": len(new_rows),
        "telegram_sent": sent_count,
        "telegram_configured": configured,
        "status": "OK" if configured else "TELEGRAM_NOT_CONFIGURED",
    }]).to_csv(HEALTH, index=False)

    return candidates


if __name__ == "__main__":
    out = run()
    if out.empty:
        print("No high-conviction intraday setup right now.")
    else:
        print(out.to_string(index=False))
