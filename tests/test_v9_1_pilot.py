from datetime import datetime, timezone
import json

import pandas as pd

from scanner.v9_pilot import build_pilot_rehearsal, run


NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def write(path, value):
    path.write_text(json.dumps(value))


def seed(output, eligible=True):
    output.mkdir()
    write(output / "v9_readiness.json", {"eligible_for_manual_review": eligible})
    write(output / "v8_2_evidence_scorecard.json", {"status": "V9_REVIEW_READY"})
    write(output / "v7_allocation_health.json", {"paper_only": True, "broker_execution_enabled": False})
    pd.DataFrame([
        {"ticker": "AAA", "entry_trigger": 10, "stop": 9.5, "effective_rr": 3.0, "v7_paper_shares": 100},
        {"ticker": "BBB", "entry_trigger": 20, "stop": 19, "effective_rr": 2.8, "v7_paper_shares": 50},
        {"ticker": "CCC", "entry_trigger": 30, "stop": 29, "effective_rr": 2.6, "v7_paper_shares": 25},
    ]).to_csv(output / "v7_paper_portfolio.csv", index=False)


def test_blocked_readiness_produces_no_candidate_or_order(tmp_path):
    output = tmp_path / "outputs"
    seed(output, eligible=False)
    candidates, health = build_pilot_rehearsal(output, now=NOW)
    assert candidates.empty
    assert health["status"] == "BLOCKED_PAPER_REHEARSAL"
    assert health["orders_generated"] == 0
    assert health["broker_execution_enabled"] is False


def test_ready_packet_is_bounded_and_contains_no_share_instruction(tmp_path):
    output = tmp_path / "outputs"
    seed(output)
    candidates, health = build_pilot_rehearsal(output, now=NOW)
    assert list(candidates["ticker"]) == ["AAA", "BBB"]
    assert "v7_paper_shares" not in candidates.columns
    assert set(candidates["v9_action"]) == {"MANUAL_REVIEW_ONLY"}
    assert health["manual_approval_required"] is True
    assert health["live_orders_enabled"] is False
    assert health["orders_generated"] == 0


def test_run_writes_empty_fail_closed_artifacts_when_inputs_missing(tmp_path):
    output = tmp_path / "outputs"
    candidates, health = run(output)
    assert candidates.empty
    assert health["status"] == "BLOCKED_PAPER_REHEARSAL"
    assert (output / "v9_1_pilot_candidates.csv").exists()
    assert (output / "v9_1_pilot_health.json").exists()
