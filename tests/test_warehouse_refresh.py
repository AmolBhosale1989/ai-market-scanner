from datetime import datetime, timezone
import sys

import pandas as pd
import pytest

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
    assert "AAPL" in candidates


def test_daily_refresh_does_not_force_current_critical_symbols(monkeypatch):
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-20T12:00:00Z")))
    watermarks={"SPY":datetime(2026,9,18,tzinfo=timezone.utc)}
    assert warehouse_refresh._refresh_candidates(watermarks,"1d",["SPY"])==set()


@pytest.mark.parametrize("bounded", [True, False])
def test_preflight_is_bounded_but_broad_refresh_keeps_dependencies(monkeypatch, bounded):
    requested = []
    downloaded = []
    monkeypatch.setattr(warehouse_refresh, "verify_health", lambda: None)
    monkeypatch.setattr(warehouse_refresh, "start_run",
                        lambda **kw: requested.extend(kw["payload"]["tickers"]) or "run")
    monkeypatch.setattr(warehouse_refresh, "finish_run", lambda *a, **kw: None)
    monkeypatch.setattr(warehouse_refresh, "latest_event_timestamps", lambda *a, **kw: {})
    monkeypatch.setattr(warehouse_refresh, "download_batch",
                        lambda tickers, **kw: downloaded.extend(tickers) or {})
    kwargs = {"include_ingestion_dependencies": False} if bounded else {}
    result = warehouse_refresh.refresh(list(warehouse_refresh.CRITICAL_MARKET_SYMBOLS),
                                       benchmark_backfill=False, **kwargs)
    expected = set(warehouse_refresh.CRITICAL_MARKET_SYMBOLS)
    if bounded:
        assert len(expected) == 30
    else:
        expected.update(warehouse_refresh.INGESTION_CRITICAL_SYMBOLS)
        assert len(expected) > 30
    assert set(requested) == set(downloaded) == expected
    assert result["requested_symbols"] == len(expected)


def test_critical_only_cli_passes_exact_preflight_scope(monkeypatch):
    def refresh(tickers, **kwargs):
        assert tickers == list(warehouse_refresh.CRITICAL_MARKET_SYMBOLS)
        assert kwargs["include_ingestion_dependencies"] is False
        return {"run_id": "run", "interval": "1d", "ingested_symbols": 0, "observations": 0}
    monkeypatch.setattr(warehouse_refresh, "refresh", refresh)
    monkeypatch.setattr(sys, "argv", ["warehouse_refresh", "--critical-only", "--period", "1y"])
    warehouse_refresh.main()


def test_only_completed_provider_candles_are_stored(monkeypatch):
    frame=pd.DataFrame({"event_timestamp":pd.to_datetime([
        "2026-09-24T18:20:00Z", "2026-09-24T18:25:00Z"])})
    kept=warehouse_refresh._completed_observations(frame,"5m",pd.Timestamp("2026-09-24T18:27:00Z"))
    assert len(kept)==1
    monkeypatch.setattr(warehouse_refresh,"completed_daily_session",lambda:pd.Timestamp("2026-09-23").date())
    daily=pd.DataFrame({"event_timestamp":pd.to_datetime(["2026-09-23T04:00:00Z","2026-09-24T04:00:00Z"])})
    assert len(warehouse_refresh._completed_observations(daily,"1d",pd.Timestamp("2026-09-24T18:27:00Z")))==1


def test_incremental_refresh_revisits_latest_bar(monkeypatch):
    event=pd.Timestamp("2026-09-23T18:20:00Z")
    frame=pd.DataFrame({"Open":[100.],"High":[102.],"Low":[99.],"Close":[101.],"Volume":[500.]},index=pd.DatetimeIndex([event]))
    captured=[]
    monkeypatch.setattr(warehouse_refresh,"verify_health",lambda:None)
    monkeypatch.setattr(warehouse_refresh,"start_run",lambda **kw:"run")
    monkeypatch.setattr(warehouse_refresh,"finish_run",lambda *a,**kw:None)
    monkeypatch.setattr(warehouse_refresh,"latest_event_timestamps",lambda *a,**kw:{"AAPL":event})
    monkeypatch.setattr(warehouse_refresh,"download_batch",lambda *a,**kw:{"AAPL":frame})
    monkeypatch.setattr(warehouse_refresh,"ingest_observations",lambda f,**kw:captured.append(f) or len(f))
    warehouse_refresh.refresh(["AAPL"],interval="5m",benchmark_backfill=False,include_ingestion_dependencies=False)
    assert len(captured)==1
    assert captured[0].event_timestamp.iloc[0]==event
