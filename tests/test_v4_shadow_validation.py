from pathlib import Path

import pandas as pd

from scanner.v4.shadow_validation import (
    ShadowValidationLedger,
    ShadowValidationSettings,
    infer_as_of_session,
)


def candidates():
    return pd.DataFrame([
        {
            "ticker": ticker,
            "price": price,
            "market_hunt_score": v3_score,
            "v45_calibrated_score": v45_score,
            "v45_p5_probability": probability,
            "v45_p10_probability": probability - 10,
            "v45_p15_probability": probability - 20,
            "stop": price * 0.95,
            "effective_target": price * 1.10,
            "effective_rr": 2.0,
            "theme": "SEMIS" if ticker != "C" else "BIOTECH",
            "catalyst_type": "NEWS",
            "market_regime_state": "STRONG",
            "live_session_date": "2026-09-01",
        }
        for ticker, price, v3_score, v45_score, probability in (
            ("A", 100.0, 99, 70, 70),
            ("B", 50.0, 98, 90, 80),
            ("C", 20.0, 70, 95, 85),
        )
    ])


def history(prices):
    dates = pd.date_range("2026-09-02", periods=5, freq="B")
    return pd.DataFrame({
        "High": [row[0] for row in prices],
        "Low": [row[1] for row in prices],
        "Close": [row[2] for row in prices],
    }, index=dates)


def ledger(tmp_path: Path):
    return ShadowValidationLedger(str(tmp_path), ShadowValidationSettings(top_k=2))


def test_snapshot_is_point_in_time_ranked_and_idempotent(tmp_path):
    frame = candidates()
    store = ledger(tmp_path)
    first = store.record_snapshot(
        frame,
        frame,
        "2026-09-01",
        "2026-09-01T21:00:00+00:00",
        {"model_version": "v4.5-test", "promotion_status": "VALIDATED_SHADOW"},
    )
    assert set(first.loc[first["selected_v3"], "ticker"]) == {"A", "B"}
    assert set(first.loc[first["selected_v45"], "ticker"]) == {"B", "C"}
    assert len(first) == 3

    changed = frame.copy()
    changed.loc[changed["ticker"] == "A", "market_hunt_score"] = 1
    second = store.record_snapshot(changed, changed, "2026-09-01")
    assert len(second) == 3
    assert second.set_index("ticker").loc["A", "v3_rank"] == 1


def test_snapshot_session_membership_is_frozen_on_rerun(tmp_path):
    frame = candidates()
    store = ledger(tmp_path)
    first = store.record_snapshot(
        frame,
        frame,
        "2026-09-01",
        "2026-09-01T20:00:00+00:00",
    )
    replacement = frame[frame["ticker"].eq("C")].copy()
    replacement.loc[:, "ticker"] = "NEW"

    second = store.record_snapshot(
        replacement,
        replacement,
        "2026-09-01",
        "2026-09-01T21:00:00+00:00",
    )

    assert len(second) == len(first)
    assert set(second["ticker"]) == set(first["ticker"])
    assert "NEW" not in set(second["ticker"])


def test_forward_resolution_and_strategy_comparison(tmp_path):
    frame = candidates()
    store = ledger(tmp_path)
    store.record_snapshot(frame, frame, "2026-09-01")
    resolved = store.resolve_histories({
        "A": history([(103, 98, 102), (106, 101, 105), (110, 104, 109), (112, 107, 110), (111, 108, 110)]),
        "B": history([(51, 49, 50), (52, 48, 49), (52, 47, 48), (51, 46, 47), (50, 45, 46)]),
        "C": history([(22, 20, 21), (23, 21, 22), (24, 22, 23), (25, 23, 24), (26, 24, 25)]),
    }).set_index("ticker")
    assert resolved.loc["A", "forward_hit_5pct"]
    assert not resolved.loc["B", "forward_hit_5pct"]
    assert resolved.loc["B", "false_breakout"]
    assert resolved.loc["C", "forward_hit_15pct"]

    summary = store._strategy_summary(resolved.reset_index()).set_index("strategy")
    assert summary.loc["V3", "forward_hit_5pct_rate"] == 50.0
    assert summary.loc["V4_5", "forward_hit_5pct_rate"] == 50.0
    assert pd.notna(summary.loc["V4_5", "p5_brier_score"])
    daily = store._daily(resolved.reset_index()).iloc[0]
    assert daily["top_k_agreement_pct"] == 50.0


def test_partial_history_does_not_create_mature_outcomes(tmp_path):
    frame = candidates()
    store = ledger(tmp_path)
    store.record_snapshot(frame, frame, "2026-09-01")
    partial = history([(103, 98, 102), (106, 101, 105), (110, 104, 109), (112, 107, 110), (111, 108, 110)]).head(2)
    resolved = store.resolve_histories({ticker: partial for ticker in ("A", "B", "C")})
    assert resolved["daily_bars_resolved"].eq(2).all()
    assert resolved["forward_hit_5pct"].isna().all()
    summary = store._strategy_summary(resolved)
    assert summary["mature_candidates"].eq(0).all()


def test_session_comes_from_market_session_not_weekend_publish_time():
    frame = candidates()
    assert infer_as_of_session(frame, "2026-09-06T02:00:00+00:00") == "2026-09-01"


def test_snapshot_captures_v5_v6_and_v7_paper_rankings(tmp_path):
    frame = candidates()
    frame["v5_adaptive_score"] = [90, 80, 70]
    frame["v6_robust_score"] = [85, 75, 65]
    frame["v6_p5_probability"] = 60
    frame["v6_p10_probability"] = 40
    frame["v6_p15_probability"] = 20
    frame["v6_confidence"] = "HIGH"
    frame["v6_decision"] = "RANK"
    frame["technical_score"] = [88, 77, 66]
    frame["catalyst_score"] = [45, 30, 20]
    frame["intraday_rvol"] = [2.0, 1.5, 1.2]
    frame["runway_to_next_resistance_pct"] = [12, 9, 6]
    store = ledger(tmp_path)
    captured = store.record_snapshot(
        frame,
        frame,
        "2026-09-01",
        v5_candidates=frame,
        v6_candidates=frame,
        v7_candidates=frame.head(1),
    ).set_index("ticker")
    assert captured["selected_v5"].sum() == 2
    assert captured["selected_v6"].sum() == 2
    assert captured["selected_v7"].sum() == 1
    assert captured.loc["A", "v6_confidence"] == "HIGH"
    assert captured.loc["A", "technical_score"] == 88
    assert captured.loc["A", "catalyst_score"] == 45
    assert captured.loc["A", "intraday_rvol"] == 2.0
    assert captured.loc["A", "runway_to_next_resistance_pct"] == 12


def test_ten_session_return_resolves_without_changing_five_session_labels(tmp_path):
    frame = candidates().head(1)
    store = ledger(tmp_path)
    store.record_snapshot(frame, frame, "2026-09-01")
    dates = pd.date_range("2026-09-02", periods=10, freq="B")
    prices = list(range(101, 111))
    history_10d = pd.DataFrame({"High": prices, "Low": prices, "Close": prices}, index=dates)
    resolved = store.resolve_histories({"A": history_10d}).iloc[0]
    assert resolved["daily_bars_resolved"] == 10
    assert resolved["return_10d_pct"] == 10.0
