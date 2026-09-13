import pandas as pd

from scanner.v4.model_monitor import evaluate_model_health


def model():
    return {
        "model_version": "v4.5-test",
        "targets": {
            "p5": {"global_probability": 0.60},
            "p10": {"global_probability": 0.40},
            "p15": {"global_probability": 0.20},
        },
    }


def observations(degraded=False):
    rows = []
    for i in range(40):
        hit5 = i % 10 < (2 if degraded else 7)
        hit10 = i % 10 < (1 if degraded else 4)
        hit15 = i % 10 < (0 if degraded else 2)
        rows.append({
            "ticker": f"T{i}", "as_of_session": f"2026-08-{i % 20 + 1:02d}",
            "daily_bars_resolved": 5, "selected_v45": True, "selected_v3": True,
            "v45_rank": i % 20 + 1, "v45_model_version": "v4.5-test",
            "v45_model_status": "VALIDATED_SHADOW",
            "v45_p5_probability": 95 if degraded else 70,
            "v45_p10_probability": 90 if degraded else 40,
            "v45_p15_probability": 85 if degraded else 20,
            "forward_hit_5pct": hit5, "forward_hit_10pct": hit10, "forward_hit_15pct": hit15,
            "r_multiple_5d": -0.5 if degraded else 0.8,
            "false_breakout": degraded or i % 10 == 0,
        })
    return pd.DataFrame(rows)


def test_monitor_collects_before_minimum_sample():
    assert evaluate_model_health(observations().head(10), model())["status"] == "COLLECTING"


def test_monitor_accepts_healthy_recent_evidence():
    health = evaluate_model_health(observations(), model())
    assert health["status"] == "HEALTHY"
    assert health["failed_checks"] == []


def test_monitor_marks_calibration_and_expectancy_degraded():
    health = evaluate_model_health(observations(degraded=True), model())
    assert health["status"] == "DEGRADED"
    assert "P5_CALIBRATION" in health["failed_checks"]
    assert "NEGATIVE_EXPECTANCY" in health["failed_checks"]
    assert "FALSE_BREAKOUT_RATE" in health["failed_checks"]
