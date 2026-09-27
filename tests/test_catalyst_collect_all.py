from datetime import datetime,timezone
from unittest.mock import Mock
import pandas as pd
import pytest

from scanner import catalyst_pipeline as cp


def test_batch_polling_keeps_unresolved_sec_fail_closed_and_reaches_alpha(monkeypatch):
    anchor=datetime(2026,9,26,16,0,tzinfo=timezone.utc)
    monkeypatch.setattr(cp,"read_dataset",lambda name: pd.DataFrame({"ticker":["AAA","BBB"]}))
    monkeypatch.setattr(cp,"consumer_anchor",lambda: anchor)
    yahoo=Mock(provider_name="YAHOO_NEWS")
    sec=Mock(provider_name="SEC_EDGAR")
    yahoo.fetch_batch.return_value=(Mock(events=(),rejected_count=0),{"errors":0})
    sec.fetch_batch.return_value=(Mock(events=(),rejected_count=0),
                                  {"errors":0,"unresolved":1,"unresolved_tickers":["AAA"]})
    monkeypatch.setattr(cp,"ExistingYahooNewsAdapter",lambda **kwargs: yahoo)
    monkeypatch.setattr(cp,"ExistingSecEdgarAdapter",lambda **kwargs: sec)
    calls=[]
    monkeypatch.setattr(cp,"persist_batch_result",
        lambda adapter,tickers,result,anchor: calls.append((adapter.provider_name,tuple(tickers))))
    alpha=Mock()
    monkeypatch.setattr(cp,"AlphaVantageCalendarBatch",lambda fn: alpha)
    monkeypatch.setattr(cp,"ingest_alpha_vantage_batch",
        lambda *a,**k: calls.append(("ALPHA_VANTAGE","BATCH")))
    monkeypatch.setattr(cp,"verify_coverage",
        lambda *a,**k: (_ for _ in ()).throw(RuntimeError("CATALYST_COVERAGE_INCOMPLETE missing=1")))

    with pytest.raises(RuntimeError,match="CATALYST_COVERAGE_INCOMPLETE"):
        cp.run()

    assert ("YAHOO_NEWS",("AAA","BBB")) in calls
    assert ("SEC_EDGAR",("BBB",)) in calls
    assert ("ALPHA_VANTAGE","BATCH") in calls
    yahoo.fetch_batch.assert_called_once()
    sec.fetch_batch.assert_called_once()


def test_sec_failure_preserves_health_payload():
    from scanner.catalyst_existing_adapters import ExistingSecEdgarAdapter
    delegate=Mock()
    delegate.poll.return_value=([],{"unresolved":1,"errors":0,"ticker_map_source":"SEC_OFFICIAL","resolved":0})
    adapter=ExistingSecEdgarAdapter(delegate=delegate)
    with pytest.raises(RuntimeError,match=r"ticker=AAA.*unresolved.*SEC_OFFICIAL"):
        adapter.fetch_catalysts("AAA",anchor=datetime(2026,9,26,16,0,tzinfo=timezone.utc))
