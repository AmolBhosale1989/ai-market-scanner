from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from .warehouse import DataRequirement, provide

from .config import OUTPUT_DIR
from .order_flow import bar_order_flow_proxy
from .session_contract import latest_frame_session

NY = ZoneInfo("America/New_York")

MOMENTUM_COLUMNS = [
    "ticker","candidate_source","theme","signal","reason","price","day_change_pct",
    "move_30m_pct","rel_vs_spy_pct","theme_rotation_score","broad_breakout_score",
    "intraday_rvol","vwap","opening_range_high","above_vwap","above_or_high",
    "entry","stop","risk_pct","target_5pct","target_8pct","order_flow_score",
    "buy_pressure_pct","sell_pressure_pct","volume_imbalance_proxy","volume_impulse",
    "vwap_pressure","order_flow_state","order_flow_mode","last_bar_et",
]


def _read_optional(path) -> pd.DataFrame:
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (OSError,pd.errors.EmptyDataError,pd.errors.ParserError):
        return pd.DataFrame()


def _write_outputs(out: pd.DataFrame, now: datetime, session_date: str, candidate_inputs: int) -> pd.DataFrame:
    if out.empty:
        out=pd.DataFrame(columns=MOMENTUM_COLUMNS)
    out.to_csv(OUTPUT_DIR/"momentum_signals.csv",index=False)
    pd.DataFrame([{
        "updated_at_et":now.isoformat(timespec="seconds"),
        "session_date":session_date,
        "candidate_inputs":candidate_inputs,
        "leaders_evaluated":len(out),
        "momentum_buys":int(out["signal"].eq("MOMENTUM BUY").sum()),
        "extended_waits":int(out["signal"].eq("EXTENDED / WAIT RETEST").sum()),
        "mode":"FAST_ROTATION_MOMENTUM",
    }]).to_csv(OUTPUT_DIR/"momentum_health.csv",index=False)
    return out


def _upstream_session_date() -> str:
    for name in ("broad_breakout_health.csv","sector_rotation_health.csv"):
        health=_read_optional(OUTPUT_DIR/name)
        if not health.empty and "session_date" in health.columns:
            value=str(health.iloc[-1].get("session_date","")).strip()
            if value and value.lower()!="nan":
                return value
    return ""


def _extract(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker in raw.columns.get_level_values(0):
            d = raw[ticker].copy()
        elif ticker in raw.columns.get_level_values(-1):
            d = raw.xs(ticker, axis=1, level=-1).copy()
        else:
            return pd.DataFrame()
    else:
        d = raw.copy()
    need={"High","Low","Close","Volume"}
    if not need.issubset(d.columns):
        return pd.DataFrame()
    idx=pd.DatetimeIndex(d.index)
    if idx.tz is None:
        idx=idx.tz_localize("UTC")
    d.index=idx.tz_convert(NY)
    return d.dropna(subset=["Close"]).sort_index()


def _vwap(d: pd.DataFrame) -> float:
    vol=pd.to_numeric(d["Volume"],errors="coerce").fillna(0)
    denom=float(vol.sum())
    if denom<=0:
        return math.nan
    typical=(d["High"]+d["Low"]+d["Close"])/3
    return float((typical*vol).sum()/denom)


def _same_time_rvol(d: pd.DataFrame, today: pd.DataFrame, session_date) -> float:
    if today.empty:
        return math.nan
    bars=len(today)
    cur=float(pd.to_numeric(today["Volume"],errors="coerce").fillna(0).sum())
    prior_dates=sorted({x for x in d.index.date if x<session_date},reverse=True)[:3]
    comps=[]
    for dt in prior_dates:
        s=d[d.index.date==dt].between_time("09:30","16:00").iloc[:bars]
        if s.empty:
            continue
        v=float(pd.to_numeric(s["Volume"],errors="coerce").fillna(0).sum())
        if v>0:
            comps.append(v)
    if not comps or cur<=0:
        return math.nan
    base=sum(comps)/len(comps)
    return cur/base if base>0 else math.nan


def run(limit: int = 40):
    src=OUTPUT_DIR/"rotation_leaders.csv"
    broad_src=OUTPUT_DIR/"broad_breakout_discovery.csv"
    themed=_read_optional(src)
    broad=_read_optional(broad_src)
    now=datetime.now(NY)

    if not themed.empty:
        mask=themed.get("rotation_leader",pd.Series(False,index=themed.index)).astype(str).str.lower().isin(["true","1","yes"])
        themed=themed[mask].copy()
        if "source" not in themed.columns:
            themed["source"]="THEME_ROTATION"
    if not broad.empty:
        if "source" not in broad.columns:
            broad["source"]="BROAD_BREAKOUT"

    leaders=pd.concat([themed,broad],ignore_index=True,sort=False) if (not themed.empty or not broad.empty) else pd.DataFrame()
    if leaders.empty:
        return _write_outputs(pd.DataFrame(),now,_upstream_session_date(),0)

    if "theme_rotation_score" not in leaders.columns:
        leaders["theme_rotation_score"]=0.0
    if "rotation_leader_score" not in leaders.columns:
        leaders["rotation_leader_score"]=0.0
    if "broad_breakout_score" not in leaders.columns:
        leaders["broad_breakout_score"]=0.0
    leaders["theme_rotation_score"]=pd.to_numeric(leaders["theme_rotation_score"],errors="coerce").fillna(0)
    leaders["rotation_leader_score"]=pd.to_numeric(leaders["rotation_leader_score"],errors="coerce").fillna(0)
    leaders["broad_breakout_score"]=pd.to_numeric(leaders["broad_breakout_score"],errors="coerce").fillna(0)
    leaders["candidate_priority"]=leaders[["theme_rotation_score","rotation_leader_score","broad_breakout_score"]].max(axis=1)
    leaders=leaders.sort_values(["candidate_priority","rel_vs_spy_pct"],ascending=[False,False]).drop_duplicates("ticker").head(limit)
    candidate_inputs=len(leaders)
    tickers=leaders["ticker"].astype(str).tolist()
    view=provide(DataRequirement(consumer="momentum_signals",tickers=tuple(tickers),interval="5m",period="5d",max_age_minutes=10,view_name="momentum_signals_5m"))
    raw={}
    for ticker,g in view.frame.groupby("ticker"):
        x=g.copy(); x["bar_timestamp"]=pd.to_datetime(x["bar_timestamp"],utc=True,errors="coerce")
        raw[str(ticker)]=x.dropna(subset=["bar_timestamp"]).set_index("bar_timestamp")

    available=[_extract(frame,ticker) for ticker,frame in raw.items()]
    available=[frame for frame in available if not frame.empty]
    if not available:
        raise RuntimeError("MOMENTUM_SESSION_UNAVAILABLE: warehouse returned no usable frames")
    session_date=max(latest_frame_session(frame) for frame in available)

    rows=[]
    for _,meta in leaders.iterrows():
        ticker=str(meta["ticker"])
        d=_extract(raw.get(ticker,pd.DataFrame()),ticker)
        if d.empty:
            continue
        today=d[d.index.date==session_date].between_time("09:30","16:00")
        if today.empty:
            continue

        price=float(today["Close"].iloc[-1])
        vwap=_vwap(today)
        orb=today.between_time("09:30","09:59")
        or_high=float(orb["High"].max()) if not orb.empty else math.nan
        recent_low=float(today["Low"].tail(3).min()) if len(today)>=3 else float(today["Low"].min())
        rvol=_same_time_rvol(d,today,session_date)
        order_flow=bar_order_flow_proxy(today)
        day=float(meta.get("day_change_pct",math.nan))
        rel=float(meta.get("rel_vs_spy_pct",math.nan))
        move30=float(meta.get("move_30m_pct",math.nan))
        theme_score=float(meta.get("theme_rotation_score",0))
        candidate_source=str(meta.get("source","THEME_ROTATION"))
        broad_score=float(meta.get("broad_breakout_score",0) or 0)

        above_vwap=math.isfinite(vwap) and price>vwap
        above_or=math.isfinite(or_high) and price>or_high
        liquid_dollars=float(meta.get("intraday_volume",0))*price
        liquidity_ok=liquid_dollars>=20_000_000
        early_zone=math.isfinite(day) and 1.5<=day<=8.0
        extended=math.isfinite(day) and day>8.0
        themed_momentum_ok=(theme_score>=70 and rel>=1.0 and move30>0 and above_vwap and
                            (above_or or price>=float(today["High"].tail(4).max())*0.997) and
                            math.isfinite(rvol) and rvol>=1.20 and liquidity_ok)
        broad_momentum_ok=(candidate_source=="BROAD_BREAKOUT" and broad_score>=65 and rel>=1.25 and
                           above_vwap and (above_or or price>=float(today["High"].tail(4).max())*0.995) and
                           math.isfinite(rvol) and rvol>=1.25 and liquidity_ok)
        momentum_ok=bool(themed_momentum_ok or broad_momentum_ok)

        stop_anchor=min(vwap,recent_low) if math.isfinite(vwap) else recent_low
        stop=stop_anchor*0.997 if math.isfinite(stop_anchor) else math.nan
        risk_pct=(price/stop-1)*100 if math.isfinite(stop) and stop>0 else math.nan
        risk_ok=math.isfinite(risk_pct) and 0.3<=risk_pct<=4.0

        if momentum_ok and early_zone and risk_ok:
            signal="MOMENTUM BUY"
            reason=("BROAD BREAKOUT + VWAP + RVOL" if candidate_source=="BROAD_BREAKOUT" else "ROTATION + VWAP + ORB + RVOL")
        elif extended and momentum_ok:
            signal="EXTENDED / WAIT RETEST"
            reason="STRONG ROTATION BUT MOVE ALREADY >8%"
        elif momentum_ok:
            signal="WATCH / NEAR ENTRY"
            reason="LIVE MOMENTUM PRESENT; ENTRY/RISK FILTER NOT READY"
        else:
            signal="NO SIGNAL"
            reason="LIVE MOMENTUM CONDITIONS INCOMPLETE"

        rows.append({
            "ticker":ticker,
            "candidate_source":candidate_source,
            "theme":meta.get("theme",""),
            "signal":signal,
            "reason":reason,
            "price":round(price,2),
            "day_change_pct":round(day,2) if math.isfinite(day) else math.nan,
            "move_30m_pct":round(move30,2) if math.isfinite(move30) else math.nan,
            "rel_vs_spy_pct":round(rel,2) if math.isfinite(rel) else math.nan,
            "theme_rotation_score":round(theme_score,1),
            "broad_breakout_score":round(broad_score,1),
            "intraday_rvol":round(rvol,2) if math.isfinite(rvol) else math.nan,
            "vwap":round(vwap,2) if math.isfinite(vwap) else math.nan,
            "opening_range_high":round(or_high,2) if math.isfinite(or_high) else math.nan,
            "above_vwap":above_vwap,
            "above_or_high":above_or,
            "entry":round(price,2),
            "stop":round(stop,2) if math.isfinite(stop) else math.nan,
            "risk_pct":round(risk_pct,2) if math.isfinite(risk_pct) else math.nan,
            "target_5pct":round(price*1.05,2),
            "target_8pct":round(price*1.08,2),
            **order_flow,
            "last_bar_et":today.index[-1].isoformat(),
        })

    out=pd.DataFrame(rows,columns=MOMENTUM_COLUMNS)
    if not out.empty:
        rank={"MOMENTUM BUY":0,"WATCH / NEAR ENTRY":1,"EXTENDED / WAIT RETEST":2,"NO SIGNAL":3}
        out["_rank"]=out["signal"].map(rank).fillna(9)
        out=out.sort_values(["_rank","theme_rotation_score","rel_vs_spy_pct"],ascending=[True,False,False]).drop(columns=["_rank"])
    return _write_outputs(out,now,str(session_date),candidate_inputs)


if __name__=="__main__":
    run()
