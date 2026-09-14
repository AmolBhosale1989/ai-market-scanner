from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from .config import OUTPUT_DIR

NY = ZoneInfo("America/New_York")

THEME_CONSTITUENTS = {
    "Cybersecurity": ["CRWD","PANW","FTNT","ZS","S","CYBR","OKTA","TENB","RBRK","VRNS","QLYS","GEN","CHKP"],
    "Semiconductors": ["NVDA","AMD","AVGO","MU","MRVL","ARM","INTC","QCOM","TSM","ASML","LRCX","AMAT","KLAC","MPWR","ON"],
    "AI & Robotics": ["NVDA","PLTR","AI","PATH","SYM","TER","ROK","ISRG","ABB","CGNX"],
    "Cloud Computing": ["SNOW","DDOG","NET","MDB","NOW","CRM","ORCL","AMZN","MSFT","GTLB","ESTC"],
    "Biotechnology": ["MRNA","BMRN","VRTX","REGN","ALNY","NBIX","IONS","CRSP","BEAM","NTLA","IOVA"],
    "Defense & Aerospace": ["LMT","NOC","RTX","GD","LHX","HII","KTOS","AVAV","RKLB"],
    "Energy": ["XOM","CVX","COP","EOG","OXY","FANG","DVN","MPC","VLO"],
    "Uranium & Nuclear": ["CCJ","UEC","UUUU","NXE","DNN","SMR","OKLO","LEU"],
    "Gold Miners": ["NEM","AEM","GOLD","KGC","AU","WPM","FNV"],
    "Clean Energy": ["FSLR","ENPH","SEDG","RUN","NXT","BE","PLUG"],
}

THEME_ETFS = {
    "Cybersecurity":"HACK","Semiconductors":"SMH","AI & Robotics":"BOTZ","Cloud Computing":"SKYY",
    "Biotechnology":"XBI","Defense & Aerospace":"ITA","Energy":"XLE","Uranium & Nuclear":"URA",
    "Gold Miners":"GDX","Clean Energy":"ICLN",
}


def _bars(ticker: str) -> pd.DataFrame:
    try:
        d = yf.download(ticker, period="5d", interval="5m", auto_adjust=True, progress=False,
                        threads=False, prepost=True, timeout=20)
    except TypeError:
        d = yf.download(ticker, period="5d", interval="5m", auto_adjust=True, progress=False,
                        threads=False, prepost=True)
    if d is None or d.empty:
        return pd.DataFrame()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = [x[0] for x in d.columns]
    idx = pd.DatetimeIndex(d.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    d.index = idx.tz_convert(NY)
    return d.sort_index()


def _stats(ticker: str):
    d = _bars(ticker)
    if d.empty:
        return None
    now = datetime.now(NY)
    today = d[d.index.date == now.date()]
    prior_dates = sorted({x for x in d.index.date if x < now.date()}, reverse=True)
    if today.empty or not prior_dates:
        return None
    prior = d[d.index.date == prior_dates[0]].between_time("09:30","16:00")
    if prior.empty:
        return None
    prior_close = float(prior["Close"].iloc[-1])
    last = float(today["Close"].iloc[-1])
    chg = (last/prior_close - 1)*100 if prior_close else math.nan
    vol = float(pd.to_numeric(today.get("Volume",0), errors="coerce").fillna(0).sum())
    last30 = today.tail(6)
    move30 = ((float(last30["Close"].iloc[-1])/float(last30["Close"].iloc[0])-1)*100
              if len(last30) >= 2 and float(last30["Close"].iloc[0]) else math.nan)
    return {
        "ticker":ticker,"last":round(last,2),"day_change_pct":round(chg,2),
        "move_30m_pct":round(move30,2) if math.isfinite(move30) else math.nan,
        "intraday_volume":round(vol,0),"last_bar_et":today.index[-1].isoformat()
    }


def run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    spy = _stats("SPY")
    spy_chg = float(spy["day_change_pct"]) if spy else 0.0

    theme_rows=[]
    leader_rows=[]
    for theme, etf in THEME_ETFS.items():
        etf_stats=_stats(etf)
        if not etf_stats:
            continue
        rel=float(etf_stats["day_change_pct"])-spy_chg
        theme_score=max(0,min(100,50 + rel*12 + float(etf_stats.get("move_30m_pct") or 0)*5))
        members=THEME_CONSTITUENTS.get(theme,[])
        positive=0; member_stats=[]
        for ticker in members:
            s=_stats(ticker)
            if not s:
                continue
            member_stats.append(s)
            if float(s["day_change_pct"]) > spy_chg:
                positive += 1
        breadth=(positive/len(member_stats)*100) if member_stats else 0
        rotation_score=max(0,min(100,theme_score*0.7+breadth*0.3))
        state=("ROTATION_LEADER" if rotation_score>=70 and rel>0.5
               else "STRONG_ROTATION" if rotation_score>=60 and rel>0
               else "NEUTRAL")
        theme_rows.append({
            "theme":theme,"etf":etf,"etf_change_pct":etf_stats["day_change_pct"],
            "rel_vs_spy_pct":round(rel,2),"breadth_pct":round(breadth,1),
            "rotation_score":round(rotation_score,1),"rotation_state":state,
            "updated_at_et":etf_stats["last_bar_et"]
        })
        for s in member_stats:
            stock_rel=float(s["day_change_pct"])-spy_chg
            leader_score=max(0,min(100,50 + stock_rel*10 + float(s.get("move_30m_pct") or 0)*4))
            leader_rows.append({
                **s,"theme":theme,"theme_rotation_score":round(rotation_score,1),
                "theme_rotation_state":state,"rel_vs_spy_pct":round(stock_rel,2),
                "rotation_leader_score":round(leader_score,1),
                "rotation_leader":bool(state in {"ROTATION_LEADER","STRONG_ROTATION"} and stock_rel>0.75),
            })

    themes=pd.DataFrame(theme_rows)
    leaders=pd.DataFrame(leader_rows)
    if not themes.empty:
        themes=themes.sort_values(["rotation_score","rel_vs_spy_pct"],ascending=[False,False]).reset_index(drop=True)
        themes["rotation_rank"]=range(1,len(themes)+1)
    if not leaders.empty:
        leaders=leaders.sort_values(["rotation_leader","rotation_leader_score","day_change_pct"],
                                    ascending=[False,False,False]).reset_index(drop=True)
        leaders["rotation_rank"]=range(1,len(leaders)+1)
    themes.to_csv(OUTPUT_DIR/"sector_rotation.csv",index=False)
    leaders.to_csv(OUTPUT_DIR/"rotation_leaders.csv",index=False)
    pd.DataFrame([{
        "updated_at_et":datetime.now(NY).isoformat(timespec="seconds"),
        "spy_change_pct":spy_chg,"themes_scanned":len(themes),"stocks_scanned":len(leaders),
        "rotation_leaders":int(leaders["rotation_leader"].sum()) if not leaders.empty else 0,
    }]).to_csv(OUTPUT_DIR/"sector_rotation_health.csv",index=False)
    return themes, leaders


if __name__ == "__main__":
    run()
