from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from .warehouse import DataRequirement, frames as warehouse_frames, provide

from .config import OUTPUT_DIR
from .session_contract import latest_frame_session

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
    "Crypto & Digital Infrastructure": ["CIFR","IREN","MARA","RIOT","CLSK","HUT","WULF","CORZ","BTDR","BITF","CAN","ARBK","APLD"],
}

THEME_ETFS = {
    "Cybersecurity":"HACK","Semiconductors":"SMH","AI & Robotics":"BOTZ","Cloud Computing":"SKYY",
    "Biotechnology":"XBI","Defense & Aerospace":"ITA","Energy":"XLE","Uranium & Nuclear":"URA",
    "Gold Miners":"GDX","Clean Energy":"ICLN","Crypto & Digital Infrastructure":"WGMI",
}


def _extract(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns,pd.MultiIndex):
        if ticker in raw.columns.get_level_values(0):
            d=raw[ticker].copy()
        elif ticker in raw.columns.get_level_values(-1):
            d=raw.xs(ticker,axis=1,level=-1).copy()
        else:
            return pd.DataFrame()
    else:
        d=raw.copy()
    if "Close" not in d.columns:
        return pd.DataFrame()
    idx=pd.DatetimeIndex(d.index)
    if idx.tz is None:
        idx=idx.tz_localize("UTC")
    d.index=idx.tz_convert(NY)
    return d.dropna(subset=["Close"]).sort_index()


def _stats(d: pd.DataFrame, ticker: str, session_date):
    if d.empty:
        return None
    today=d[d.index.date==session_date]
    prior_dates=sorted({x for x in d.index.date if x<session_date},reverse=True)
    if today.empty or not prior_dates:
        return None
    prior=d[d.index.date==prior_dates[0]].between_time("09:30","16:00")
    if prior.empty:
        return None
    prior_close=float(prior["Close"].iloc[-1])
    last=float(today["Close"].iloc[-1])
    day=(last/prior_close-1)*100 if prior_close>0 else math.nan
    recent=today.tail(6)
    move30=((float(recent["Close"].iloc[-1])/float(recent["Close"].iloc[0])-1)*100
            if len(recent)>=2 and float(recent["Close"].iloc[0]) else math.nan)
    vol=float(pd.to_numeric(today.get("Volume",0),errors="coerce").fillna(0).sum())
    return {"ticker":ticker,"last":round(last,2),"day_change_pct":round(day,2),
            "move_30m_pct":round(move30,2) if math.isfinite(move30) else math.nan,
            "intraday_volume":round(vol,0),"last_bar_et":today.index[-1].isoformat()}


def _load_rotation_history() -> tuple[list[str], dict[str, pd.DataFrame]]:
    core=sorted({"SPY",*THEME_ETFS.values()})
    members=sorted({ticker for values in THEME_CONSTITUENTS.values() for ticker in values}-set(core))
    view=provide(DataRequirement(
        consumer="sector_rotation.core",tickers=tuple(core),interval="5m",period="5d",
        max_age_minutes=10,view_name="sector_rotation_core_5m",
    ))
    raw={}
    for ticker,g in view.frame.groupby("ticker"):
        x=g.copy(); x["bar_timestamp"]=pd.to_datetime(x["bar_timestamp"],utc=True,errors="coerce")
        raw[str(ticker)]=x.dropna(subset=["bar_timestamp"]).set_index("bar_timestamp")
    raw.update(warehouse_frames(
        members,period="5d",interval="5m",max_age_minutes=10,require_complete=False
    ))
    return sorted(set(core)|set(members)),raw


def run():
    OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
    tickers,raw=_load_rotation_history()

    spy_frame=_extract(raw.get("SPY",pd.DataFrame()),"SPY")
    session_date=latest_frame_session(spy_frame)
    cache={t:_stats(_extract(raw.get(t,pd.DataFrame()),t),t,session_date) for t in tickers}
    spy=cache.get("SPY")
    if not spy:
        raise RuntimeError(f"SECTOR_ROTATION_SESSION_EMPTY: SPY has no data for {session_date}")
    spy_chg=float(spy["day_change_pct"])

    theme_rows=[]; leader_rows=[]
    for theme,etf in THEME_ETFS.items():
        es=cache.get(etf)
        if not es:
            continue
        rel=float(es["day_change_pct"])-spy_chg
        member_stats=[cache.get(t) for t in THEME_CONSTITUENTS.get(theme,[]) if cache.get(t)]
        positive=sum(1 for s in member_stats if float(s["day_change_pct"])>spy_chg)
        breadth=(positive/len(member_stats)*100) if member_stats else 0.0
        move30=float(es.get("move_30m_pct") or 0)
        rotation_score=max(0,min(100,50+rel*12+move30*4+breadth*0.25))
        state=("ROTATION_LEADER" if rotation_score>=70 and rel>0.5
               else "STRONG_ROTATION" if rotation_score>=60 and rel>0
               else "NEUTRAL")
        theme_rows.append({
            "theme":theme,"etf":etf,"etf_change_pct":es["day_change_pct"],
            "rel_vs_spy_pct":round(rel,2),"breadth_pct":round(breadth,1),
            "rotation_score":round(rotation_score,1),"rotation_state":state,
            "updated_at_et":es["last_bar_et"]
        })
        for s in member_stats:
            stock_rel=float(s["day_change_pct"])-spy_chg
            score=max(0,min(100,50+stock_rel*10+float(s.get("move_30m_pct") or 0)*4))
            leader_rows.append({
                **s,"theme":theme,"theme_rotation_score":round(rotation_score,1),
                "theme_rotation_state":state,"rel_vs_spy_pct":round(stock_rel,2),
                "rotation_leader_score":round(score,1),
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
        "session_date":str(session_date),
        "spy_change_pct":spy_chg,"themes_scanned":len(themes),"stocks_scanned":len(leaders),
        "rotation_leaders":int(leaders["rotation_leader"].sum()) if not leaders.empty else 0,
        "mode":"BATCHED_INTRADAY_ROTATION",
    }]).to_csv(OUTPUT_DIR/"sector_rotation_health.csv",index=False)
    return themes,leaders

if __name__=="__main__":
    run()
