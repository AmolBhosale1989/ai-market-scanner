import pandas as pd
import pytest

from scanner import market_cutoff, warehouse, warehouse_gate, control_plane


def rows(session="2026-09-22"):
    return pd.DataFrame([{"ticker": "SPY", "event_timestamp": pd.Timestamp(session, tz="UTC"),
                          "ingested_at": pd.Timestamp("2026-09-23T19:30Z")}])


def test_daily_consumer_and_gate_share_cutoff_across_close(monkeypatch):
    monkeypatch.setattr(warehouse, "daily_clock", lambda: pd.Timestamp("2026-09-23T19:30Z"))
    frame = rows().assign(bars=220, invalid_bars=0)
    tier = warehouse_gate.CoverageTier("CRITICAL_DAILY", ("SPY",), "1d", 1.0, 220, 20)
    assert warehouse._freshness_failures(frame, "1d", 20, "test")[0] == []
    assert warehouse_gate.evaluate_tier(tier, frame)["status"] == "PASS"
    # The very same input must fail the real-time publication check after close.
    result = warehouse_gate.evaluate_tier(tier, frame, now_utc=pd.Timestamp("2026-09-23T20:01Z"))
    assert result["status"] == "FAIL"
    assert result["stale_sample"] == ["SPY"]
    assert warehouse_gate.evaluate_tier(tier, frame.assign(invalid_bars=1))["status"] == "FAIL"
    assert warehouse_gate.evaluate_tier(tier, frame.iloc[:0])["status"] == "FAIL"


def test_daily_event_cutoff_excludes_unfinished_session_but_not_new_ingestion(monkeypatch):
    monkeypatch.setattr(market_cutoff, "daily_clock", lambda: pd.Timestamp("2026-09-23T19:30Z"))
    after_close = pd.Timestamp("2026-09-23T20:10Z")
    assert market_cutoff.event_cutoff("1d", after_close) == pd.Timestamp("2026-09-22T23:59:59.999999Z")
    assert market_cutoff.event_cutoff("5m", after_close) == after_close
    # Explicit historical reads must not admit that historical day's unfinished candle.
    assert market_cutoff.event_cutoff("1d", pd.Timestamp("2026-09-18T19:00Z")).date().isoformat() == "2026-09-17"


def test_intraday_stale_protection_is_not_frozen(monkeypatch):
    monkeypatch.setattr(warehouse, "daily_clock", lambda: pd.Timestamp("2026-09-23T14:00Z"))
    frame = rows("2026-09-23T14:00Z")
    assert warehouse._freshness_failures(frame, "5m", 10, "test", now_utc=pd.Timestamp("2026-09-23T15:00Z"))[0] == ["SPY"]


def test_publication_rechecks_frozen_inputs_and_blocks_before_writing(monkeypatch):
    def blocked(_rid):
        raise RuntimeError("PUBLICATION_FRESHNESS_BLOCKED")
    monkeypatch.setattr(warehouse_gate, "validate_publication_freshness", blocked)
    monkeypatch.setattr(control_plane, "_connect", lambda: pytest.fail("publication must not write"))
    with pytest.raises(RuntimeError, match="PUBLICATION_FRESHNESS_BLOCKED"):
        control_plane.publish("production", ["warehouse_snapshot"], run_id="test")


def test_publication_uses_original_asof_and_current_freshness(monkeypatch):
    as_of = pd.Timestamp("2026-09-23T19:40Z")
    now = pd.Timestamp("2026-09-23T19:45Z")
    control_plane.write_dataset("warehouse_snapshot", pd.DataFrame([{
        "status": "PASS", "daily_session": "2026-09-22", "as_of_utc": as_of.isoformat()}]))
    for name in ("master_universe", "live_universe"):
        control_plane.write_dataset(name, pd.DataFrame([{"ticker": "SPY"}]))
    tier = warehouse_gate.CoverageTier("CRITICAL_INTRADAY", ("SPY",), "5m", 1.0, 1, 10)
    monkeypatch.setattr(warehouse_gate, "build_tiers", lambda *_: [tier])
    captured = []
    def coverage(tier, at):
        captured.append(at)
        return rows("2026-09-23T19:35Z").assign(bars=1, invalid_bars=0)
    monkeypatch.setattr(warehouse_gate, "coverage_frame", coverage)
    rid = control_plane.current_run_id()
    warehouse_gate.validate_publication_freshness(rid, now_utc=now)
    assert captured == [as_of]
    with pytest.raises(RuntimeError, match="CRITICAL_INTRADAY"):
        warehouse_gate.validate_publication_freshness(rid, now_utc=now + pd.Timedelta(minutes=11))
    with pytest.raises(RuntimeError, match="daily session changed"):
        warehouse_gate.validate_publication_freshness(rid, now_utc=pd.Timestamp("2026-09-23T20:01Z"))


@pytest.mark.parametrize("at,expected", [
    ("2026-09-23T19:59Z", "2026-09-22"),
    ("2026-09-23T20:01Z", "2026-09-23"),
    ("2026-09-26T12:00Z", "2026-09-25"),
    ("2026-11-27T18:01Z", "2026-11-27"),
])
def test_session_calendar_handles_close_weekend_and_early_close(at, expected):
    assert market_cutoff.completed_daily_session(pd.Timestamp(at)).isoformat() == expected
