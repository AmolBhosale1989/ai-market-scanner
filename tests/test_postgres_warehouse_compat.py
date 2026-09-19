from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from scanner import warehouse


def _rows(tickers=("AAPL",), age_minutes=1):
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return pd.DataFrame([
        {
            "ticker": ticker,
            "event_timestamp": now - timedelta(days=1),
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


def test_update_cannot_recreate_csv_warehouse():
    with pytest.raises(RuntimeError, match="WAREHOUSE_UPDATE_REMOVED"):
        warehouse.update(["AAPL"])


def test_auxiliary_csv_fallback_is_disabled():
    with pytest.raises(RuntimeError, match="WAREHOUSE_DATASET_NOT_MIGRATED"):
        warehouse.request_dataset("event_news", "test", ["AAPL"])
