from datetime import datetime,timezone
from unittest.mock import Mock
import pandas as pd
import pytest

from scanner import catalyst_data_plane as cp


def test_data_plane_collects_independently_of_control_anchor(monkeypatch):
    yahoo=Mock(provider_name="YAHOO_NEWS")
    sec=Mock(provider_name="SEC_EDGAR")
    yahoo.fetch_batch.return_value=(Mock(events=(),rejected_count=0),
        {"errors":1,"successful_tickers":["AAA"],"failed_tickers":["BBB"]})
    sec.fetch_batch.return_value=(Mock(events=(),rejected_count=0),
        {"errors":0,"unresolved":1,"unresolved_tickers":["AAA"],
         "successful_tickers":["BBB"],"failed_tickers":[]})
    monkeypatch.setattr(cp,"_universe",lambda: ["AAA","BBB"])
    monkeypatch.setattr(cp,"ExistingYahooNewsAdapter",lambda **kwargs: yahoo)
    monkeypatch.setattr(cp,"ExistingSecEdgarAdapter",lambda **kwargs: sec)
    calls=[]
    monkeypatch.setattr(cp,"persist_batch_result",
        lambda adapter,tickers,result,anchor,checked_at_by_ticker=None:
            calls.append((adapter.provider_name,tuple(tickers),anchor)))
    monkeypatch.setattr(cp,"AlphaVantageCalendarBatch",lambda fn: Mock(provider_name="ALPHA_VANTAGE"))
    monkeypatch.setattr(cp,"ingest_alpha_vantage_batch",
        lambda *a,**k: calls.append(("ALPHA_VANTAGE","BATCH",k["anchor"])))

    result=cp.run()

    assert result["failures"]==2  # Yahoo failure + unresolved SEC remain explicit
    assert calls[0][0:2]==("YAHOO_NEWS",("AAA",))
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


def test_partial_batch_never_turns_failed_ticker_into_no_event(monkeypatch):
    yahoo=Mock(provider_name="YAHOO_NEWS")
    yahoo.fetch_batch.return_value=(Mock(events=(),rejected_count=0),
        {"errors":1,"successful_tickers":["AAA"],"failed_tickers":["BBB"]})
    sec=Mock(provider_name="SEC_EDGAR")
    sec.fetch_batch.return_value=(Mock(events=(),rejected_count=0),
        {"errors":2,"successful_tickers":[],"failed_tickers":["AAA","BBB"],
         "unresolved":0,"unresolved_tickers":[]})
    monkeypatch.setattr(cp,"_universe",lambda:["AAA","BBB"])
    monkeypatch.setattr(cp,"ExistingYahooNewsAdapter",lambda **kwargs:yahoo)
    monkeypatch.setattr(cp,"ExistingSecEdgarAdapter",lambda **kwargs:sec)
    persisted=[]
    monkeypatch.setattr(cp,"persist_batch_result",
        lambda adapter,tickers,result,anchor,checked_at_by_ticker=None:
            persisted.append((adapter.provider_name,tuple(tickers))))
    monkeypatch.setattr(cp,"AlphaVantageCalendarBatch",lambda fn:Mock(provider_name="ALPHA_VANTAGE"))
    monkeypatch.setattr(cp,"ingest_alpha_vantage_batch",lambda *a,**k:None)
    cp.run()
    assert ("YAHOO_NEWS",("AAA",)) in persisted
    assert ("YAHOO_NEWS",("BBB",)) not in persisted
    assert ("SEC_EDGAR",()) in persisted


def test_data_plane_passes_per_ticker_knowledge_times(monkeypatch):
    t1=datetime(2026,9,27,14,1,tzinfo=timezone.utc)
    t2=datetime(2026,9,27,14,2,tzinfo=timezone.utc)
    yahoo=Mock(provider_name="YAHOO_NEWS")
    yahoo.fetch_batch.return_value=(Mock(events=(),rejected_count=0),{
        "errors":0,"successful_tickers":["AAA","BBB"],"failed_tickers":[],
        "successful_checks":[{"ticker":"AAA","checked_at":t1},{"ticker":"BBB","checked_at":t2}],
    })
    monkeypatch.setattr(cp,"_universe",lambda:["AAA","BBB"])
    monkeypatch.setattr(cp,"ExistingYahooNewsAdapter",lambda **kwargs:yahoo)
    captured={}
    monkeypatch.setattr(cp,"persist_batch_result",
        lambda adapter,tickers,result,anchor,checked_at_by_ticker=None:
            captured.update(checked_at_by_ticker or {}))
    cp.run("yahoo")
    assert captured=={"AAA":t1,"BBB":t2}
