"""Objective promotion gate from paper validation to limited live testing."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR

MIN_CLOSED_SIGNALS = 30
MIN_WIN_RATE_PCT = 45.0
MIN_AVG_R = 0.25
MIN_PROFIT_FACTOR_R = 1.20
MIN_USABLE_CALIBRATION_BUCKETS = 1


def _first(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path)
        return frame.iloc[0].to_dict() if not frame.empty else {}
    except Exception:
        return {}


def _number(value, default=0.0) -> float:
    try:
        value = float(value)
        return value if pd.notna(value) else default
    except (TypeError, ValueError):
        return default


def build_readiness_gate() -> dict:
    performance = _first(OUTPUT_DIR / "performance_summary.csv")
    monitor = _first(OUTPUT_DIR / "monitor_health.csv")
    calibration_path = OUTPUT_DIR / "probability_calibration.csv"
    try:
        calibration = pd.read_csv(calibration_path)
    except Exception:
        calibration = pd.DataFrame()

    closed = int(_number(performance.get("closed_signals")))
    win_rate = _number(performance.get("win_rate_pct"))
    avg_r = _number(performance.get("avg_r_multiple"))
    profit_factor = _number(performance.get("profit_factor_r"))
    usable_buckets = (
        int(calibration["calibration_status"].astype(str).eq("USABLE").sum())
        if not calibration.empty and "calibration_status" in calibration else 0
    )
    checks = {
        "sample_size": closed >= MIN_CLOSED_SIGNALS,
        "win_rate": win_rate >= MIN_WIN_RATE_PCT,
        "expectancy": avg_r >= MIN_AVG_R,
        "profit_factor": profit_factor >= MIN_PROFIT_FACTOR_R,
        "calibration": usable_buckets >= MIN_USABLE_CALIBRATION_BUCKETS,
        "monitor_healthy": str(monitor.get("status", "")).upper() in {"PASS", "OK", "HEALTHY"},
    }
    ready = all(checks.values())
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "gate_status": "LIMITED_LIVE_TEST_READY" if ready else "PAPER_VALIDATION",
        "ready_for_real_money": ready,
        "closed_signals": closed,
        "min_closed_signals": MIN_CLOSED_SIGNALS,
        "win_rate_pct": round(win_rate, 2),
        "min_win_rate_pct": MIN_WIN_RATE_PCT,
        "avg_r_multiple": round(avg_r, 3),
        "min_avg_r_multiple": MIN_AVG_R,
        "profit_factor_r": round(profit_factor, 3),
        "min_profit_factor_r": MIN_PROFIT_FACTOR_R,
        "usable_calibration_buckets": usable_buckets,
        "min_usable_calibration_buckets": MIN_USABLE_CALIBRATION_BUCKETS,
        "monitor_status": str(monitor.get("status", "UNAVAILABLE")),
        "failed_checks": ",".join(name for name, passed in checks.items() if not passed),
        "live_test_max_risk_per_trade_pct": 0.50,
        "live_test_max_open_positions": 2,
        "live_test_broker_orders_enabled": False,
    }
    pd.DataFrame([payload]).to_csv(OUTPUT_DIR / "validation_gate.csv", index=False)
    (OUTPUT_DIR / "validation_gate.json").write_text(json.dumps(payload, indent=2))
    return payload


if __name__ == "__main__":
    result = build_readiness_gate()
    print(json.dumps(result, indent=2))
