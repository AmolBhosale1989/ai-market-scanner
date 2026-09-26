from datetime import datetime,timezone
from unittest.mock import Mock
import pandas as pd
import pytest

from scanner import catalyst_pipeline as cp


def test_collect_all_attempts_later_providers_then_fails_coverage(monkeypatch):
    anchor=datetime(2026,9,26,16,0,tzinfo=timezone.utc)
    monkeypatch.setattr(cp,"read_dataset",lambda name: pd.DataFrame({"ticker":["AAA","BBB"]}))
    monkeypatch.setattr(cp,"consumer_anchor",lambda: anchor)
    yahoo=Mock(provider_name="YAHOO_NEWS")
    sec=Mock(provider_name="SEC_EDGAR")
    monkeypatch.setattr(cp,"ExistingYahooNewsAdapter",lambda: yahoo)
    monkeypatch.setattr(cp,"ExistingSecEdgarAdapter",lambda: sec)
    calls=[]
    def ingest(adapter,ticker,*,anchor):
        calls.append((adapter.provider_name,ticker))
        if adapter.provider_name=="SEC_EDGAR" and ticker=="AAA":
            raise RuntimeError("SEC_EDGAR_PROVIDER_FAILED ticker=AAA health={'unresolved': 1, 'errors': 0}")
    monkeypatch.setattr(cp,"ingest_ticker",ingest)
    alpha=Mock()
    monkeypatch.setattr(cp,"AlphaVantageCalendarBatch",lambda fn: alpha)
    monkeypatch.setattr(cp,"ingest_alpha_vantage_batch",lambda *a,**k: calls.append(("ALPHA_VANTAGE","BATCH")))
    monkeypatch.setattr(cp,"verify_coverage",lambda *a,**k: (_ for _ in ()).throw(RuntimeError("CATALYST_COVERAGE_INCOMPLETE missing=1")))

    with pytest.raises(RuntimeError,match="CATALYST_COVERAGE_INCOMPLETE"):
        cp.run()

    assert calls==[
        ("YAHOO_NEWS","AAA"),("SEC_EDGAR","AAA"),
        ("YAHOO_NEWS","BBB"),("SEC_EDGAR","BBB"),
        ("ALPHA_VANTAGE","BATCH"),
    ]


def test_sec_failure_preserves_health_payload():
    from scanner.catalyst_existing_adapters import ExistingSecEdgarAdapter
    delegate=Mock()
    delegate.poll.return_value=([],{"unresolved":1,"errors":0,"ticker_map_source":"SEC_OFFICIAL","resolved":0})
    adapter=ExistingSecEdgarAdapter(delegate=delegate)
    with pytest.raises(RuntimeError,match=r"ticker=AAA.*unresolved.*SEC_OFFICIAL"):
        adapter.fetch_catalysts("AAA",anchor=datetime(2026,9,26,16,0,tzinfo=timezone.utc))
