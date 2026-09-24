import math

from scanner.dashboard_signal_card import signal_card_detail


def test_strict_recommendation_displays_its_score_and_reward_risk():
    assert signal_card_detail({
        "final_decision": "BUY",
        "market_hunt_score": 88.25,
        "effective_rr": 3.1,
    }) == "BUY · score 88.2 · R/R 3.10×"


def test_momentum_only_signal_does_not_invent_zero_score_or_reward_risk():
    detail = signal_card_detail({
        "signal": "MOMENTUM BUY",
        "price": 244.25,
        "rel_vs_spy_pct": 2.4,
        "intraday_rvol": 1.75,
    })
    assert detail == "MOMENTUM BUY · price $244.25 · vs SPY +2.40% · RVOL 1.75×"
    assert "score" not in detail
    assert "R/R" not in detail


def test_nonfinite_deep_metrics_fall_back_to_observed_momentum_fields():
    detail = signal_card_detail({
        "market_hunt_score": math.nan,
        "effective_rr": math.inf,
        "momentum_signal": "MOMENTUM BUY",
        "current_price": 7.67,
    })
    assert detail == "MOMENTUM BUY · price $7.67"
