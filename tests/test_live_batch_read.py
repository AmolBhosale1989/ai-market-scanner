import pandas as pd
import pytest

from scanner import live


def _candidates():
    return pd.DataFrame([
        {"ticker":"AAA","stage":"ARMED","final_score":90.0},
        {"ticker":"BBB","stage":"FORMING","final_score":80.0},
    ])


def test_live_enrichment_reads_selected_histories_once(monkeypatch):
    histories={"AAA":pd.DataFrame({"marker":[1]}),"BBB":pd.DataFrame({"marker":[2]})}
    batch_calls=[]
    analyzed=[]

    def batch(tickers,**kwargs):
        batch_calls.append((tickers,kwargs))
        return histories

    def analyze(**kwargs):
        analyzed.append((kwargs["ticker"],kwargs["history_frame"]))
        return {"live_status":"MARKET CLOSED"}

    monkeypatch.setattr(live,"warehouse_frames",batch)
    monkeypatch.setattr(live,"analyze_live_candidate",analyze)

    result=live.enrich_live_candidates(_candidates(),limit=2)

    assert batch_calls==[(["AAA","BBB"],{
        "period":live.LIVE_PERIOD,
        "interval":live.LIVE_INTERVAL,
        "max_age_minutes":10,
        "require_complete":True,
    })]
    assert [(ticker,frame is histories[ticker]) for ticker,frame in analyzed]==[("AAA",True),("BBB",True)]
    assert result["live_status"].tolist()==["MARKET CLOSED","MARKET CLOSED"]


@pytest.mark.parametrize("failure",[
    "WAREHOUSE_COVERAGE_INCOMPLETE",
    "WAREHOUSE_QUALITY_FAILED",
    "WAREHOUSE_STALE",
])
def test_live_batch_read_fails_closed_before_analysis(monkeypatch,failure):
    def fail(*args,**kwargs):
        raise RuntimeError(failure)

    monkeypatch.setattr(live,"warehouse_frames",fail)
    monkeypatch.setattr(live,"analyze_live_candidate",lambda **kwargs:pytest.fail("analysis ran after rejected batch"))

    with pytest.raises(RuntimeError,match=failure):
        live.enrich_live_candidates(_candidates(),limit=2)


def test_analyze_live_candidate_uses_supplied_history(monkeypatch):
    supplied=pd.DataFrame()
    monkeypatch.setattr(live,"warehouse_history",lambda *args,**kwargs:pytest.fail("per-symbol warehouse read used"))

    result=live.analyze_live_candidate(
        ticker="AAA",
        entry_trigger=10.0,
        stage="ARMED",
        catalyst_score=0.0,
        rr_to_8pct=2.0,
        runway_pct=10.0,
        negative_catalyst_risk=False,
        history_frame=supplied,
    )

    assert result["live_status"]=="NO INTRADAY DATA"
