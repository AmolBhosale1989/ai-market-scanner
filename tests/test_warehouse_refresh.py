from datetime import datetime, timezone

import pandas as pd

from scanner import warehouse_refresh


def test_daily_refresh_skips_symbols_current_for_latest_completed_session(monkeypatch):
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-20T12:00:00Z")))
    watermarks={
        "FRESH":datetime(2026,9,18,tzinfo=timezone.utc),
        "STALE":datetime(2026,9,17,tzinfo=timezone.utc),
    }
    assert warehouse_refresh._stale_watermarks(watermarks,"1d")=={"STALE"}


def test_intraday_refresh_skips_symbols_current_for_latest_completed_session(monkeypatch):
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-20T12:00:00Z")))
    watermarks={
        "FRESH":datetime(2026,9,18,19,55,tzinfo=timezone.utc),
        "STALE":datetime(2026,9,17,19,55,tzinfo=timezone.utc),
    }
    assert warehouse_refresh._stale_watermarks(watermarks,"5m")=={"STALE"}
