from datetime import datetime,timedelta,timezone
import pytest

from scanner.catalyst_pipeline import PROVIDER_MAX_AGE,REQUIRED_PROVIDERS


def test_required_provider_policy_is_complete():
    assert set(PROVIDER_MAX_AGE)==set(REQUIRED_PROVIDERS)
    assert PROVIDER_MAX_AGE["YAHOO_NEWS"]==timedelta(minutes=15)
    assert PROVIDER_MAX_AGE["SEC_EDGAR"]==timedelta(minutes=15)
    assert PROVIDER_MAX_AGE["ALPHA_VANTAGE"]==timedelta(hours=24)


def test_production_workflow_orders_catalysts_before_consumers():
    from pathlib import Path
    text=Path(".github/workflows/production.yml").read_text()
    assert "stage warehouse_gate 70" in text
    assert "stage catalyst_ingestion 72 python -m scanner.catalyst_pipeline" in text
    assert text.index("stage warehouse_gate 70") < text.index("stage catalyst_ingestion 72")
    assert text.index("stage catalyst_ingestion 72") < text.index("stage v3_live 80")


def test_acceptance_calls_catalyst_coverage():
    from pathlib import Path
    text=Path("scanner/production_acceptance.py").read_text()
    assert "verify_coverage(live_tickers,anchor=anchor.to_pydatetime())" in text
