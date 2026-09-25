from pathlib import Path

import pandas as pd

from scanner.workflow_continuation import should_continue_live_session


def test_continues_during_regular_session_with_safe_runtime_buffer():
    decision, reason = should_continue_live_session(pd.Timestamp("2026-09-24T17:15:00Z"))
    assert decision is True
    assert reason.startswith("market_open_")


def test_stops_before_close_when_another_pipeline_cannot_finish_safely():
    decision, reason = should_continue_live_session(pd.Timestamp("2026-09-24T19:41:00Z"))
    assert decision is False
    assert reason.startswith("closing_buffer_")


def test_stops_outside_exchange_session_and_on_weekend():
    assert should_continue_live_session(pd.Timestamp("2026-09-24T12:00:00Z"))[0] is False
    assert should_continue_live_session(pd.Timestamp("2026-09-26T16:00:00Z"))[0] is False


def test_workflow_chains_only_after_successful_live_publication():
    workflow = Path(".github/workflows/production.yml").read_text()
    assert "actions: write" in workflow
    assert "if: ${{ success() }}" in workflow
    assert "python -m scanner.workflow_continuation" in workflow
    assert "steps.live_continuation.outputs.dispatch == 'true'" in workflow
    assert "gh workflow run production.yml --ref main -f mode=live" in workflow
    assert workflow.index("production_telemetry --finalize --mode production") < workflow.index(
        "python -m scanner.workflow_continuation"
    )
