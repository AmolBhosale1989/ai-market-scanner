from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scanner import warehouse
from scanner.market_cutoff import completed_daily_session


def _rows(tickers=("AAPL",), age_minutes=1):
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return pd.DataFrame([
        {
            "ticker": ticker,
            "event_timestamp": pd.Timestamp(completed_daily_session(), tz="UTC").as_unit("ns"),
            "ingested_at": now,
            "warehouse_run_id": "00000000-0000-0000-0000-000000000001",
            "provider": "TEST",
            "open": 100, "high": 110, "low": 99, "close": 108, "volume": 1000,
        }
        for ticker in tickers
    ])


def test_frames_preserves_legacy_ohlcv_shape(monkeypatch):
    monkeypatch.setattr(warehouse, "point_in_time", lambda req: _rows(req.tickers))
    result = warehouse.frames(["AAPL"], interval="1d", max_age_minutes=20)
    assert list(result["AAPL"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert float(result["AAPL"].iloc[-1]["Close"]) == 108


def test_missing_symbol_fails_closed(monkeypatch):
    monkeypatch.setattr(warehouse, "point_in_time", lambda req: _rows(("AAPL",)))
    with pytest.raises(RuntimeError, match="WAREHOUSE_COVERAGE_INCOMPLETE"):
        warehouse.frames(["AAPL", "MSFT"], interval="1d", max_age_minutes=20)


def test_stale_postgres_data_fails_closed(monkeypatch):
    stale = _rows(("AAPL",))
    stale["event_timestamp"] = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5)
    monkeypatch.setattr(warehouse, "point_in_time", lambda req: stale)
    with pytest.raises(RuntimeError, match="WAREHOUSE_STALE"):
        warehouse.history("AAPL", interval="1d", max_age_minutes=20)


def test_tolerant_frames_quarantines_stale_symbol(monkeypatch):
    rows=_rows(("AAPL","STALE"))
    rows.loc[rows["ticker"].eq("STALE"),"event_timestamp"]=pd.Timestamp.now(tz="UTC")-pd.Timedelta(days=5)
    monkeypatch.setattr(warehouse,"point_in_time",lambda req: rows)
    result=warehouse.frames(["AAPL","STALE"],interval="1d",max_age_minutes=20,require_complete=False)
    assert set(result)=={"AAPL"}


def test_provide_quarantines_optional_stale_symbol_within_coverage(monkeypatch):
    rows=_rows(("SPY","FINX","ARKK"))
    monkeypatch.setattr(warehouse,"point_in_time",lambda req: rows)
    def freshness(frame,*_args):
        stale=[x for x in ("FINX",) if x in set(frame["ticker"])]
        return stale,pd.Timestamp.now(tz="UTC"),"market_open bar_end_max=20m"
    monkeypatch.setattr(warehouse,"_freshness_failures",freshness)
    view=warehouse.provide(warehouse.DataRequirement(
        consumer="theme_live",tickers=("SPY","FINX","ARKK"),interval="5m",
        minimum_fresh_coverage=0.5,required_fresh_tickers=("SPY",),
    ))
    assert set(view.frame["ticker"])=={"SPY","ARKK"}


def test_provide_never_quarantines_required_stale_symbol(monkeypatch):
    rows=_rows(("SPY","FINX"))
    monkeypatch.setattr(warehouse,"point_in_time",lambda req: rows)
    monkeypatch.setattr(
        warehouse,"_freshness_failures",
        lambda *_args: (["SPY"],pd.Timestamp.now(tz="UTC"),"market_open bar_end_max=20m"),
    )
    with pytest.raises(RuntimeError,match="required_stale=SPY"):
        warehouse.provide(warehouse.DataRequirement(
            consumer="theme_live",tickers=("SPY","FINX"),interval="5m",
            minimum_fresh_coverage=0.9,required_fresh_tickers=("SPY",),
        ))


def test_update_cannot_recreate_csv_warehouse():
    with pytest.raises(RuntimeError, match="WAREHOUSE_UPDATE_REMOVED"):
        warehouse.update(["AAPL"])


def test_auxiliary_csv_fallback_is_disabled():
    with pytest.raises(RuntimeError, match="WAREHOUSE_DATASET_NOT_MIGRATED"):
        warehouse.request_dataset("event_news", "test", ["AAPL"])


@pytest.mark.parametrize("now,expected", [
    ("2026-09-25T19:59:59Z", ["SPY"]),
    ("2026-09-25T20:00:00Z", []),
    ("2026-09-25T20:01:00Z", []),
])
def test_bar_start_in_past_does_not_make_unfinished_bar_fresh(now, expected):
    rows = pd.DataFrame([{"ticker": "SPY", "event_timestamp": "2026-09-25T19:55:00Z",
                          "ingested_at": "2026-09-25T19:59:00Z"}])
    assert warehouse._freshness_failures(rows, "5m", 10, "audit", now_utc=now)[0] == expected
