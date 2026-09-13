import json
from pathlib import Path

import pandas as pd
import pytest

from scanner.v4.cutover import (
    PRIMARY_MODE,
    SHADOW_MODE,
    CutoverController,
    CutoverSettings,
    apply_active_ranking,
    evaluate_cutover,
    rollback_drill,
)


def candidates():
    return pd.DataFrame([
        {"ticker": f"T{i}", "market_hunt_score": 100-i, "technical_score": 80, "catalyst_score": 70,
         "effective_rr": 3.5, "intraday_rvol": 2.0, "theme_score": 75, "stage": "ARMED",
         "market_regime_state": "STRONG", "entry_model": "RESISTANCE_BREAKOUT", "theme": "TEST"}
        for i in range(30)
    ])


def outcomes(n=40):
    return pd.DataFrame([
        {
            "ticker": f"T{i}",
            "entered_at_utc": "2026-09-01T14:00:00+00:00",
            "daily_bars_resolved": 5,
            "forward_hit_5pct": i < 28,
        }
        for i in range(n)
    ])


def decision_inputs():
    v3 = candidates()
    v45 = v3.copy()
    v45["v45_calibrated_score"] = list(range(100, 70, -1))
    model = {"promotion_status": "VALIDATED_SHADOW", "model_version": "v4.5-test"}
    health = {"market_hours_uptime_pct": 99.0, "event_lag_p95_ms": 1000.0}
    return v3, v45, outcomes(), model, health


def test_v46_all_gates_pass_and_rollback_drill_is_real():
    v3, v45, out, model, health = decision_inputs()
    decision = evaluate_cutover(v3, v45, out, model, health)
    assert decision.eligible
    assert decision.status == "ELIGIBLE_FOR_MANUAL_CUTOVER"
    assert decision.rollback_drill_passed
    assert rollback_drill()


def test_v46_fails_closed_when_precision_or_uptime_missing():
    v3, v45, out, model, health = decision_inputs()
    health = {}
    out["forward_hit_5pct"] = False
    decision = evaluate_cutover(v3, v45, out, model, health)
    assert not decision.eligible
    assert "ALERT_PRECISION" in decision.failed_gates
    assert "MARKET_UPTIME" in decision.failed_gates
    assert "EVENT_LAG" in decision.failed_gates


def test_v46_blocks_degraded_model_monitor():
    v3, v45, out, model, health = decision_inputs()
    health["model_monitor_status"] = "DEGRADED"
    decision = evaluate_cutover(v3, v45, out, model, health)
    assert not decision.eligible
    assert decision.model_monitor_status == "DEGRADED"
    assert "MODEL_MONITOR" in decision.failed_gates


def test_v46_controller_refuses_activation_without_pass(tmp_path):
    v3, v45, out, model, health = decision_inputs()
    decision = evaluate_cutover(
        v3.head(1), v45.head(1), out.head(1), model, health,
        CutoverSettings(min_alert_samples=30),
    )
    controller = CutoverController(tmp_path / "state.json")
    with pytest.raises(RuntimeError):
        controller.activate(decision, "v4.5-test")
    assert controller.state()["mode"] == SHADOW_MODE


def test_v46_activate_then_rollback(tmp_path):
    v3, v45, out, model, health = decision_inputs()
    decision = evaluate_cutover(v3, v45, out, model, health)
    controller = CutoverController(tmp_path / "state.json")
    active = controller.activate(decision, "v4.5-test")
    assert active["mode"] == PRIMARY_MODE
    rolled = controller.rollback("drill")
    assert rolled["mode"] == SHADOW_MODE
    assert rolled["rollback_reason"] == "drill"


def test_active_ranking_falls_back_on_model_mismatch(tmp_path):
    state = tmp_path / "cutover.json"
    state.write_text(json.dumps({
        "mode": PRIMARY_MODE,
        "active_model_version": "different",
    }))
    model = tmp_path / "model.json"
    model.write_text(json.dumps({
        "model_version": "v4.5-test",
        "promotion_status": "VALIDATED_SHADOW",
        "targets": {},
    }))
    frame, mode = apply_active_ranking(candidates(), state, model)
    assert mode == SHADOW_MODE
    assert "v4_active_rank_score" not in frame.columns
