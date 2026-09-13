from __future__ import annotations

from pathlib import Path

import pandas as pd


def _numeric(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    value = frame[column] if column in frame else pd.Series(default, index=frame.index)
    return pd.to_numeric(value, errors="coerce").fillna(default)


def build_monitor_shortlist(
    candidates: pd.DataFrame,
    hot_limit: int = 50,
    warm_limit: int = 200,
) -> pd.DataFrame:
    """Convert the daily broad scan into bounded HOT/WARM live-monitor tiers."""
    if candidates is None or candidates.empty:
        return pd.DataFrame()
    frame = candidates.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    if "universal_10pct_gate" in frame:
        gate = frame["universal_10pct_gate"].fillna(False).astype(bool)
        frame = frame[gate].copy()
    if frame.empty:
        return frame

    score_column = "v4_active_rank_score" if "v4_active_rank_score" in frame else "market_hunt_score"
    score = _numeric(frame, score_column, -100)
    if "market_hunt_score" not in frame:
        frame["market_hunt_score"] = score
    rvol = _numeric(frame, "intraday_rvol")
    catalyst = _numeric(frame, "catalyst_score")
    theme = _numeric(frame, "theme_score")
    explosive = _numeric(frame, "max_up_day_30d_pct")
    stage_bonus = frame.get("stage", pd.Series("", index=frame.index)).map(
        {"CONFIRMED": 20, "ARMED": 15, "FORMING": 8, "DISCOVER": 3}
    ).fillna(0)
    frame["v4_priority_score"] = (
        score
        + stage_bonus
        + catalyst.clip(0, 100) * 0.12
        + theme.clip(0, 100) * 0.05
        + rvol.clip(0, 10) * 2.0
        + explosive.clip(0, 30) * 0.25
    ).round(2)

    frame = frame.sort_values(
        ["v4_priority_score", "market_hunt_score"], ascending=[False, False]
    ).drop_duplicates("ticker")
    total = max(0, hot_limit) + max(0, warm_limit)
    frame = frame.head(total).copy()
    frame["monitor_tier"] = "WARM"
    frame.iloc[: min(hot_limit, len(frame)), frame.columns.get_loc("monitor_tier")] = "HOT"
    return frame.reset_index(drop=True)


def load_shortlist_source(output_dir: Path) -> pd.DataFrame:
    for name in ("all_candidates.csv", "latest_scan.csv"):
        path = Path(output_dir) / name
        if path.exists():
            return pd.read_csv(path)
    raise FileNotFoundError("Run the broad scan first; no V4 shortlist source exists.")
