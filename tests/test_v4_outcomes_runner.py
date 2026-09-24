import pandas as pd
import pytest


class _Ledger:
    def __init__(self, records):
        self.records = records
        self.histories = None

    def load_records(self):
        return self.records

    def resolve_daily_histories(self, histories):
        self.histories = histories
        return pd.DataFrame()


def test_resolve_daily_publishes_valid_empty_outcomes_without_requesting_tickers(monkeypatch):
    import scanner.v4_outcomes as module

    ledger = _Ledger({})
    monkeypatch.setattr(module, "build_ledger", lambda: ledger)
    monkeypatch.setattr(
        module,
        "warehouse_frames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("empty ticker read is invalid")),
    )

    result = module.resolve_daily()

    assert result.empty
    assert ledger.histories == {}


def test_resolve_daily_keeps_entered_signal_warehouse_fail_closed(monkeypatch):
    import scanner.v4_outcomes as module

    ledger = _Ledger({"signal": {"ticker": "AAPL", "entered_at_utc": "2026-09-23T14:30:00Z"}})
    monkeypatch.setattr(module, "build_ledger", lambda: ledger)

    def fail_closed(tickers, **_kwargs):
        assert tickers == ["AAPL"]
        raise RuntimeError("WAREHOUSE_STALE: AAPL")

    monkeypatch.setattr(module, "warehouse_frames", fail_closed)

    with pytest.raises(RuntimeError, match="WAREHOUSE_STALE"):
        module.resolve_daily()
