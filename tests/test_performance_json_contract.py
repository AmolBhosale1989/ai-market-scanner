import json

import pandas as pd

from scanner import performance
from scanner.control_plane import _canonical


def test_no_loss_profit_factor_is_explicit_and_json_safe(monkeypatch):
    writes = {}
    monkeypatch.setattr(
        performance,
        "write_dataset",
        lambda name, frame, **_kwargs: writes.update({name: frame.copy()}),
    )
    journal = pd.DataFrame(
        [
            {"ticker": "AAA", "outcome": "TARGET_HIT", "r_multiple": 2.0},
            {"ticker": "BBB", "outcome": "TARGET_HIT", "r_multiple": 1.0},
        ]
    )

    summary, _ = performance.build_performance_reports(journal)

    assert summary.loc[0, "profit_factor_r"] == "NO_LOSSES"
    payload = _canonical(summary.iloc[0].to_dict())
    assert json.loads(payload)["profit_factor_r"] == "NO_LOSSES"
    assert "Infinity" not in payload
    assert writes["performance_summary"].loc[0, "profit_factor_r"] == "NO_LOSSES"


def test_finite_profit_factor_is_unchanged(monkeypatch):
    monkeypatch.setattr(performance, "write_dataset", lambda *_args, **_kwargs: None)
    journal = pd.DataFrame(
        [
            {"ticker": "AAA", "outcome": "TARGET_HIT", "r_multiple": 2.0},
            {"ticker": "BBB", "outcome": "FAILED_BREAKOUT", "r_multiple": -1.0},
        ]
    )

    summary, _ = performance.build_performance_reports(journal)

    assert summary.loc[0, "profit_factor_r"] == 2.0


def test_no_loss_marker_keeps_readiness_gate_closed(monkeypatch):
    from scanner import readiness

    records = {
        "performance_summary": {
            "closed_signals": 30,
            "win_rate_pct": 100.0,
            "avg_r_multiple": 1.0,
            "profit_factor_r": "NO_LOSSES",
        },
        "monitor_health": {"status": "HEALTHY"},
    }
    monkeypatch.setattr(readiness, "_first", lambda name: records[name])
    monkeypatch.setattr(
        readiness,
        "read_dataset",
        lambda *_args, **_kwargs: pd.DataFrame(
            [{"calibration_status": "USABLE"}]
        ),
    )
    written = {}
    monkeypatch.setattr(
        readiness, "write_record", lambda name, payload: written.update({name: payload})
    )

    result = readiness.build_readiness_gate()

    assert result["gate_status"] == "PAPER_VALIDATION"
    assert not result["ready_for_real_money"]
    assert "profit_factor" in result["failed_checks"]
    assert written["validation_gate"] == result
