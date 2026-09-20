from datetime import datetime, timezone

import pandas as pd
import pytest

from scanner.warehouse import _assert_fresh, _assert_quality
from scanner.warehouse_gate import CoverageTier, build_tiers, evaluate_tier


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
