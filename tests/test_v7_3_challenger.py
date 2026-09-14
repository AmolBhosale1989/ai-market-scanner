import pandas as pd

from scanner.v7.challenger import ChallengerSettings, evaluate_challenger


def proposal(status="PROPOSAL_ELIGIBLE"):
    return {
        "schema_version": "7.2.0-shadow",
        "model_version": "v7.2-test",
        "status": status,
        "created_at_utc": "2026-01-10T22:00:00+00:00",
        "recommended_change": {
            "criterion": "market_hunt_score",
            "operator": ">=",
            "proposed_value": 70.0,
        },
    }


def rows(start="2026-01-02", sessions=4):
    values = []
    for session in pd.date_range(start, periods=sessions, freq="B"):
        for index in range(10):
            strong = index < 5
            values.append({
                "ticker": f"T{index}",
                "as_of_session": session.date().isoformat(),
                "daily_bars_resolved": 5,
                "market_hunt_score": 80 if strong else 60,
                "forward_hit_5pct": strong,
                "forward_hit_10pct": strong and index % 2 == 0,
                "forward_hit_15pct": strong and index % 3 == 0,
                "r_multiple_5d": 2 if strong else -1,
                "false_breakout": not strong,
            })
    return pd.DataFrame(values)


def settings():
    return ChallengerSettings(min_future_sessions=2, min_baseline_samples=20, min_challenger_samples=10)


def test_challenger_waits_for_valid_allowlisted_proposal():
    state, comparison = evaluate_challenger(proposal("INSUFFICIENT_DATA"), rows(), settings=settings())
    assert state["status"] == "WAITING_FOR_PROPOSAL"
    assert state["active_challenger"] is None
    assert state["production_applied"] is False
    assert state["activation_allowed"] is False
    assert comparison.empty


def test_challenger_rejects_change_outside_committed_allowlist():
    invalid = proposal()
    invalid["recommended_change"]["criterion"] = "arbitrary_python_setting"
    state, _ = evaluate_challenger(invalid, rows(), settings=settings())
    assert state["status"] == "WAITING_FOR_PROPOSAL"
    assert state["active_challenger"] is None


def test_registration_excludes_all_existing_sessions():
    state, comparison = evaluate_challenger(proposal(), rows(), settings=settings())
    assert state["status"] == "COLLECTING_FUTURE"
    assert state["active_challenger"]["start_after_session"] == "2026-01-10"
    assert state["future_sessions"] == 0
    assert comparison.empty


def test_future_only_challenger_validates_and_freezes_result():
    historical = rows()
    state, _ = evaluate_challenger(proposal(), historical, settings=settings())
    future = pd.concat([historical, rows("2026-01-12", 2)], ignore_index=True)
    evaluated, comparison = evaluate_challenger(proposal(), future, state, settings())
    assert evaluated["status"] == "CHALLENGER_VALIDATED"
    assert evaluated["future_sessions"] == 2
    assert evaluated["baseline_samples"] == 20
    assert evaluated["challenger_samples"] == 10
    assert evaluated["manual_review_required"] is True
    assert evaluated["production_applied"] is False
    assert evaluated["activation_allowed"] is False
    assert set(comparison["variant"]) == {"BASELINE", "CHALLENGER"}

    more_future = pd.concat([future, rows("2026-01-14", 2)], ignore_index=True)
    frozen, _ = evaluate_challenger(proposal(), more_future, evaluated, settings())
    assert frozen["evaluation_end_session"] == evaluated["evaluation_end_session"]
    assert frozen["baseline_samples"] == 20
