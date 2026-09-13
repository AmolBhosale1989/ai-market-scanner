from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Protocol

import pandas as pd
import requests

from .shortlist import load_shortlist_source


DEFAULT_REMOTE_BASE = (
    "https://raw.githubusercontent.com/AmolBhosale1989/"
    "ai-market-scanner/scan-data/dashboard-data"
)


@dataclass(frozen=True)
class CandidateSourceResult:
    frame: pd.DataFrame
    source_name: str
    source_timestamp_utc: str = ""
    degraded: bool = False
    detail: str = ""


class CandidateSource(Protocol):
    def load(self) -> CandidateSourceResult: ...


class LocalCandidateSource:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)

    def load(self) -> CandidateSourceResult:
        return CandidateSourceResult(
            frame=load_shortlist_source(self.output_dir),
            source_name="local-scan-output",
        )


class HttpCandidateSource:
    def __init__(self, base_url: str = DEFAULT_REMOTE_BASE, timeout_seconds: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _get(self, name: str) -> requests.Response:
        response = requests.get(f"{self.base_url}/{name}", timeout=self.timeout_seconds)
        response.raise_for_status()
        return response

    def load(self) -> CandidateSourceResult:
        candidates = self._get("all_candidates.csv")
        frame = pd.read_csv(StringIO(candidates.text))
        timestamp = ""
        detail = ""
        try:
            metadata = pd.read_csv(StringIO(self._get("scan_metadata.csv").text))
            if not metadata.empty:
                for column in ("generated_at_utc", "updated_at_utc"):
                    if column in metadata:
                        timestamp = str(metadata.iloc[0][column])
                        break
        except Exception as exc:
            detail = f"metadata unavailable: {type(exc).__name__}"
        return CandidateSourceResult(
            frame=frame,
            source_name="github-scan-data",
            source_timestamp_utc=timestamp,
            degraded=bool(detail),
            detail=detail,
        )


class FallbackCandidateSource:
    def __init__(self, primary: CandidateSource, fallback: CandidateSource):
        self.primary = primary
        self.fallback = fallback

    def load(self) -> CandidateSourceResult:
        try:
            return self.primary.load()
        except Exception as primary_error:
            result = self.fallback.load()
            return CandidateSourceResult(
                frame=result.frame,
                source_name=result.source_name,
                source_timestamp_utc=result.source_timestamp_utc,
                degraded=True,
                detail=f"primary failed: {type(primary_error).__name__}",
            )
