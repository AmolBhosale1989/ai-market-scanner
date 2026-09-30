from datetime import datetime,timedelta,timezone
import pytest

from scanner.catalyst_pipeline import PROVIDER_MAX_AGE,REQUIRED_PROVIDERS,OPTIONAL_PROVIDERS


def test_required_provider_policy_is_complete():
    assert set(PROVIDER_MAX_AGE)==set(REQUIRED_PROVIDERS+OPTIONAL_PROVIDERS)
    assert PROVIDER_MAX_AGE["YAHOO_NEWS"]==timedelta(minutes=15)
    assert PROVIDER_MAX_AGE["SEC_EDGAR"]==timedelta(minutes=15)
    assert PROVIDER_MAX_AGE["ALPHA_VANTAGE"]==timedelta(hours=24)


def test_production_workflow_orders_catalysts_before_consumers():
    from pathlib import Path
    text=Path(".github/workflows/production.yml").read_text()
    assert text.index("stage intraday_warehouse 60") < text.index("stage catalyst_ingest 65")
    assert text.index("stage catalyst_ingest 65") < text.index("stage warehouse_gate 70")
    assert "python -m scanner.catalyst_pipeline --ingest-only --mandatory-only" in text
    assert "stage catalyst_gate 72 python -m scanner.catalyst_pipeline --verify-only" in text
    assert text.index("stage warehouse_gate 70") < text.index("stage catalyst_gate 72")
    assert text.index("stage catalyst_gate 72") < text.index("stage v3_live 80")


def test_acceptance_calls_catalyst_coverage():
    from pathlib import Path
    text=Path("scanner/production_acceptance.py").read_text()
    assert "verify_coverage(live_tickers,anchor=anchor.to_pydatetime(),pg_snapshot=snapshots[0].get(" in text


def test_production_workflow_exposes_alpha_vantage_secret():
    from pathlib import Path
    text=Path(".github/workflows/production.yml").read_text()
    assert 'ALPHA_VANTAGE_API_KEY: ${{ secrets.ALPHA_VANTAGE_API_KEY }}' in text


def test_stage72_is_database_only():
    from pathlib import Path
    text=Path("scanner/catalyst_pipeline.py").read_text()
    assert "verify_coverage(tickers,anchor=anchor)" in text
    assert "ExistingYahooNewsAdapter" not in text
    assert "ExistingSecEdgarAdapter" not in text
    assert "AlphaVantageCalendarBatch" not in text
    assert "requests" not in text


def test_catalyst_data_plane_is_independently_scheduled():
    from pathlib import Path
    text=Path(".github/workflows/catalyst_ingestion.yml").read_text()
    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "python -m scanner.catalyst_data_plane" in text
    assert "ALPHA_VANTAGE_API_KEY:" in text


def test_control_plane_allows_optional_stale_or_missing_evidence(monkeypatch):
    from scanner import catalyst_pipeline as gate
    t0=datetime(2026,9,26,16,0,tzinfo=timezone.utc)
    tminus1=t0-timedelta(minutes=1)
    tminus25h=t0-timedelta(hours=25)
    class Cursor:
        def __init__(self,rows): self.rows=rows
        def execute(self,*args,**kwargs): pass
        def fetchall(self): return self.rows
        def __enter__(self): return self
        def __exit__(self,*args): pass
    class Conn:
        def __init__(self,rows): self.rows=rows
        def cursor(self): return Cursor(self.rows)
        def __enter__(self): return self
        def __exit__(self,*args): pass
    providers=list(gate.REQUIRED_PROVIDERS+gate.OPTIONAL_PROVIDERS)
    monkeypatch.setattr("scanner.consumer_snapshot.resolve_pg_snapshot",lambda x: "10:10:")
    fresh=[("AAA",provider,tminus1) for provider in providers]
    monkeypatch.setattr("scanner.database.connection",lambda: Conn(fresh))
    assert gate.verify_coverage(["AAA"],anchor=t0) is True

    stale=[("AAA",provider,tminus25h) for provider in providers]
    monkeypatch.setattr("scanner.database.connection",lambda: Conn(stale))
    with pytest.raises(RuntimeError,match="CATALYST_COVERAGE_INCOMPLETE"):
        gate.verify_coverage(["AAA"],anchor=t0)

    missing=[("AAA",provider,tminus1) for provider in providers if provider!="ALPHA_VANTAGE"]
    monkeypatch.setattr("scanner.database.connection",lambda: Conn(missing))
    with pytest.raises(RuntimeError,match="CATALYST_COVERAGE_INCOMPLETE"):
        gate.verify_coverage(["AAA"],anchor=t0)


def test_data_plane_reads_last_published_production_universe(monkeypatch):
    from scanner import catalyst_data_plane as data_plane
    calls=[]
    def fake_read(name,**kwargs):
        calls.append((name,kwargs))
        import pandas as pd
        return pd.DataFrame({"ticker":["AAA","BBB"]})
    monkeypatch.setattr(data_plane,"read_dataset",fake_read)
    assert data_plane._universe()==["AAA","BBB"]
    assert calls==[("live_universe",{"published_mode":"production","required":False})]


def test_catalyst_workflow_isolates_provider_jobs():
    from pathlib import Path
    text=Path(".github/workflows/catalyst_ingestion.yml").read_text()
    assert "fail-fast: false" in text
    assert "provider: [alpha_vantage, yahoo, sec]" in text
    assert "python -m scanner.catalyst_data_plane --provider ${{ matrix.provider }}" in text
    assert "timeout-minutes: 30" in text
    assert "timeout: 5" not in text
    assert "timeout: 10" not in text
