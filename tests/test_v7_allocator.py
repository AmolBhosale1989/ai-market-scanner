import pandas as pd

from scanner.v7.allocator import AllocationSettings, allocate_paper_portfolio


def candidates():
    rows = []
    for i in range(10):
        rows.append({
            "ticker": f"T{i}",
            "sector": "TECH" if i < 5 else "HEALTH",
            "theme": "AI" if i < 4 else f"THEME{i}",
            "v6_robust_score": 95 - i,
            "v6_decision": "RANK",
            "v6_confidence": "HIGH",
            "v6_p5_lower": 45,
            "effective_rr": 3.0,
            "entry_trigger": 100 + i,
            "stop": 95 + i,
        })
    return pd.DataFrame(rows)


def test_v7_fails_closed_without_validated_v6():
    allocations, health = allocate_paper_portfolio(candidates(), {"promotion_status": "SHADOW_ONLY"})
    assert allocations.empty
    assert health["status"] == "HOLD_SHADOW"
    assert health["broker_execution_enabled"] is False


def test_v7_enforces_portfolio_and_cluster_risk_caps():
    settings = AllocationSettings(
        paper_equity=10_000,
        max_positions=4,
        risk_per_position_pct=0.5,
        max_total_risk_pct=1.5,
        max_position_notional_pct=20,
        max_sector_positions=2,
        max_theme_positions=1,
    )
    allocations, health = allocate_paper_portfolio(
        candidates(),
        {"promotion_status": "VALIDATED_SHADOW", "model_version": "v6-test"},
        settings,
    )
    assert not allocations.empty
    assert len(allocations) <= settings.max_positions
    assert allocations.groupby("sector").size().max() <= settings.max_sector_positions
    assert allocations.groupby("theme").size().max() <= settings.max_theme_positions
    assert health["planned_total_risk_pct"] <= settings.max_total_risk_pct
    assert health["paper_only"] is True
    assert health["broker_execution_enabled"] is False


def test_v7_rejects_abstentions_and_invalid_stops():
    frame = candidates().iloc[:2].copy()
    frame.loc[frame.index[0], "v6_decision"] = "ABSTAIN"
    frame.loc[frame.index[1], "stop"] = frame.loc[frame.index[1], "entry_trigger"]
    allocations, health = allocate_paper_portfolio(
        frame,
        {"promotion_status": "VALIDATED_SHADOW", "model_version": "v6-test"},
    )
    assert allocations.empty
    assert health["rejected"]["V6_ABSTAIN"] == 1
    assert health["rejected"]["INVALID_RISK_LEVELS"] == 1
