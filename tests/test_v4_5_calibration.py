from datetime import datetime, timedelta, timezone

import pandas as pd

from scanner.v4.calibration import FitSettings, fit_model


def outcomes(n=120):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        strong = i % 4 in (0, 1, 2)
        rows.append({
            "ticker": f"T{i}",
            "entered_at_utc": (start + timedelta(days=i)).isoformat(),
            "market_hunt_score": 88 if strong else 52,
            "technical_score": 82 if strong else 48,
            "catalyst_score": 75 if strong else 20,
            "effective_rr": 3.8 if strong else 1.8,
            "intraday_rvol": 2.4 if strong else 0.7,
            "theme_score": 80 if strong else 35,
            "stage": "ARMED",
            "market_regime_state": "STRONG" if strong else "WEAK",
            "entry_model": "RESISTANCE_BREAKOUT",
            "theme": "SEMIS" if strong else "OTHER",
            "forward_hit_5pct": strong,
            "forward_hit_10pct": strong and i % 5 != 0,
            "forward_hit_15pct": strong and i % 3 == 0,
        })
    return pd.DataFrame(rows)


def test_v45_refuses_to_train_on_small_samples():
    model, validation = fit_model(outcomes(20))
    assert model.promotion_status == "INSUFFICIENT_DATA"
    assert model.version == "untrained"
    assert validation.empty


def test_v45_uses_time_split_and_emits_three_probabilities():
    frame = outcomes(120)
    model, validation = fit_model(
        frame,
        FitSettings(
            min_total_samples=60,
            min_validation_samples=15,
            min_positive_samples=5,
            validation_fraction=0.25,
            max_brier_degradation=0.02,
        ),
    )
    assert model.version.startswith("v4.5-")
    assert model.payload["train_end_utc"] < model.payload["validation_start_utc"]
    assert set(validation["target"]) == {"p5", "p10", "p15"}

    candidates = pd.DataFrame([
        {
            "ticker": "STRONG",
            "market_hunt_score": 90,
            "technical_score": 85,
            "catalyst_score": 80,
            "effective_rr": 4.0,
            "intraday_rvol": 2.8,
            "theme_score": 85,
            "stage": "ARMED",
            "market_regime_state": "STRONG",
            "entry_model": "RESISTANCE_BREAKOUT",
            "theme": "SEMIS",
        },
        {
            "ticker": "WEAK",
            "market_hunt_score": 50,
            "technical_score": 45,
            "catalyst_score": 10,
            "effective_rr": 1.7,
            "intraday_rvol": 0.6,
            "theme_score": 30,
            "stage": "FORMING",
            "market_regime_state": "WEAK",
            "entry_model": "RESISTANCE_BREAKOUT",
            "theme": "OTHER",
        },
    ])
    scored = model.score(candidates)
    for column in ("v45_p5_probability", "v45_p10_probability", "v45_p15_probability"):
        assert scored[column].between(0.5, 99.5).all()
    assert scored.iloc[0]["ticker"] == "STRONG"
    assert scored.iloc[0]["v45_calibrated_score"] > scored.iloc[1]["v45_calibrated_score"]


def test_v45_validation_gate_is_recorded_for_each_target():
    model, validation = fit_model(
        outcomes(100),
        FitSettings(min_total_samples=60, min_validation_samples=15, min_positive_samples=5),
    )
    assert len(validation) == 3
    assert set(validation["validation_gate"]).issubset({"PASS", "FAIL"})
    assert model.promotion_status in {"VALIDATED_SHADOW", "SHADOW_ONLY"}


def test_v45_model_version_is_stable_for_identical_evidence():
    settings = FitSettings(min_total_samples=60, min_validation_samples=15, min_positive_samples=5)
    first, _ = fit_model(outcomes(100), settings)
    second, _ = fit_model(outcomes(100), settings)
    assert first.version == second.version
