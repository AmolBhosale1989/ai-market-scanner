import pandas as pd

from scanner.live import _quote_spread
from scanner.prefilter import evaluate_prefilter


def _history(volume=2_000_000, daily_range_pct=3.0):
    rows=40
    close=20.0
    half_range=close * daily_range_pct / 200
    return pd.DataFrame({
        "Open":[close] * rows,
        "High":[close + half_range] * rows,
        "Low":[close - half_range] * rows,
        "Close":[close] * rows,
        "Volume":[volume] * rows,
    })


def test_quality_prefilter_accepts_liquid_moving_stock():
    row=evaluate_prefilter("GOOD",_history())
    assert row["tradable"] is True
    assert row["avg_share_volume20"] >= 1_000_000
    assert row["median_dollar_volume20"] >= 25_000_000
    assert row["adr20_pct"] >= 2.0


def test_quality_prefilter_rejects_low_daily_range():
    row=evaluate_prefilter("SLOW",_history(daily_range_pct=1.0))
    assert row["tradable"] is False
    assert "LOW_DAILY_RANGE" in row["rejection_reason"]


def test_quality_prefilter_uses_median_to_reject_one_day_volume_spike():
    hist=_history()
    hist.loc[20:38,"Volume"]=500_000
    hist.loc[39,"Volume"]=20_000_000
    row=evaluate_prefilter("SPIKE",hist)
    assert row["avg_dollar_volume20"] >= 20_000_000
    assert row["median_dollar_volume20"] < 25_000_000
    assert row["tradable"] is False
    assert "MEDIAN_DOLLAR_VOLUME" in row["rejection_reason"]


def test_quote_spread_calculation(monkeypatch):
    class FakeTicker:
        def __init__(self,ticker):
            self.ticker=ticker

        def get_info(self):
            return {"bid":99.9,"ask":100.1}

    monkeypatch.setattr("scanner.live.yf.Ticker",FakeTicker)
    bid,ask,spread=_quote_spread("TEST")
    assert bid == 99.9
    assert ask == 100.1
    assert round(spread,3) == 0.2


def test_quote_spread_rejects_missing_quote(monkeypatch):
    class FakeTicker:
        def __init__(self,ticker):
            self.ticker=ticker

        def get_info(self):
            return {"bid":0,"ask":0}

    monkeypatch.setattr("scanner.live.yf.Ticker",FakeTicker)
    _,_,spread=_quote_spread("TEST")
    assert pd.isna(spread)
