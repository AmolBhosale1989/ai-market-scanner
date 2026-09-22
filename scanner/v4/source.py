from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from ..control_plane import read_dataset, read_record


@dataclass(frozen=True)
class CandidateSourceResult:
    frame: pd.DataFrame
    source_name: str
    source_timestamp_utc: str = ""
    degraded: bool = False
    detail: str = ""


class CandidateSource(Protocol):
    def load(self) -> CandidateSourceResult: ...


class ControlPlaneCandidateSource:
    def load(self) -> CandidateSourceResult:
        frame = read_dataset("all_candidates")
        snapshot = read_record("warehouse_snapshot", required=True)
        timestamp = str(snapshot.get("as_of_utc", ""))
        if not timestamp:
            raise RuntimeError("V4_SOURCE_PROVENANCE_MISSING")
        return CandidateSourceResult(
            frame=frame,
            source_name="postgres-control-plane",
            source_timestamp_utc=timestamp,
        )


class FallbackCandidateSource:
    """Explicit degraded fallback for injected sources; production does not configure one."""

    def __init__(self, primary: CandidateSource, fallback: CandidateSource):
        self.primary = primary
        self.fallback = fallback

    def load(self) -> CandidateSourceResult:
        try:
            return self.primary.load()
        except Exception as exc:
            result = self.fallback.load()
            return CandidateSourceResult(
                frame=result.frame,
                source_name=result.source_name,
                source_timestamp_utc=result.source_timestamp_utc,
                degraded=True,
                detail=f"primary failed: {type(exc).__name__}",
            )
