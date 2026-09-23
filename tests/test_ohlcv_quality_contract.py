import os
from datetime import datetime, timezone

import pandas as pd
import pytest

from scanner.ohlcv_quality import invalid_rows, invalid_sql
from scanner.warehouse import _assert_quality
from scanner.warehouse_refresh import _normalize
from scanner.bitemporal_warehouse import ingest_observations, _connect


def row():
    return dict(ticker="SPY",event_timestamp="2026-09-22T00:00:00Z",
                open=10.,high=12.,low=9.,close=11.,volume=100.)


@pytest.mark.parametrize("field",["open","high","low","close","volume"])
@pytest.mark.parametrize("value",[float("nan"),float("inf"),float("-inf"),None])
def test_nonfinite_values_rejected_at_reader_and_ingestion(field,value):
    item=row(); item[field]=value
    frame=pd.DataFrame([item])
    with pytest.raises(RuntimeError,match="invalid_rows=1"):
        _assert_quality(frame,"test")
    provider=frame.rename(columns={c:c.title() for c in ("open","high","low","close","volume")})
    provider["ingested_at"]=pd.Timestamp.now(tz="UTC")
    with pytest.raises(RuntimeError,match="INGEST_QUALITY_FAILED"):
        ingest_observations(provider,"unused","test","OHLCV","1d")


def test_provider_missing_price_row_does_not_enter_warehouse():
    frame=pd.DataFrame({"Open":[10.,float("nan")],"High":[12.,float("nan")],
                        "Low":[9.,float("nan")],"Close":[11.,float("nan")],"Volume":[100.,100.]},
                       index=pd.to_datetime(["2026-09-21","2026-09-22"]))
    result=_normalize("SPY",frame,datetime.now(timezone.utc))
    assert len(result)==1
    assert result.iloc[0].event_timestamp==pd.Timestamp("2026-09-21",tz="UTC")


def test_diagnostic_counts_bad_rows_not_entire_symbol_history():
    rows=[dict(row(),event_timestamp=str(i)) for i in range(253)]
    rows[-1]["open"]=float("nan")
    with pytest.raises(RuntimeError,match="invalid_rows=1 sample=SPY"):
        _assert_quality(pd.DataFrame(rows),"test")


@pytest.mark.postgres_integration
def test_postgres_and_pandas_apply_same_quality_rules():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    examples=[row()]
    for field in ("open","high","low","close","volume"):
        for value in (None,float("nan"),float("inf"),float("-inf"),-1.):
            examples.append(dict(row(),**{field:value}))
    examples.extend([dict(row(),high=10.),dict(row(),low=11.),dict(row(),volume=0.)])
    with _connect() as conn, conn.cursor() as cur:
        for item in examples:
            cur.execute(f"SELECT {invalid_sql('v')} FROM (SELECT %s::double precision AS open,"
                        "%s::double precision AS high,%s::double precision AS low,"
                        "%s::double precision AS close,%s::double precision AS volume) v",
                        tuple(item[c] for c in ("open","high","low","close","volume")))
            assert cur.fetchone()[0]==bool(invalid_rows(pd.DataFrame([item])).iloc[0])


@pytest.mark.postgres_integration
def test_invalid_latest_version_is_refetched_and_corrected_without_losing_history():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL required")
    import uuid
    from scanner.bitemporal_warehouse import (
        start_run, latest_event_timestamps, point_in_time, PointInTimeRequirement,
    )
    from scanner.warehouse_gate import coverage_frame, CoverageTier
    symbol="QUALITY_"+uuid.uuid4().hex.upper()
    rid=start_run("test","quality_contract",{})
    original=pd.Timestamp("2026-01-01",tz="UTC")
    broken=original+pd.Timedelta(days=1)
    corrected=original+pd.Timedelta(days=2)
    frame=pd.DataFrame([dict(row(),ticker=symbol,event_timestamp=original,
                             ingested_at=original)]).rename(columns={
        c:c.title() for c in ("open","high","low","close","volume")})
    assert ingest_observations(frame,rid,"test","OHLCV","1d")==1
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO market_observation
          (instrument_id,data_type,timeframe,event_timestamp,ingested_at,warehouse_run_id,
           provider,open,high,low,close,volume,payload)
          SELECT instrument_id,data_type,timeframe,event_timestamp,%s,warehouse_run_id,
           provider,'NaN'::double precision,high,low,close,volume,payload
          FROM market_observation WHERE warehouse_run_id=%s""",(broken,rid))
    assert symbol not in latest_event_timestamps([symbol])
    tier=CoverageTier("TEST",(symbol,),"1d",1.,1,20)
    assert coverage_frame(tier,broken).iloc[0].invalid_bars==1
    frame["ingested_at"]=corrected
    assert ingest_observations(frame,rid,"test","OHLCV","1d")==1
    assert ingest_observations(frame,rid,"test","OHLCV","1d")==0
    assert symbol in latest_event_timestamps([symbol])
    assert coverage_frame(tier,corrected).iloc[0].invalid_bars==0
    old=point_in_time(PointInTimeRequirement("test",(symbol,),as_of=broken))
    assert invalid_rows(old).any()
    current=point_in_time(PointInTimeRequirement("test",(symbol,),as_of=corrected))
    _assert_quality(current,"test")
