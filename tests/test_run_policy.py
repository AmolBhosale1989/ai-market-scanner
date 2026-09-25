import pandas as pd
import pytest
from scanner.run_policy import select_mode


def manifest(at):
    return {"status": "PUBLISHED", "warehouse_as_of_utc": at}


def test_live_reuses_only_same_completed_daily_session():
    source = manifest("2026-09-24T17:00:00Z")
    assert select_mode("live", source, now="2026-09-24T19:00:00Z") == "live"
    assert select_mode("live", source, now="2026-09-24T20:01:00Z") == "full"
    assert select_mode("live", source, now="2026-09-25T00:02:00Z") == "full"


def test_completed_close_snapshot_can_be_reused_next_session_and_weekend():
    source = manifest("2026-09-25T21:00:00Z")
    assert select_mode("live", source, now="2026-09-26T12:00:00Z") == "live"
    assert select_mode("live", source, now="2026-09-28T15:00:00Z") == "live"
    assert select_mode("live", source, now="2026-09-28T20:01:00Z") == "full"


def test_missing_baseline_requires_full_and_bad_timestamps_block():
    assert select_mode("live", None, now="2026-09-25T01:00:00Z") == "full"
    assert select_mode("full", None) == "full"
    for at in (None, "2026-09-26T01:00:00Z", "2026-09-24"):
        with pytest.raises(RuntimeError):
            select_mode("live", manifest(at), now="2026-09-25T01:00:00Z")
