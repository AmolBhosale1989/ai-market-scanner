import json
from pathlib import Path

import pandas as pd

from scanner import quant_shadow


def candidate(ticker: str, score: float = 80, **overrides):
    row = {
        "ticker": ticker, "price": 100.0, "median_dollar_volume20": 100_000_000,
        "liquidity_gate_passed": True, "negative_catalyst_risk": False, "risk_score": 20,
        "ema20": 98, "ema50": 92, "ema200": 80, "rs20_vs_spy": score/10,
        "rvol": 1.8, "theme_score": score, "technical_score": score,
        "formation_score": score, "rsi14": 58, "adr20_pct": 3,
        "distance_to_20d_high_pct": 2, "extension_above_20d_high_pct": 0,
        "retest_quality_score": score, "higher_low": True, "bullish_close": True,
        "sector_regime_ok": True, "theme_match_confidence": score,
        "market_hunt_score": score, "stop": 97, "atr_pct": 3,
    }
    row.update(overrides)
    return row


def test_builds_isolated_deterministic_strategy_cohorts():
    frame = pd.DataFrame([candidate(f"Q{i}", 90-i) for i in range(12)])
    first = quant_shadow.build_signals(frame, "2026-09-18", top_k=3)
    second = quant_shadow.build_signals(frame.sample(frac=1, random_state=9), "2026-09-18", top_k=3)

    assert first["signal_id"].tolist() == second["signal_id"].tolist()
    assert set(quant_shadow.STRATEGIES).issubset(set(first["strategy"]))
    assert quant_shadow.ENSEMBLE in set(first["strategy"])
    assert quant_shadow.BASELINE in set(first["strategy"])
    assert first["broker_execution_enabled"].eq(False).all()
    assert first["production_applied"].eq(False).all()
    assert first["market_data_source"].eq("POSTGRESQL_DERIVED_SHADOW").all()


def test_same_session_cohort_is_immutable():
    first = quant_shadow.build_signals(pd.DataFrame([candidate("AAA")]), "2026-09-18", top_k=1)
    ledger = quant_shadow.freeze_cohort({}, first, "2026-09-18T21:00:00+00:00")
    original = json.loads(json.dumps(ledger))
    changed = first.copy()
    changed["reference_close"] = 999
    rerun = quant_shadow.freeze_cohort(ledger, changed, "2026-09-18T22:00:00+00:00")
    assert rerun == original


def test_empty_restored_ledger_starts_clean(memory_control_plane):
    assert memory_control_plane["states"].get(("quant_shadow", "ledger"), {}) == {}


def test_forward_resolution_enters_next_session_and_stop_wins_collision():
    signals = quant_shadow.build_signals(pd.DataFrame([candidate("AAA")]), "2026-09-18", top_k=1)
    # Keep one model record so the outcome is unambiguous.
    signals = signals[signals["strategy"].eq(quant_shadow.BASELINE)]
    ledger = quant_shadow.freeze_cohort({}, signals, "2026-09-18T21:00:00+00:00")
    history = pd.DataFrame({
        "Open": [100, 101], "High": [101, 110], "Low": [99, 95], "Close": [100, 106],
    }, index=pd.to_datetime(["2026-09-18", "2026-09-21"], utc=True))
    resolved = next(iter(quant_shadow.resolve_ledger(ledger, {"AAA": history}).values()))

    assert resolved["entry_session"] == "2026-09-21"
    assert resolved["entry_price"] == 101
    assert resolved["exit_reason"] == "STOP"
    assert resolved["status"] == "CLOSED"


def test_seven_session_expiry_tracks_forward_outcomes():
    signals = quant_shadow.build_signals(pd.DataFrame([candidate("AAA")]), "2026-09-18", top_k=1)
    signals = signals[signals["strategy"].eq(quant_shadow.BASELINE)]
    ledger = quant_shadow.freeze_cohort({}, signals, "2026-09-18T21:00:00+00:00")
    dates = pd.date_range("2026-09-21", periods=7, freq="B", tz="UTC")
    history = pd.DataFrame({
        "Open": [100]*7, "High": [102, 103, 104, 105, 106, 107, 107],
        "Low": [99]*7, "Close": [101, 102, 103, 104, 105, 106, 107],
    }, index=dates)
    resolved = next(iter(quant_shadow.resolve_ledger(ledger, {"AAA": history}).values()))
    assert resolved["status"] == "EXPIRED"
    assert resolved["return_5d_pct"] == 5.0
    assert resolved["return_7d_pct"] == 7.0
    assert resolved["hit_5pct"]
    assert not resolved["hit_10pct"]


def test_promotion_gate_requires_sample_expectancy_and_profit_factor():
    records = {}
    for i in range(29):
        records[str(i)] = {"strategy": quant_shadow.ENSEMBLE, "status": "CLOSED", "r_multiple": 1.0,
                           "hit_5pct": True, "hit_10pct": False, "hit_15pct": False}
    before = quant_shadow.performance_table(records).set_index("strategy")
    assert not bool(before.loc[quant_shadow.ENSEMBLE, "manual_review_eligible"])
    records["29"] = {"strategy": quant_shadow.ENSEMBLE, "status": "CLOSED", "r_multiple": 1.0,
                     "hit_5pct": True, "hit_10pct": False, "hit_15pct": False}
    after = quant_shadow.performance_table(records).set_index("strategy")
    assert bool(after.loc[quant_shadow.ENSEMBLE, "manual_review_eligible"])
    assert not bool(after.loc[quant_shadow.ENSEMBLE, "production_applied"])


def test_daily_workflow_restores_runs_and_publishes_shadow_ledger():
    workflow = Path(".github/workflows/production.yml").read_text()
    assert "seed_snapshot" in workflow
    assert "python -m scanner.quant_shadow" in workflow
    assert "scanner.quant_shadow" in workflow


def test_daily_publication_gate_requires_quant_shadow_artifacts():
    from scanner.production_telemetry import REQUIRED_DATASETS
    assert {"quant_shadow_signals", "quant_shadow_ledger",
            "quant_shadow_performance", "quant_shadow_health"}.issubset(REQUIRED_DATASETS)
