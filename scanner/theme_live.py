from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from .warehouse import DataRequirement, provide

from .config import (
    CORE_INTRADAY_MARKET_SYMBOLS,
    OUTPUT_DIR,
    THEME_INTRADAY_MAX_AGE_MINUTES,
    THEME_INTRADAY_MIN_COVERAGE,
)
from .session_contract import latest_frame_session
from .themes import THEMES, rank_themes

NY = ZoneInfo("America/New_York")


def _extract(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker in raw.columns.get_level_values(0):
            out=raw[ticker].copy()
        elif ticker in raw.columns.get_level_values(-1):
            out=raw.xs(ticker,axis=1,level=-1).copy()
        else:
            return pd.DataFrame()
    else:
        out=raw.copy()
    if "Close" not in out.columns:
        return pd.DataFrame()
    idx=pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx=idx.tz_localize("UTC")
    out.index=idx.tz_convert(NY)
    return out.dropna(subset=["Close"]).sort_index()


def _stats(d: pd.DataFrame, session_date):
    if d.empty:
        return math.nan,""
    today=d[d.index.date==session_date]
    prior_dates=sorted({x for x in d.index.date if x<session_date},reverse=True)
    if today.empty or not prior_dates:
        return math.nan,""
    prior=d[d.index.date==prior_dates[0]].between_time("09:30","16:00")
    if prior.empty:
        return math.nan,""
    prior_close=float(prior["Close"].iloc[-1])
    last=float(today["Close"].iloc[-1])
    move=(last/prior_close-1)*100 if prior_close>0 else math.nan
    return move,today.index[-1].isoformat()


def _load_theme_history(tickers: list[str]):
    return provide(DataRequirement(
        consumer="theme_live",tickers=tuple(tickers),interval="5m",period="5d",
        max_age_minutes=THEME_INTRADAY_MAX_AGE_MINUTES,view_name="theme_live_5m",
        minimum_fresh_coverage=THEME_INTRADAY_MIN_COVERAGE,
        required_fresh_tickers=tuple(x for x in tickers if x in CORE_INTRADAY_MARKET_SYMBOLS),
    ))


def run():
    base=rank_themes()
    if base is None or base.empty:
        return pd.DataFrame()

    tickers=["SPY"]+sorted({str(v["etf"]) for v in THEMES.values()})
    view=_load_theme_history(tickers)
    raw={}
    for ticker,g in view.frame.groupby("ticker"):
        x=g.copy(); x["bar_timestamp"]=pd.to_datetime(x["bar_timestamp"],utc=True,errors="coerce")
        raw[str(ticker)]=x.dropna(subset=["bar_timestamp"]).set_index("bar_timestamp")
    fresh_etfs=set(raw).intersection(tickers)-{"SPY"}
    expected_etfs=set(tickers)-{"SPY"}
    quarantined_etfs=sorted(expected_etfs-fresh_etfs)

    spy_frame=_extract(raw.get("SPY",pd.DataFrame()),"SPY")
    session_date=latest_frame_session(spy_frame)
    spy_move,spy_bar=_stats(spy_frame,session_date)
    out=base.copy()
    moves=[]; rels=[]; bars=[]
    for _,row in out.iterrows():
        move,bar=_stats(_extract(raw.get(str(row["etf"]),pd.DataFrame()),str(row["etf"])),session_date)
        moves.append(round(move,2) if math.isfinite(move) else math.nan)
        rel=move-spy_move if math.isfinite(move) and math.isfinite(spy_move) else math.nan
        rels.append(round(rel,2) if math.isfinite(rel) else math.nan)
        bars.append(bar)

    out["live_change_pct"]=moves
    out["live_rel_vs_spy_pct"]=rels
    out["live_bar_at_et"]=bars
    base_score=pd.to_numeric(out["theme_score"],errors="coerce").fillna(0)
    live_rel=pd.to_numeric(out["live_rel_vs_spy_pct"],errors="coerce").fillna(0)
    live_move=pd.to_numeric(out["live_change_pct"],errors="coerce").fillna(0)
    out["live_theme_score"]=(base_score*0.60+(50+live_rel*10+live_move*2).clip(0,100)*0.40).round(1)
    out=out.sort_values(["live_theme_score","live_rel_vs_spy_pct","theme_score"],ascending=[False,False,False]).reset_index(drop=True)
    out["theme_rank"]=range(1,len(out)+1)
    out["theme_state_live"]=pd.cut(out["live_theme_score"],[-1,49.99,59.99,69.99,100],
                                    labels=["WEAK","NEUTRAL","STRONG","LEADING"]).astype(str)

    OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
    out.to_csv(OUTPUT_DIR/"trending_themes.csv",index=False)
    pd.DataFrame([{
        "updated_at_et":datetime.now(NY).isoformat(timespec="seconds"),
        "session_date":str(session_date),
        "spy_live_change_pct":round(spy_move,2) if math.isfinite(spy_move) else math.nan,
        "spy_live_bar_at_et":spy_bar,
        "themes_ranked":len(out),
        "live_etfs_expected":len(expected_etfs),
        "live_etfs_fresh":len(fresh_etfs),
        "live_etfs_quarantined":len(quarantined_etfs),
        "quarantined_etf_sample":",".join(quarantined_etfs[:10]),
        "mode":"BATCHED_LIVE_EXTENDED_HOURS_PLUS_DAILY_TREND",
    }]).to_csv(OUTPUT_DIR/"theme_health.csv",index=False)
    return out

if __name__=="__main__":
    run()
