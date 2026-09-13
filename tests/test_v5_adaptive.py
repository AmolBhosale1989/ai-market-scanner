from datetime import datetime, timedelta, timezone

import pandas as pd

from scanner.v5.adaptive import AdaptiveSettings, fit_adaptive_model


def outcomes(n=140):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        strong = i % 2 == 0
        rows.append({
            "ticker": f"T{i}", "entered_at_utc": (start + timedelta(days=i)).isoformat(),
            "market_hunt_score": 85 if strong else 65, "technical_score": 85 if strong else 60,
            "catalyst_score": 70 if strong else 30, "effective_rr": 3.5 if strong else 2.2,
            "intraday_rvol": 2.2 if strong else 1.0, "theme_score": 80 if strong else 45,
            "stage": "ARMED", "market_regime_state": "STRONG" if strong else "RISK_OFF",
            "entry_model": "BREAKOUT" if strong else "RETEST", "theme": "SEMIS" if strong else "BIOTECH",
            "catalyst_type": "EARNINGS" if strong else "NONE",
            "forward_hit_5pct": strong or i % 7 == 0,
            "forward_hit_10pct": strong and i % 3 != 0,
            "forward_hit_15pct": strong and i % 5 == 0,
        })
    return pd.DataFrame(rows)


def test_v5_refuses_small_samples():
    model, validation = fit_adaptive_model(outcomes(20))
    assert model.status == "INSUFFICIENT_DATA"
    assert validation.empty


def test_v5_time_split_probabilities_and_version_are_bounded():
    model, validation = fit_adaptive_model(outcomes(), AdaptiveSettings(min_segment_samples=5))
    assert model.version.startswith("v5-")
    assert model.payload["train_end_utc"] < model.payload["validation_start_utc"]
    assert set(validation["validation_gate"]).issubset({"PASS", "FAIL"})
    scored = model.score(outcomes(4))
    for column in ("v5_p5_probability", "v5_p10_probability", "v5_p15_probability"):
        assert scored[column].between(0.5, 99.5).all()
    strong = scored[scored["market_regime_state"] == "STRONG"].iloc[0]
    weak = scored[scored["market_regime_state"] == "RISK_OFF"].iloc[0]
    assert strong["v5_p5_probability"] > weak["v5_p5_probability"]
    repeated, _ = fit_adaptive_model(outcomes(), AdaptiveSettings(min_segment_samples=5))
    assert repeated.version == model.version
