import numpy as np
import pandas as pd
import pytest
from scanner.daily_repair import repair_daily


def inputs():
    bars=pd.DataFrame({"Open":10.,"High":12.,"Low":9.,"Close":11.,"Volume":100.},
        index=pd.date_range("2026-09-22T13:30Z",periods=78,freq="5min"))
    daily=pd.DataFrame({"Open":[8.,np.nan],"High":[10.,np.nan],"Low":[7.,np.nan],
                        "Close":[9.,np.nan],"Volume":[500.,100.]},
                       index=pd.to_datetime(["2026-09-21","2026-09-22"]))
    return daily,bars


def test_complete_session_reconstruction_preserves_history_and_provenance():
    daily,bars=inputs()
    result=repair_daily(daily,bars,now="2026-09-23T06:00Z")
    assert result.iloc[-1].Close==11.
    assert result.iloc[-1].Volume==7800.
    assert result.iloc[-1].source_bar_count==78
    assert result.iloc[-1].daily_bar_source=="COMPLETE_REGULAR_SESSION_5M"
    pd.testing.assert_series_equal(result.iloc[0][daily.columns],daily.iloc[0],check_dtype=False)


@pytest.mark.parametrize("failure",["missing","duplicate","invalid","stale","open_session"])
def test_bad_or_incomplete_intraday_cannot_repair_daily(failure):
    daily,bars=inputs()
    now="2026-09-23T06:00Z"
    if failure=="missing": bars=bars.drop(bars.index[10])
    if failure=="duplicate": bars=pd.concat([bars,bars.iloc[-1:]])
    if failure=="invalid": bars.iloc[-1,bars.columns.get_loc("Close")]=np.inf
    if failure=="stale": bars.index=bars.index-pd.Timedelta(days=1)
    if failure=="open_session": now="2026-09-22T19:58Z"
    result=repair_daily(daily,bars,now=now)
    assert pd.isna(result.iloc[-1].Close)


def test_good_daily_prices_are_not_replaced():
    daily,bars=inputs(); daily.iloc[-1]=[10.,12.,9.,10.5,9000.]
    pd.testing.assert_frame_equal(repair_daily(daily,bars,now="2026-09-23T06:00Z"),daily)


@pytest.mark.parametrize("interval,count",[("30m",13),("60m",7)])
def test_complete_coarser_session_and_missing_closing_bar(interval,count):
    daily,bars=inputs()
    bars=bars.resample(interval.replace("m","min"),origin=bars.index[0]).agg({
        "Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"})
    result=repair_daily(daily,bars,now="2026-09-23T06:00Z",interval=interval)
    assert result.iloc[-1].source_bar_count==count
    assert result.iloc[-1].source_timeframe==interval
    assert result.iloc[-1].Volume==7800
    assert pd.isna(repair_daily(daily,bars.iloc[:-1],now="2026-09-23T06:00Z",interval=interval).iloc[-1].Close)


def test_refresh_persists_repair_evidence_before_daily(monkeypatch):
    from scanner import warehouse_refresh as refresh
    daily,bars=inputs()
    monkeypatch.setattr(pd.Timestamp,"now",classmethod(lambda cls,tz=None: pd.Timestamp("2026-09-23T06:00Z")))
    monkeypatch.setattr(refresh,"INGESTION_CRITICAL_SYMBOLS",())
    monkeypatch.setattr(refresh,"verify_health",lambda:None)
    monkeypatch.setattr(refresh,"start_run",lambda **kw:"run")
    monkeypatch.setattr(refresh,"finish_run",lambda *a,**kw:None)
    monkeypatch.setattr(refresh,"latest_event_timestamps",lambda *a,**kw:{})
    monkeypatch.setattr(refresh,"download_batch",lambda tickers,period,interval:{"SPY":daily if interval=="1d" else bars})
    writes=[]
    def ingest(frame,**kw):
        writes.append((kw["timeframe"],frame.copy()))
        return len(frame)
    monkeypatch.setattr(refresh,"ingest_observations",ingest)
    refresh.refresh(["SPY"],period="1y",interval="1d",benchmark_backfill=False)
    assert [x[0] for x in writes]==["5m","1d"]
    assert len(writes[0][1])==78
    assert writes[1][1].iloc[-1].Close==11.
    assert writes[1][1].iloc[-1].source_timeframe=="5m"
