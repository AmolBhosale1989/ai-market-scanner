from datetime import datetime, timezone

import pandas as pd

from scanner.v9_pilot import build_pilot_rehearsal, run

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def put(memory, name, value):
    frame = value if isinstance(value, pd.DataFrame) else pd.DataFrame([value])
    memory["datasets"][(memory["run_id"], name)] = frame


def seed(memory, eligible=True):
    put(memory, "v9_readiness", {"eligible_for_manual_review": eligible})
    put(memory, "v8_2_evidence_scorecard", {"status": "V9_REVIEW_READY"})
    put(memory, "v7_allocation_health", {"paper_only": True, "broker_execution_enabled": False})
    put(memory, "v7_paper_portfolio", pd.DataFrame([
        {"ticker": "AAA", "entry_trigger": 10, "stop": 9.5, "effective_rr": 3.0, "v7_paper_shares": 100},
        {"ticker": "BBB", "entry_trigger": 20, "stop": 19, "effective_rr": 2.8, "v7_paper_shares": 50},
        {"ticker": "CCC", "entry_trigger": 30, "stop": 29, "effective_rr": 2.6, "v7_paper_shares": 25},
    ]))


def test_blocked_readiness_produces_no_candidate_or_order(memory_control_plane):
    seed(memory_control_plane, False)
    candidates, health = build_pilot_rehearsal(now=NOW)
    assert candidates.empty
    assert health["orders_generated"] == 0


def test_ready_packet_is_bounded_and_contains_no_share_instruction(memory_control_plane):
    seed(memory_control_plane)
    candidates, health = build_pilot_rehearsal(now=NOW)
    assert list(candidates["ticker"]) == ["AAA", "BBB"]
    assert "v7_paper_shares" not in candidates.columns
    assert set(candidates["v9_action"]) == {"MANUAL_REVIEW_ONLY"}
    assert health["live_orders_enabled"] is False


def test_run_writes_empty_fail_closed_datasets(memory_control_plane):
    candidates, health = run()
    assert candidates.empty
    assert health["status"] == "BLOCKED_PAPER_REHEARSAL"
    assert (memory_control_plane["run_id"], "v9_1_pilot_candidates") in memory_control_plane["datasets"]
