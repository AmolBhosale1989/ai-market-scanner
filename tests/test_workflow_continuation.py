from pathlib import Path

import pandas as pd
import pytest

from scanner import workflow_continuation as continuation
from scanner.workflow_continuation import plan_continuation, should_continue_live_session


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


def test_failed_publication_recovers_with_backoff_and_bounded_attempts():
    now = pd.Timestamp("2026-10-07T18:00:00Z")
    for attempt in range(3):
        plan = plan_continuation(now, result="failure", recovery_attempt=attempt)
        assert plan.dispatch is True
        assert plan.recovery_attempt == attempt + 1
        assert plan.delay_seconds == 300
    exhausted = plan_continuation(now, result="failure", recovery_attempt=3)
    assert exhausted.dispatch is False
    assert exhausted.reason == "recovery_limit_reached"


def test_success_resets_recovery_counter_without_delay():
    plan = plan_continuation(pd.Timestamp("2026-10-07T18:00:00Z"), recovery_attempt=3)
    assert plan.dispatch is True
    assert plan.recovery_attempt == 0
    assert plan.delay_seconds == 0


@pytest.mark.parametrize("result", ["cancelled", "skipped"])
def test_cancelled_or_skipped_run_never_spawns_a_successor(result):
    plan = plan_continuation(pd.Timestamp("2026-10-07T18:00:00Z"), result=result)
    assert plan.dispatch is False
    assert plan.delay_seconds == 0


@pytest.mark.parametrize("now", ["2026-10-10T18:00:00Z", "2026-10-07T19:41:00Z", "2026-11-27T17:41:00Z"])
def test_failure_does_not_override_weekends_regular_close_or_early_close(now):
    assert plan_continuation(pd.Timestamp(now), result="failure").dispatch is False


@pytest.mark.parametrize("attempt", [-1, 4, 1.5, True, "1"])
def test_invalid_recovery_counter_is_rejected(attempt):
    with pytest.raises(ValueError, match="Invalid recovery"):
        plan_continuation(recovery_attempt=attempt)


def test_unknown_result_is_rejected():
    with pytest.raises(ValueError, match="Unknown workflow"):
        plan_continuation(result="unknown")


def test_cli_rechecks_closing_buffer_after_failure_backoff(monkeypatch, capsys):
    times = iter([pd.Timestamp("2026-10-07T19:37:00Z"), pd.Timestamp("2026-10-07T19:42:00Z")])
    original = continuation.plan_continuation
    monkeypatch.setattr(continuation, "plan_continuation", lambda **kw: original(next(times), **kw))
    waits = []
    monkeypatch.setattr(continuation.time, "sleep", waits.append)
    continuation.main(["--result", "failure", "--recovery-attempt", "0", "--wait-before-retry"])
    output = capsys.readouterr()
    assert waits == [300]
    assert "dispatch=false" in output.out
    assert "closing_buffer_18.0m" in output.out
    assert "::warning::" in output.err


def test_cli_emits_successor_counter_after_backoff(monkeypatch, capsys):
    original = continuation.plan_continuation
    monkeypatch.setattr(continuation, "plan_continuation", lambda **kw: original(pd.Timestamp("2026-10-07T18:00:00Z"), **kw))
    waits = []
    monkeypatch.setattr(continuation.time, "sleep", waits.append)
    continuation.main(["--result", "failure", "--recovery-attempt", "1", "--wait-before-retry"])
    output = capsys.readouterr()
    assert waits == [300]
    assert "dispatch=true" in output.out
    assert "recovery_attempt=2" in output.out


def test_cli_reports_exhaustion_without_sleep_or_dispatch(monkeypatch, capsys):
    original = continuation.plan_continuation
    monkeypatch.setattr(continuation, "plan_continuation", lambda **kw: original(pd.Timestamp("2026-10-07T18:00:00Z"), **kw))
    waits = []
    monkeypatch.setattr(continuation.time, "sleep", waits.append)
    continuation.main(["--result", "failure", "--recovery-attempt", "3", "--wait-before-retry"])
    output = capsys.readouterr()
    assert waits == []
    assert "dispatch=false" in output.out
    assert "::error::Automatic production recovery exhausted" in output.err


def test_workflow_recovers_without_hiding_failure_or_overriding_cancellation():
    workflow = Path(".github/workflows/production.yml").read_text()
    assert "actions: write" in workflow
    assert "if: ${{ !cancelled() && github.ref == 'refs/heads/main' }}" in workflow
    assert "!cancelled() && github.ref == 'refs/heads/main' && steps.live_continuation.outputs.dispatch == 'true'" in workflow
    assert "PREVIOUS_RESULT: ${{ job.status }}" in workflow
    assert "RECOVERY_ATTEMPT: ${{ inputs.recovery_attempt || 0 }}" in workflow
    assert '--result "$PREVIOUS_RESULT"' in workflow
    assert "--wait-before-retry" in workflow
    assert 'recovery_attempt="$NEXT_RECOVERY_ATTEMPT"' in workflow
    assert "continue-on-error" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "python -m scanner.workflow_continuation" in workflow
    assert "steps.live_continuation.outputs.dispatch == 'true'" in workflow
    assert "gh workflow run production.yml --ref main -f mode=live" in workflow
    assert workflow.index("production_telemetry --finalize --mode production") < workflow.index(
        "python -m scanner.workflow_continuation"
    )
