from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from .config import OUTPUT_DIR
from .themes import THEMES, rank_themes

NY = ZoneInfo("America/New_York")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [c[0] for c in out.columns]
    if "Close" not in out.columns:
        return pd.DataFrame()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx.tz_convert(NY)
    return out.dropna(subset=["Close"]).sort_index()


def _live_move(symbol: str):
    try:
        raw = yf.download(
            symbol,
            period="5d",
            interval="5m",
            auto_adjust=True,
            progress=False,
            threads=False,
            prepost=True,
            timeout=20,
        )
    except TypeError:
        raw = yf.download(
            symbol,
            period="5d",
            interval="5m",
            auto_adjust=True,
            progress=False,
            threads=False,
            prepost=True,
        )
    d = _normalize(raw)
    if d.empty:
        return math.nan, math.nan, ""

    now = datetime.now(NY)
    today = d[d.index.date == now.date()]
    prior_dates = sorted({x for x in d.index.date if x < now.date()}, reverse=True)
    prior_close = math.nan
    if prior_dates:
        prior = d[d.index.date == prior_dates[0]].between_time("09:30", "16:00")
        if not prior.empty:
            prior_close = float(prior["Close"].iloc[-1])

    if today.empty:
        return math.nan, prior_close, ""

    last = float(today["Close"].iloc[-1])
    change = (last / prior_close - 1) * 100 if math.isfinite(prior_close) and prior_close > 0 else math.nan
    return change, prior_close, today.index[-1].isoformat()


def run():
    base = rank_themes()
    if base is None or base.empty:
        print("No base theme table available.")
        return pd.DataFrame()

    spy_move, _, spy_bar = _live_move("SPY")
    out = base.copy()
    live_changes = []
    live_rel = []
    live_bars = []

    for _, row in out.iterrows():
        move, _, bar = _live_move(str(row["etf"]))
        live_changes.append(round(move, 2) if math.isfinite(move) else math.nan)
        rel = move - spy_move if math.isfinite(move) and math.isfinite(spy_move) else math.nan
        live_rel.append(round(rel, 2) if math.isfinite(rel) else math.nan)
        live_bars.append(bar)

    out["live_change_pct"] = live_changes
    out["live_rel_vs_spy_pct"] = live_rel
    out["live_bar_at_et"] = live_bars

    base_score = pd.to_numeric(out["theme_score"], errors="coerce").fillna(0)
    live_rel_s = pd.to_numeric(out["live_rel_vs_spy_pct"], errors="coerce").fillna(0)
    live_move_s = pd.to_numeric(out["live_change_pct"], errors="coerce").fillna(0)

    # Keep the slower multi-day trend as the anchor, but let today's/pre-market
    # leadership materially reorder the list.
    out["live_theme_score"] = (
        base_score * 0.70
        + (50 + live_rel_s * 8 + live_move_s * 2).clip(0, 100) * 0.30
    ).round(1)

    out = out.sort_values(
        ["live_theme_score", "live_rel_vs_spy_pct", "theme_score"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    out["theme_rank"] = range(1, len(out) + 1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_DIR / "trending_themes.csv", index=False)
    pd.DataFrame([{
        "updated_at_et": datetime.now(NY).isoformat(timespec="seconds"),
        "spy_live_change_pct": round(spy_move, 2) if math.isfinite(spy_move) else math.nan,
        "spy_live_bar_at_et": spy_bar,
        "themes_ranked": len(out),
        "mode": "LIVE_EXTENDED_HOURS_PLUS_DAILY_TREND",
    }]).to_csv(OUTPUT_DIR / "theme_health.csv", index=False)

    print(out[["theme_rank","theme","etf","theme_score","live_change_pct","live_rel_vs_spy_pct","live_theme_score"]].to_string(index=False))
    return out


if __name__ == "__main__":
    run()
