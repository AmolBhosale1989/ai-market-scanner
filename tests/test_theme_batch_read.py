import pandas as pd
import pytest

from scanner import themes


def test_theme_history_is_read_once_and_ranked_without_changing_returns(monkeypatch):
    calls = []
    history = pd.DataFrame({"Close": range(100, 200)})
    monkeypatch.setattr(themes, "THEMES", {"Alpha": {"etf": "AAA"}, "Beta": {"etf": "BBB"}})

    def read(tickers, **kwargs):
        calls.append((tickers, kwargs))
        return {ticker: history.copy() for ticker in tickers}

    def indicators(frame):
        return frame.assign(EMA20=180, EMA50=170, EMA200=160)

    monkeypatch.setattr(themes, "warehouse_frames", read)
    monkeypatch.setattr(themes, "add_indicators", indicators)
    monkeypatch.setattr(themes, "write_dataset", lambda *args, **kwargs: None)
    result = themes.rank_themes()
    assert calls == [(["SPY", "AAA", "BBB"], dict(period="6mo", interval="1d", max_age_minutes=20, require_complete=True))]
    assert result["theme_score"].tolist() == [74, 74]
    assert result["rel20_vs_spy"].tolist() == [0, 0]
    assert result["ret20_pct"].tolist() == [round((199 / 179 - 1) * 100, 2)] * 2


@pytest.mark.parametrize("reason", ["WAREHOUSE_STALE", "WAREHOUSE_QUALITY_FAILED", "WAREHOUSE_COVERAGE_FAILED"])
def test_theme_batch_failure_prevents_publication(monkeypatch, reason):
    def fail(*args, **kwargs):
        raise RuntimeError(reason)

    writes = []
    monkeypatch.setattr(themes, "warehouse_frames", fail)
    monkeypatch.setattr(themes, "write_dataset", lambda *args, **kwargs: writes.append(args))
    with pytest.raises(RuntimeError, match=reason):
        themes.rank_themes()
    assert writes == []
