from datetime import datetime, timedelta, timezone

import pandas as pd

from scanner.v6.uncertainty import UncertaintySettings, fit_uncertainty_model


def outcomes(n=280):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        strong = i % 3 != 0
        rows.append({
            "ticker": f"T{i}",
            "entered_at_utc": (start + timedelta(days=i)).isoformat(),
            "market_hunt_score": 88 if strong else 58,
            "technical_score": 86 if strong else 52,
            "catalyst_score": 75 if strong else 20,
            "effective_rr": 3.8 if strong else 1.9,
            "intraday_rvol": 2.4 if strong else 0.8,
            "theme_score": 82 if strong else 38,
            "stage": "ARMED" if strong else "WATCH",
            "market_regime_state": "STRONG" if strong else "RISK_OFF",
            "entry_model": "BREAKOUT" if strong else "RETEST",
            "theme": "SEMIS" if strong else "BIOTECH",
            "catalyst_type": "EARNINGS" if strong else "NONE",
            "forward_hit_5pct": strong or i % 11 == 0,
            "forward_hit_10pct": strong and i % 4 != 0,
            "forward_hit_15pct": strong and i % 7 == 0,
        })
    return pd.DataFrame(rows)


def test_v6_refuses_small_samples():
    model, validation = fit_uncertainty_model(outcomes(40))
    assert model.status == "INSUFFICIENT_DATA"
    assert validation.empty


def test_v6_uses_three_ordered_windows_and_stable_version():
    settings = UncertaintySettings(min_total_samples=240, min_calibration_samples=30, min_validation_samples=30)
    model, validation = fit_uncertainty_model(outcomes(), settings)
    assert model.version.startswith("v6-")
    assert model.payload["train_end_utc"] < model.payload["calibration_start_utc"]
    assert model.payload["calibration_end_utc"] < model.payload["validation_start_utc"]
    assert set(validation["validation_gate"]).issubset({"PASS", "FAIL"})
    repeated, _ = fit_uncertainty_model(outcomes(), settings)
    assert repeated.version == model.version


def test_v6_probabilities_intervals_and_abstention_are_bounded():
    model, _ = fit_uncertainty_model(outcomes())
    scored = model.score(outcomes(12))
    assert not scored.empty
    for target in ("p5", "p10", "p15"):
        assert scored[f"v6_{target}_probability"].between(0.5, 99.5).all()
        assert (scored[f"v6_{target}_lower"] <= scored[f"v6_{target}_probability"]).all()
        assert (scored[f"v6_{target}_probability"] <= scored[f"v6_{target}_upper"]).all()
    assert set(scored["v6_decision"]).issubset({"RANK", "ABSTAIN"})
    assert (scored.loc[scored["v6_decision"].eq("ABSTAIN"), "v6_robust_score"] == 0).all()


def test_v6_keeps_daily_batches_in_distinct_three_way_windows():
    frame = outcomes(400)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    frame["entered_at_utc"] = [
        (start + timedelta(days=i // 20)).isoformat() for i in range(len(frame))
    ]
    model, _ = fit_uncertainty_model(frame)
    assert model.status != "INSUFFICIENT_DATA"
    assert model.payload["train_end_utc"] < model.payload["calibration_start_utc"]
    assert model.payload["calibration_end_utc"] < model.payload["validation_start_utc"]
