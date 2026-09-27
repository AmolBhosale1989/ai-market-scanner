from datetime import datetime,timezone
from unittest.mock import Mock
import pandas as pd
import pytest

from scanner import catalyst_data_plane as cp


def test_data_plane_collects_independently_of_control_anchor(monkeypatch):
    yahoo=Mock(provider_name="YAHOO_NEWS")
    sec=Mock(provider_name="SEC_EDGAR")
    yahoo.fetch_batch.return_value=(Mock(events=(),rejected_count=0),{"errors":0})
    sec.fetch_batch.return_value=(Mock(events=(),rejected_count=0),
                                  {"errors":0,"unresolved":1,"unresolved_tickers":["AAA"]})
    monkeypatch.setattr(cp,"_universe",lambda: ["AAA","BBB"])
    monkeypatch.setattr(cp,"ExistingYahooNewsAdapter",lambda **kwargs: yahoo)
    monkeypatch.setattr(cp,"ExistingSecEdgarAdapter",lambda **kwargs: sec)
    calls=[]
    monkeypatch.setattr(cp,"persist_batch_result",
        lambda adapter,tickers,result,anchor: calls.append((adapter.provider_name,tuple(tickers),anchor)))
    monkeypatch.setattr(cp,"AlphaVantageCalendarBatch",lambda fn: Mock(provider_name="ALPHA_VANTAGE"))
    monkeypatch.setattr(cp,"ingest_alpha_vantage_batch",
        lambda *a,**k: calls.append(("ALPHA_VANTAGE","BATCH",k["anchor"])))

    result=cp.run()

    assert result["failures"]==1  # unresolved SEC evidence remains explicit
    assert calls[0][0:2]==("YAHOO_NEWS",("AAA","BBB"))
    assert calls[1][0:2]==("SEC_EDGAR",("BBB",))
    assert calls[2][0:2]==("ALPHA_VANTAGE","BATCH")
    knowledge_times={item[2] for item in calls}
    assert len(knowledge_times)==1
    assert next(iter(knowledge_times)).tzinfo is not None


def test_sec_failure_preserves_health_payload():
    from scanner.catalyst_existing_adapters import ExistingSecEdgarAdapter
    delegate=Mock()
    delegate.poll.return_value=([],{"unresolved":1,"errors":0,"ticker_map_source":"SEC_OFFICIAL","resolved":0})
    adapter=ExistingSecEdgarAdapter(delegate=delegate)
    with pytest.raises(RuntimeError,match=r"ticker=AAA.*unresolved.*SEC_OFFICIAL"):
        adapter.fetch_catalysts("AAA",anchor=datetime(2026,9,26,16,0,tzinfo=timezone.utc))
