from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from scanner import system_health


@pytest.fixture(autouse=True)
def _health_batch(monkeypatch,memory_control_plane):
    def read(names,*,run_id):
        return {name:(memory_control_plane["datasets"].get((run_id,name),pd.DataFrame()).copy(),"postgresql") for name in names}
    monkeypatch.setattr(system_health,"read_health_datasets",read)


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


def test_missing_production_data_is_not_healthy_outside_market_hours(monkeypatch, memory_control_plane):
    _freeze(monkeypatch)
    system_health.run()
    summary=memory_control_plane["datasets"][(memory_control_plane["run_id"],"live_system_health_summary")]
    assert summary.iloc[0]["overall_status"]=="DEGRADED"


def _freeze_at(monkeypatch, instant):
    now=pd.Timestamp(instant)
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.to_pydatetime().astimezone(tz)
    monkeypatch.setattr(system_health,"datetime",Frozen)
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: now))


def test_future_health_timestamp_is_invalid(monkeypatch,memory_control_plane):
    _freeze_at(monkeypatch,"2026-09-25T02:00:00Z")
    _put(memory_control_plane,"warehouse_snapshot",{"as_of_utc":"2026-09-25T03:00:00Z"})
    health=system_health.run().set_index("module")
    assert health.loc["Warehouse Snapshot","status"]=="INVALID"


def test_health_clock_respects_nyse_holiday(monkeypatch,memory_control_plane):
    _freeze_at(monkeypatch,"2026-07-03T15:00:00Z")
    health=system_health.run()
    assert not health["market_open"].any()


def test_health_clock_respects_early_close(monkeypatch,memory_control_plane):
    _freeze_at(monkeypatch,"2026-11-27T18:30:00Z")
    health=system_health.run()
    assert not health["market_open"].any()


def test_regular_session_expired_health_is_still_blocked(monkeypatch,memory_control_plane):
    _freeze_at(monkeypatch,"2026-09-24T15:00:00Z")
    _put(memory_control_plane,"warehouse_snapshot",{"as_of_utc":"2026-09-24T14:00:00Z"})
    health=system_health.run().set_index("module")
    assert health.loc["Warehouse Snapshot","market_open"]
    assert health.loc["Warehouse Snapshot","status"]=="STALE"


def test_health_reads_one_verified_batch(monkeypatch,memory_control_plane):
    _freeze(monkeypatch)
    calls=[]
    def read(names,*,run_id):
        calls.append((names,run_id))
        return {name:(pd.DataFrame(),"blocked") for name in names}
    monkeypatch.setattr(system_health,"read_health_datasets",read)
    monkeypatch.setattr(system_health,"read_dataset",lambda *a,**k:pytest.fail("serial read"))
    system_health.run()
    assert len(calls)==1
    assert calls[0][1]==memory_control_plane["run_id"]
    assert len(calls[0][0])==12


def test_health_corrupt_batch_cannot_be_healthy(monkeypatch):
    _freeze(monkeypatch)
    def fail(*a,**k):raise RuntimeError("CONTROL_PLANE_DATASET_CORRUPT")
    monkeypatch.setattr(system_health,"read_health_datasets",fail)
    monkeypatch.setattr(system_health,"write_dataset",lambda *a,**k:pytest.fail("health published after corrupt read"))
    with pytest.raises(RuntimeError,match="CONTROL_PLANE_DATASET_CORRUPT"):
        system_health.run()
