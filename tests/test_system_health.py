from datetime import datetime
from pathlib import Path

import pandas as pd

from scanner import system_health


class _FrozenSunday(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 9, 20, 4, 10)
        return value.replace(tzinfo=tz) if tz is not None else value


def _freeze(monkeypatch):
    monkeypatch.setattr(system_health, "datetime", _FrozenSunday)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp("2026-09-20T08:10:00Z")))
    monkeypatch.setattr(system_health, "expected_market_data_session", lambda now: pd.Timestamp("2026-09-18").date())


def _put(memory, name, row):
    memory["datasets"][(memory["run_id"], name)] = pd.DataFrame([row])


def test_warehouse_snapshot_is_recognized_as_production_health(monkeypatch, memory_control_plane):
    _freeze(monkeypatch)
    _put(memory_control_plane, "warehouse_snapshot", {"status": "PASS", "as_of_utc": "2026-09-20T08:07:14Z"})
    health = system_health.run()
    warehouse = health.loc[health["module"] == "Warehouse Snapshot"].iloc[0]
    assert warehouse["status"] == "OK"
    assert warehouse["last_update_utc"] == "2026-09-20T08:07:14+00:00"


def test_production_workflow_seeds_prior_snapshot_before_live_refresh():
    workflow = (Path(__file__).parents[1] / ".github/workflows/production.yml").read_text()
    assert "production_telemetry --seed --mode production" in workflow


def test_semantic_health_rejects_wrong_session_and_dropped_rows(monkeypatch, memory_control_plane):
    _freeze(monkeypatch)
    timestamp = "2026-09-20T08:00:00Z"
    _put(memory_control_plane, "warehouse_snapshot", {"as_of_utc": timestamp})
    _put(memory_control_plane, "theme_health", {"updated_at_et": timestamp, "session_date": "2026-09-18", "themes_ranked": 19})
    _put(memory_control_plane, "sector_rotation_health", {"updated_at_et": timestamp, "session_date": "2026-09-20", "themes_scanned": 0, "stocks_scanned": 0})
    _put(memory_control_plane, "momentum_health", {"updated_at_et": timestamp, "session_date": "2026-09-18", "candidate_inputs": 33, "leaders_evaluated": 0})
    _put(memory_control_plane, "order_flow_strategy_health", {"updated_at_et": timestamp, "session_date": "2026-09-18", "expected_inputs": 0, "evaluated": 0})
    health = system_health.run().set_index("module")
    assert health.loc["Themes", "status"] == "OK"
    assert set(health.loc[["Sector Rotation", "Momentum", "Order Flow"], "status"]) == {"INVALID"}


def test_semantic_health_accepts_no_trade_when_all_inputs_were_evaluated(monkeypatch, memory_control_plane):
    _freeze(monkeypatch)
    timestamp = "2026-09-20T08:00:00Z"
    _put(memory_control_plane, "warehouse_snapshot", {"as_of_utc": timestamp})
    _put(memory_control_plane, "theme_health", {"updated_at_et": timestamp, "session_date": "2026-09-18", "themes_ranked": 19})
    _put(memory_control_plane, "sector_rotation_health", {"updated_at_et": timestamp, "session_date": "2026-09-18", "themes_scanned": 11, "stocks_scanned": 110})
    _put(memory_control_plane, "momentum_health", {"updated_at_et": timestamp, "session_date": "2026-09-18", "candidate_inputs": 33, "leaders_evaluated": 33})
    _put(memory_control_plane, "order_flow_strategy_health", {"updated_at_et": timestamp, "session_date": "2026-09-18", "expected_inputs": 33, "evaluated": 33})
    health = system_health.run().set_index("module")
    assert set(health.loc[["Themes", "Sector Rotation", "Momentum", "Order Flow"], "status"]) == {"OK"}
