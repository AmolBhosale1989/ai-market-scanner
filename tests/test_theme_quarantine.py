from types import SimpleNamespace
import pandas as pd
from scanner import theme_live


def test_quarantined_etf_cannot_receive_synthetic_zero_return_or_live_rank(monkeypatch):
    base = pd.DataFrame([{"etf": "AAA", "theme": "Fresh", "theme_score": 80},
                         {"etf": "FINX", "theme": "Missing", "theme_score": 99}])
    monkeypatch.setattr(theme_live, "THEMES", {"Fresh": {"etf": "AAA"}, "Missing": {"etf": "FINX"}})
    monkeypatch.setattr(theme_live, "rank_themes", lambda: base)
    frame = pd.DataFrame([
        {"ticker": symbol, "bar_timestamp": stamp, "Close": price}
        for symbol in ("SPY", "AAA")
        for stamp, price in (("2026-09-24T19:55:00Z", 100), ("2026-09-25T19:55:00Z", 102))
    ])
    monkeypatch.setattr(theme_live, "_load_theme_history", lambda _: SimpleNamespace(frame=frame))
    monkeypatch.setattr(theme_live, "latest_frame_session", lambda _: pd.Timestamp("2026-09-25").date())
    writes = {}
    monkeypatch.setattr(theme_live, "write_dataset", lambda name, frame, **kwargs: writes.update({name: frame}))
    result = theme_live.run()
    assert result["etf"].tolist() == ["AAA"]
    assert result["theme_rank"].tolist() == [1]
    assert result["live_change_pct"].tolist() == [2.0]
    assert writes["theme_health"].iloc[0]["quarantined_etf_sample"] == "FINX"
    assert writes["theme_health"].iloc[0]["live_etfs_quarantined"] == 1
