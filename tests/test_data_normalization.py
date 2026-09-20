import pandas as pd
from scanner import data

def _frame(columns):
    idx=pd.DatetimeIndex(["2026-09-18T13:30:00Z"])
    return pd.DataFrame([[1.0,2.0,0.5,1.5,1000]],index=idx,columns=columns)

def test_single_ticker_grouped_ticker_price(monkeypatch):
    cols=pd.MultiIndex.from_tuples([("SPY",x) for x in ["Open","High","Low","Close","Volume"]])
    monkeypatch.setattr(data.yf,"download",lambda **kwargs:_frame(cols))
    out=data._download_once(["SPY"],"5d","5m")["SPY"]
    assert {"Open","High","Low","Close","Volume"}.issubset(out.columns)

def test_single_ticker_grouped_price_ticker(monkeypatch):
    cols=pd.MultiIndex.from_tuples([(x,"SPY") for x in ["Open","High","Low","Close","Volume"]])
    monkeypatch.setattr(data.yf,"download",lambda **kwargs:_frame(cols))
    out=data._download_once(["SPY"],"5d","5m")["SPY"]
    assert {"Open","High","Low","Close","Volume"}.issubset(out.columns)
    assert out.iloc[0]["Close"] == 1.5
