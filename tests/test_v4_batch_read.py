import pandas as pd
import pytest
from scanner.v4 import adapters
from scanner import warehouse


def test_v4_reads_once_and_preserves_symbol_analysis(monkeypatch):
    histories={t:pd.DataFrame({"marker":[v]}) for t,v in [("AAA",12.0),("BBB",23.0)]}
    calls=[]
    def frames(tickers,**kwargs):
        calls.append((tickers,kwargs))
        return histories
    def analyze(**kwargs):
        assert kwargs["history_frame"] is histories[kwargs["ticker"]]
        return {"live_price":kwargs["history_frame"].marker.iloc[0],"live_status":"MARKET CLOSED"}
    monkeypatch.setattr(adapters,"warehouse_frames",frames)
    monkeypatch.setattr(adapters,"analyze_live_candidate",analyze)
    result=adapters.YahooPollingAdapter().poll(pd.DataFrame({"ticker":["AAA","BBB"]}))
    assert len(calls)==1
    assert calls[0][1]["max_age_minutes"]==10
    assert result.frame.live_price.tolist()==[12.0,23.0]
    assert result.received==2 and result.errors==0


@pytest.mark.parametrize("bad",["missing","stale","invalid"])
def test_v4_quarantined_symbol_cannot_generate_signal(monkeypatch,bad):
    now=pd.Timestamp("2026-09-25T02:00:00Z")
    rows=[dict(ticker="AAA",event_timestamp=pd.Timestamp("2026-09-24T19:55:00Z"),ingested_at=now,
               open=10.,high=11.,low=9.,close=10.5,volume=100.)]
    if bad!="missing":
        row={**rows[0],"ticker":"BBB"}
        if bad=="stale":row["event_timestamp"]=pd.Timestamp("2026-09-24T14:00:00Z")
        else:row["close"]=float("nan")
        rows.append(row)
    monkeypatch.setattr(warehouse,"_pit",lambda *a,**k:pd.DataFrame(rows))
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None:now))
    def analyze(**kwargs):
        assert kwargs["ticker"]=="AAA"
        return {"live_price":10.5,"live_status":"MARKET CLOSED"}
    monkeypatch.setattr(adapters,"analyze_live_candidate",analyze)
    result=adapters.YahooPollingAdapter().poll(pd.DataFrame({"ticker":["AAA","BBB"]}))
    assert result.received==1 and result.errors==1
    assert result.frame.iloc[1].live_status=="ERROR"
    assert pd.isna(result.frame.iloc[1].live_price)


def test_v4_database_failure_stops_batch(monkeypatch):
    def fail(*a,**k):raise RuntimeError("database unavailable")
    monkeypatch.setattr(adapters,"warehouse_frames",fail)
    monkeypatch.setattr(adapters,"analyze_live_candidate",lambda **k:pytest.fail("analysis after failed read"))
    with pytest.raises(RuntimeError,match="database unavailable"):
        adapters.YahooPollingAdapter().poll(pd.DataFrame({"ticker":["AAA"]}))
