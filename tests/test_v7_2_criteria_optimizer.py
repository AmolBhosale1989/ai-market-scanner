import pandas as pd

from scanner.v7.criteria_optimizer import (
    APPROVED_CRITERIA,
    CriteriaOptimizerSettings,
    fit_criteria_optimizer,
)


def outcomes(p5_for_all: bool = False) -> pd.DataFrame:
    rows = []
    for session_index, session in enumerate(pd.date_range("2026-01-02", periods=12, freq="B")):
        for candidate_index in range(20):
            strong = candidate_index < 8
            rows.append({
                "ticker": f"T{candidate_index:02d}",
                "evidence_session": session.date().isoformat(),
                "entered_at_utc": session.isoformat(),
                "market_hunt_score": 80.0 if strong else 60.0,
                "effective_rr": 3.0 if strong else 2.0,
                "technical_score": 80.0 if strong else 55.0,
                "catalyst_score": 45.0 if strong else 10.0,
                "intraday_rvol": 1.5 if strong else 0.8,
                "forward_hit_5pct": True if p5_for_all else strong,
                "forward_hit_10pct": strong and candidate_index % 2 == 0,
                "forward_hit_15pct": strong and candidate_index % 3 == 0,
                "r_multiple_5d": 2.0 if strong else -1.0,
                "false_breakout": not strong,
            })
    return pd.DataFrame(rows)


def test_optimizer_refuses_small_samples_and_cannot_activate():
    payload, validation, grid = fit_criteria_optimizer(outcomes().head(100))

    assert payload["status"] == "INSUFFICIENT_DATA"
    assert payload["production_applied"] is False
    assert payload["activation_allowed"] is False
    assert payload["broker_execution_enabled"] is False
    assert validation.empty
    assert grid.empty


def test_allowlist_never_relaxes_existing_hard_thresholds():
    assert min(APPROVED_CRITERIA["effective_rr"]) >= 2.5
    assert min(APPROVED_CRITERIA["catalyst_score"]) >= 30.0
    assert min(APPROVED_CRITERIA["intraday_rvol"]) >= 1.2
    assert min(APPROVED_CRITERIA["runway_to_next_resistance_pct"]) >= 5.0
    assert min(APPROVED_CRITERIA["adr20_pct"]) >= 2.0
    assert min(APPROVED_CRITERIA["avg_dollar_volume"]) >= 20_000_000.0
    assert min(APPROVED_CRITERIA["median_dollar_volume20"]) >= 25_000_000.0


def test_optimizer_selects_one_allowlisted_change_on_train_and_passes_holdout():
    payload, validation, grid = fit_criteria_optimizer(outcomes())

    assert payload["status"] == "PROPOSAL_ELIGIBLE"
    assert payload["recommended_change"]["criterion"] in APPROVED_CRITERIA
    assert payload["recommended_change"]["operator"] == ">="
    assert payload["recommended_change"]["proposed_value"] in APPROVED_CRITERIA[
        payload["recommended_change"]["criterion"]
    ]
    assert pd.Timestamp(payload["train_end_utc"]) < pd.Timestamp(payload["validation_start_utc"])
    assert set(validation["split"]) == {"TRAIN", "VALIDATION"}
    assert set(validation["variant"]) == {"BASELINE", "PROPOSAL"}
    assert payload["failed_gates"] == []
    assert payload["manual_review_required"] is True
    assert payload["production_applied"] is False
    assert payload["activation_allowed"] is False
    assert set(grid["criterion"]).issubset(APPROVED_CRITERIA)


def test_optimizer_does_not_modify_criteria_when_baseline_meets_target():
    payload, validation, grid = fit_criteria_optimizer(outcomes(p5_for_all=True))

    assert payload["status"] == "BASELINE_MEETS_TARGET"
    assert "recommended_change" not in payload
    assert payload["production_applied"] is False
    assert not validation.empty
    assert grid.empty


def test_optimizer_keeps_whole_sessions_out_of_training_holdout():
    settings = CriteriaOptimizerSettings(
        min_total_samples=100,
        min_validation_samples=20,
        min_candidate_samples=10,
        min_train_sessions=3,
        min_validation_sessions=2,
    )
    payload, _, _ = fit_criteria_optimizer(outcomes().head(180), settings=settings)

    assert payload["status"] in {"PROPOSAL_ELIGIBLE", "SHADOW_ONLY"}
    assert pd.Timestamp(payload["train_end_utc"]).date() < pd.Timestamp(payload["validation_start_utc"]).date()


def test_optimizer_version_is_deterministic():
    first, _, _ = fit_criteria_optimizer(outcomes())
    second, _, _ = fit_criteria_optimizer(outcomes())

    assert first["model_version"] == second["model_version"]
