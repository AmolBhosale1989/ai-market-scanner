from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
from typing import Protocol

import pandas as pd

from ..live import _empty_live, analyze_live_candidate
from ..config import LIVE_PERIOD, LIVE_INTERVAL
from ..warehouse import frames as warehouse_frames


@dataclass(frozen=True)
class PollResult:
    frame: pd.DataFrame
    provider: str
    started_at_utc: str
    completed_at_utc: str
    duration_ms: int
    requested: int
    received: int
    errors: int


class LiveMarketAdapter(Protocol):
    def poll(self, candidates: pd.DataFrame) -> PollResult: ...


def _number(value, default=math.nan) -> float:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(number) if pd.notna(number) else default


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


class YahooPollingAdapter:
    """Bounded free-data adapter; core V4 logic is independent of this provider."""

    def __init__(self, max_workers: int = 8):
        self.max_workers = max(1, min(int(max_workers), 16))

    @staticmethod
    def _analyze(row: pd.Series, history_frame: pd.DataFrame) -> dict:
        return analyze_live_candidate(
            ticker=str(row["ticker"]),
            entry_trigger=_number(row.get("entry_trigger")),
            stage=str(row.get("stage", "")),
            catalyst_score=_number(row.get("catalyst_score"), 0.0),
            rr_to_8pct=_number(row.get("effective_rr", row.get("rr_to_8pct"))),
            runway_pct=_number(row.get("runway_to_next_resistance_pct")),
            negative_catalyst_risk=_truthy(row.get("negative_catalyst_risk", False)),
            entry_condition=str(row.get("entry_condition", "BREAKOUT")),
            history_frame=history_frame,
        )

    def poll(self, candidates: pd.DataFrame) -> PollResult:
        started_wall = time.monotonic()
        started = datetime.now(timezone.utc).isoformat()
        if candidates is None or candidates.empty:
            return PollResult(pd.DataFrame(), "yahoo", started, started, 0, 0, 0, 0)

        output = candidates.copy().reset_index(drop=True)
        for column, value in _empty_live().items():
            output[column] = value

        # Preserve the adapter's existing per-symbol quarantine and coverage
        # accounting. Only independently validated fresh frames reach analysis.
        histories=warehouse_frames(
            output["ticker"].astype(str).str.upper().tolist(),
            period=LIVE_PERIOD, interval=LIVE_INTERVAL,
            max_age_minutes=10, require_complete=False,
        )
        for index,row in output.iterrows():
            if str(row["ticker"]).upper() not in histories:
                for key,value in _empty_live("ERROR").items():
                    output.at[index,key]=value

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(output))) as pool:
            future_to_index = {
                pool.submit(self._analyze, row, histories[str(row["ticker"]).upper()]): index
                for index, row in output.iterrows()
                if str(row["ticker"]).upper() in histories
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    values = future.result()
                except Exception:
                    values = _empty_live("ERROR")
                for key, value in values.items():
                    output.at[index, key] = value

        completed = datetime.now(timezone.utc).isoformat()
        duration_ms = round((time.monotonic() - started_wall) * 1000)
        regular_price = pd.to_numeric(output["live_price"], errors="coerce")
        premarket_price = pd.to_numeric(output["premarket_price"], errors="coerce")
        received = int((regular_price.notna() | premarket_price.notna()).sum())
        errors = len(output) - received
        return PollResult(
            output,
            "yahoo",
            started,
            completed,
            duration_ms,
            len(output),
            received,
            errors,
        )
