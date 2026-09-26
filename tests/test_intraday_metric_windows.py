import math

import pandas as pd
import pytest

from scanner.intraday_metrics import close_return_30m, same_clock_rvol


def prices(day="2026-09-25", closes=None):
    closes = closes if closes is not None else [100, 110, 111, 112, 113, 114, 120]
    return pd.DataFrame({"Close": closes, "Volume": [100] * len(closes)},
                        index=pd.date_range(f"{day} 09:30", periods=len(closes), freq="5min", tz="America/New_York"))


def test_return_uses_exact_thirty_minutes_not_last_six_rows():
    frame = prices()
    assert close_return_30m(frame) == pytest.approx(20)
    old_six_row_result = (120 / 110 - 1) * 100
    assert close_return_30m(frame) != pytest.approx(old_six_row_result)
    assert close_return_30m(frame.iloc[::-1]) == pytest.approx(20)
    assert close_return_30m(frame.tz_convert("UTC")) == pytest.approx(20)


def test_missing_interior_bucket_does_not_move_observed_endpoints():
    assert close_return_30m(prices().drop(prices().index[2])) == pytest.approx(20)


@pytest.mark.parametrize("case", ["opening", "missing_start", "duplicate", "naive", "nan", "zero", "infinity", "previous_session"])
def test_unavailable_or_invalid_endpoints_are_not_zero_or_shorter_returns(case):
    frame = prices()
    if case == "opening":
        frame = frame.iloc[:6]
    elif case == "missing_start":
        frame = frame.iloc[1:]
    elif case == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]])
    elif case == "naive":
        frame = frame.tz_localize(None)
    elif case in {"nan", "zero", "infinity"}:
        frame = frame.astype(float)
        frame.iloc[0, 0] = {"nan": math.nan, "zero": 0, "infinity": math.inf}[case]
    else:
        frame.index = pd.DatetimeIndex(["2026-09-24T23:45:00-04:00", *[f"2026-09-25T00:{m:02d}:00-04:00" for m in [0, 2, 5, 7, 10, 15]]])
    assert math.isnan(close_return_30m(frame))


@pytest.mark.parametrize("missing_today", [True, False])
def test_relative_volume_compares_same_clock_even_with_missing_buckets(missing_today):
    prior, today = prices("2026-09-24"), prices()
    if missing_today:
        today = today.drop(today.index[2])
        today["Volume"] = 200
        expected = 1200 / 700
    else:
        prior = prior.drop(prior.index[2])
        # Later historical bars must not enter the same-time denominator.
        extra = pd.DataFrame({"Close": [120], "Volume": [10000]}, index=[prior.index[-1] + pd.Timedelta(minutes=5)])
        prior = pd.concat([prior, extra])
        today["Volume"] = 200
        expected = 1400 / 600
    history = pd.concat([prior, today])
    date = today.index[-1].date()
    assert same_clock_rvol(history, today, date, sessions=2) == pytest.approx(expected)
    # Both production consumers must use the same timestamp boundary.
    from scanner.broad_breakout import _same_time_rvol as broad_rvol
    from scanner.momentum_signals import _same_time_rvol as momentum_rvol
    assert broad_rvol(history, today, date) == pytest.approx(expected)
    assert momentum_rvol(history, today, date) == pytest.approx(expected)


def test_rvol_retains_consumer_history_depth_and_rejects_no_baseline():
    today = prices()
    prior_frames = []
    for day, volume in [("2026-09-22", 600), ("2026-09-23", 200), ("2026-09-24", 100)]:
        frame = prices(day)
        frame["Volume"] = volume
        prior_frames.append(frame)
    history = pd.concat([*prior_frames, today])
    date = today.index[-1].date()
    assert same_clock_rvol(history, today, date, sessions=2) == pytest.approx(2/3)
    assert same_clock_rvol(history, today, date, sessions=3) == pytest.approx(1/3)
    assert math.isnan(same_clock_rvol(today, today, date, sessions=2))


def test_broad_discovery_publishes_the_observed_thirty_minute_move(monkeypatch, memory_control_plane):
    from scanner import broad_breakout as module
    prior, today = prices("2026-09-24"), prices()
    prior["Close"] = 100
    prior["Volume"] = 1_000_000
    today["Volume"] = 2_000_000
    history = pd.concat([prior, today])
    history["Open"] = history["Close"]
    history["High"] = history["Close"] + .1
    history["Low"] = history["Close"] - .1
    spy = history.copy()
    spy["Close"] = 100
    rid = memory_control_plane["run_id"]
    memory_control_plane["datasets"][(rid, "live_universe")] = pd.DataFrame([{"ticker": "AAA"}])
    monkeypatch.setattr(module, "warehouse_history", lambda *args, **kwargs: spy)
    monkeypatch.setattr(module, "warehouse_frames", lambda *args, **kwargs: {"AAA": history})
    out = module.run()
    assert len(out) == 1
    assert out.iloc[0].move_30m_pct == 20
    assert out.iloc[0].broad_rvol == 2


@pytest.mark.parametrize("missing", ["none", "etf", "stock"])
def test_missing_window_cannot_create_maximum_rotation_score_or_leader(monkeypatch, missing):
    from scanner import sector_rotation as module
    prior = prices("2026-09-24")
    prior["Close"] = 100
    full = pd.concat([prior, prices()])
    raw = {"SPY": full.copy(), "ETF": full.copy(), "AAA": full.copy()}
    raw["SPY"]["Close"] = 100
    if missing != "none":
        ticker = "ETF" if missing == "etf" else "AAA"
        raw[ticker] = raw[ticker].drop(prices().index[0])
    monkeypatch.setattr(module, "THEME_ETFS", {"TEST": "ETF"})
    monkeypatch.setattr(module, "THEME_CONSTITUENTS", {"TEST": ["AAA"]})
    monkeypatch.setattr(module, "_load_rotation_history", lambda: (list(raw), raw))
    themes, leaders = module.run()
    if missing == "none":
        assert themes.iloc[0].rotation_score == 100
        assert leaders.iloc[0].move_30m_pct == 20
        assert leaders.iloc[0].rotation_leader
        assert leaders.iloc[0].rotation_rank == 1
    else:
        assert pd.isna(leaders.iloc[0].rotation_leader_score)
        assert pd.isna(leaders.iloc[0].rotation_rank)
        assert not leaders.iloc[0].rotation_leader
        assert leaders.iloc[0].rotation_metric_state == "NOT_READY_30M"
        if missing == "etf":
            assert pd.isna(themes.iloc[0].rotation_score)
            assert pd.isna(themes.iloc[0].rotation_rank)
            assert themes.iloc[0].rotation_state == "NOT_READY_30M"
