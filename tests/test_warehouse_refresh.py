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


def test_intraday_refresh_always_refetches_critical_market_symbols(monkeypatch):
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-21T17:20:00Z")))
    watermarks={
        "SPY":datetime(2026,9,21,17,10,tzinfo=timezone.utc),
        "FINX":datetime(2026,9,21,17,10,tzinfo=timezone.utc),
        "AAPL":datetime(2026,9,21,17,10,tzinfo=timezone.utc),
    }
    candidates=warehouse_refresh._refresh_candidates(watermarks,"5m",list(watermarks))
    assert {"SPY","FINX"}.issubset(candidates)
    assert "AAPL" not in candidates


def test_daily_refresh_does_not_force_current_critical_symbols(monkeypatch):
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-20T12:00:00Z")))
    watermarks={"SPY":datetime(2026,9,18,tzinfo=timezone.utc)}
    assert warehouse_refresh._refresh_candidates(watermarks,"1d",["SPY"])==set()
