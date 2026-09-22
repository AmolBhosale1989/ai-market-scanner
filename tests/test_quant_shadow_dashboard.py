from pathlib import Path


APP = Path("app.py").read_text()


def test_quant_shadow_tab_is_exposed():
    assert '"Quant Shadow"' in APP
    assert "with quant_tab:" in APP
    assert "Quant Strategy Shadow Lab" in APP


def test_quant_shadow_artifacts_are_loaded():
    for artifact in (
        "quant_shadow_signals",
        "quant_shadow_ledger",
        "quant_shadow_performance",
        "quant_shadow_health",
    ):
        assert artifact in APP


def test_dashboard_states_postgres_authority_and_shadow_boundaries():
    assert "PostgreSQL is the sole authoritative OHLCV source" in APP
    assert "V3 remains production-primary" in APP
    assert "broker execution is disabled" in APP
    assert "promotion is never automatic" in APP
