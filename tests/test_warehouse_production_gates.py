from datetime import datetime, timezone

import pandas as pd
import pytest

from scanner.warehouse import _assert_fresh, _assert_quality
from scanner.warehouse_gate import CoverageTier, build_tiers, coverage_frame, evaluate_tier


def _rows(now="2026-09-18T19:55:00Z"):
    return pd.DataFrame([
        {"ticker":"A","event_timestamp":now,"ingested_at":now,"open":10,"high":12,"low":9,"close":11,"volume":100},
        {"ticker":"B","event_timestamp":now,"ingested_at":now,"open":20,"high":22,"low":19,"close":21,"volume":200},
    ])


def test_quality_gate_rejects_bad_ohlc_symbol():
    frame=_rows()
    frame.loc[1,"high"]=18
    with pytest.raises(RuntimeError,match="WAREHOUSE_QUALITY_FAILED.*B"):
        _assert_quality(frame,"test")


def test_freshness_is_per_symbol(monkeypatch):
    frame=_rows()
    frame.loc[1,"event_timestamp"]="2026-09-17T19:55:00Z"
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-18T20:00:00Z")))
    with pytest.raises(RuntimeError,match="stale_symbols=1.*B"):
        _assert_fresh(frame,"5m",10,"test")


def test_tier_gate_requires_coverage_history_quality_and_freshness(monkeypatch):
    tier=CoverageTier("TEST",("A","B"),"5m",1.0,1,10)
    frame=_rows().assign(bars=2,invalid_bars=0)
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-18T20:00:00Z")))
    result=evaluate_tier(tier,frame)
    assert result["status"]=="PASS"
    assert result["coverage"]==1.0


def test_tier_gate_fails_missing_symbol_even_if_newest_symbol_is_fresh(monkeypatch):
    tier=CoverageTier("TEST",("A","B"),"5m",1.0,1,10)
    frame=_rows().iloc[:1].assign(bars=2,invalid_bars=0)
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-18T20:00:00Z")))
    result=evaluate_tier(tier,frame)
    assert result["status"]=="FAIL"
    assert result["missing_sample"]==["B"]


def test_tolerant_tier_quarantines_bad_symbol_when_coverage_still_passes(monkeypatch):
    tier=CoverageTier("MASTER",tuple("ABCDEFGHIJ"),"5m",0.90,1,10)
    frame=pd.DataFrame([
        {"ticker":symbol,"event_timestamp":"2026-09-18T19:55:00Z","ingested_at":"2026-09-18T19:55:00Z",
         "bars":2,"invalid_bars":1 if symbol=="J" else 0}
        for symbol in tier.symbols
    ])
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-18T20:00:00Z")))
    result=evaluate_tier(tier,frame)
    assert result["status"]=="PASS"
    assert result["usable_coverage"]==0.9
    assert result["invalid_sample"]==["J"]


def test_tolerant_tier_quarantines_stale_symbol_when_coverage_still_passes(monkeypatch):
    tier=CoverageTier("LIVE",tuple("ABCDEFGHIJ"),"5m",0.90,1,10)
    frame=pd.DataFrame([
        {"ticker":symbol,"event_timestamp":"2026-09-17T19:55:00Z" if symbol=="J" else "2026-09-18T19:55:00Z",
         "ingested_at":"2026-09-18T19:55:00Z","bars":2,"invalid_bars":0}
        for symbol in tier.symbols
    ])
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-18T20:00:00Z")))
    result=evaluate_tier(tier,frame)
    assert result["status"]=="PASS"
    assert result["usable_coverage"]==0.9
    assert result["stale_sample"]==["J"]


def test_critical_intraday_history_floor_accepts_thin_valid_etfs():
    master=[f"M{i}" for i in range(5_341)]
    critical=next(tier for tier in build_tiers(master,["AAPL"]) if tier.name=="CRITICAL_INTRADAY")
    assert critical.minimum_bars==120
    assert critical.minimum_coverage==1.0
    assert critical.max_age_minutes==10
    assert "SPY" in critical.symbols
    assert "FINX" not in critical.symbols


def test_theme_intraday_uses_sparse_etf_freshness_contract():
    master=[f"M{i}" for i in range(5_341)]
    theme=next(tier for tier in build_tiers(master,["AAPL"]) if tier.name=="THEME_INTRADAY")
    assert theme.minimum_bars==120
    assert theme.minimum_coverage==0.90
    assert theme.max_age_minutes==20
    assert "FINX" in theme.symbols
    assert "SPY" not in theme.symbols
    assert "XLE" not in theme.symbols


def test_sparse_theme_bar_does_not_weaken_strict_core_gate(monkeypatch):
    master=[f"M{i}" for i in range(5_341)]
    tiers={tier.name:tier for tier in build_tiers(master,["AAPL"])}
    now=pd.Timestamp("2026-09-21T15:09:00Z")
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: now))

    core=tiers["CRITICAL_INTRADAY"]
    core_frame=pd.DataFrame([
        {"ticker":symbol,"event_timestamp":"2026-09-21T15:05:00Z","ingested_at":"2026-09-21T15:08:00Z",
         "bars":410,"invalid_bars":0}
        for symbol in core.symbols
    ])
    assert evaluate_tier(core,core_frame)["status"]=="PASS"

    theme=tiers["THEME_INTRADAY"]
    theme_frame=pd.DataFrame([
        {"ticker":symbol,"event_timestamp":"2026-09-21T14:50:00Z" if symbol=="FINX" else "2026-09-21T15:05:00Z",
         "ingested_at":"2026-09-21T15:08:00Z","bars":151 if symbol=="FINX" else 410,"invalid_bars":0}
        for symbol in theme.symbols
    ])
    assert evaluate_tier(theme,theme_frame)["status"]=="PASS"

    core_frame.loc[core_frame["ticker"]=="SPY","event_timestamp"]="2026-09-21T14:50:00Z"
    core_result=evaluate_tier(core,core_frame)
    assert core_result["status"]=="FAIL"
    assert core_result["stale_sample"]==["SPY"]


def test_theme_tier_fails_when_sparse_symbols_exceed_tolerance(monkeypatch):
    master=[f"M{i}" for i in range(5_341)]
    theme=next(tier for tier in build_tiers(master,["AAPL"]) if tier.name=="THEME_INTRADAY")
    monkeypatch.setattr(
        pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-21T15:30:00Z"))
    )
    stale=set(theme.symbols[:2])
    frame=pd.DataFrame([
        {"ticker":symbol,"event_timestamp":"2026-09-21T14:50:00Z" if symbol in stale else "2026-09-21T15:25:00Z",
         "ingested_at":"2026-09-21T15:29:00Z","bars":410,"invalid_bars":0}
        for symbol in theme.symbols
    ])
    result=evaluate_tier(theme,frame)
    assert result["status"]=="FAIL"
    assert result["stale_symbol_count"]==2


def test_live_tier_allows_one_provider_interval_of_delivery_lag(monkeypatch):
    master=[f"M{i}" for i in range(5_341)]
    live=[f"L{i}" for i in range(420)]
    tier=next(tier for tier in build_tiers(master,live) if tier.name=="LIVE_INTRADAY")
    assert tier.minimum_coverage==0.95
    assert tier.max_age_minutes==15
    monkeypatch.setattr(
        pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-21T16:48:30Z"))
    )
    frame=pd.DataFrame([
        {"ticker":symbol,"event_timestamp":"2026-09-21T16:35:00Z",
         "ingested_at":"2026-09-21T16:47:00Z","bars":264,"invalid_bars":0}
        for symbol in live
    ])
    result=evaluate_tier(tier,frame)
    assert result["status"]=="PASS"
    assert result["usable_coverage"]==1.0


def test_coverage_query_bounds_version_selection_per_instrument(monkeypatch):
    import scanner.warehouse_gate as module
    captured={}
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): return False
    monkeypatch.setattr(module,"_connect",lambda:Connection())
    def read(sql,conn,params):
        captured["sql"]=sql
        captured["params"]=params
        return pd.DataFrame()
    monkeypatch.setattr(pd,"read_sql_query",read)
    tier=CoverageTier("MASTER",("A","B"),"1d",0.75,40,20)
    coverage_frame(tier,datetime(2026,9,18,tzinfo=timezone.utc))
    assert "CROSS JOIN LATERAL" in captured["sql"]
    assert "row_number()" not in captured["sql"]
    assert captured["params"][0]==["A","B"]
