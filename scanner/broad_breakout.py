from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from .warehouse import frames as warehouse_frames, history as warehouse_history

from .config import OUTPUT_DIR
from .session_contract import latest_frame_session

NY = ZoneInfo("America/New_York")


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


def _latest_session_date(frame: pd.DataFrame, fallback):
    """Backward-compatible wrapper for the shared production session contract."""
    return latest_frame_session(frame, fallback)


def _same_time_rvol(d: pd.DataFrame, today: pd.DataFrame, session_date) -> float:
    if today.empty:
        return math.nan
    bars=len(today)
    cur=float(pd.to_numeric(today["Volume"],errors="coerce").fillna(0).sum())
    prior_dates=sorted({x for x in d.index.date if x<session_date},reverse=True)[:2]
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


def run(batch_size: int = 120, top_n: int = 80, scan_limit: int = 420) -> pd.DataFrame:
    src=OUTPUT_DIR/"live_universe.csv"
    if not src.exists():
        raise RuntimeError("BROAD_BREAKOUT_INPUT_MISSING: shared live universe is unavailable")
    u=pd.read_csv(src)
    if u.empty or "ticker" not in u.columns:
        return pd.DataFrame()

    # Keep a broad liquid universe but prioritize names most capable of producing
    # outsized moves so the live scan completes reliably within GitHub runtime limits.
    for col in ["avg_dollar_volume20","adr20_pct","max_up_day_30d_pct","ret20_pct"]:
        if col not in u.columns:
            u[col]=0.0
        u[col]=pd.to_numeric(u[col],errors="coerce").fillna(0)
    u["_priority"]=(
        u["avg_dollar_volume20"].rank(pct=True)*0.45
        + u["adr20_pct"].rank(pct=True)*0.20
        + u["max_up_day_30d_pct"].rank(pct=True)*0.25
        + u["ret20_pct"].rank(pct=True)*0.10
    )
    tickers=u.head(scan_limit)["ticker"].dropna().astype(str).unique().tolist()
    now=datetime.now(NY)

    spy_raw=warehouse_history("SPY",period="3d",interval="5m",max_age_minutes=10)
    spy=_extract(spy_raw,"SPY")
    # Use the latest warehouse market session, not the wall-clock date. This
    # keeps discovery valid on weekends/holidays while preserving live-session
    # behavior when today's bars exist.
    session_date=_latest_session_date(spy, now.date())
    spy_today=spy[spy.index.date==session_date].between_time("09:30","16:00") if not spy.empty else pd.DataFrame()
    spy_prior_dates=sorted({x for x in spy.index.date if x<session_date},reverse=True) if not spy.empty else []
    spy_change=0.0
    if not spy_today.empty and spy_prior_dates:
        prior=spy[spy.index.date==spy_prior_dates[0]].between_time("09:30","16:00")
        if not prior.empty and float(prior["Close"].iloc[-1])>0:
            spy_change=(float(spy_today["Close"].iloc[-1])/float(prior["Close"].iloc[-1])-1)*100

    rows=[]
    gates={"warehouse_data":0,"session_data":0,"prior_session":0,"day_2pct":0,"rel_1_25pct":0,"rvol_1_25":0,"dollar_20m":0,"near_high":0,"qualified":0}
    for start in range(0,len(tickers),batch_size):
        batch=tickers[start:start+batch_size]
        raw=warehouse_frames(batch,period="3d",interval="5m",max_age_minutes=10,require_complete=False)
        for ticker in batch:
            d=_extract(raw.get(ticker,pd.DataFrame()),ticker)
            if d.empty:
                continue
            gates["warehouse_data"]+=1
            today=d[d.index.date==session_date].between_time("09:30","16:00")
            prior_dates=sorted({x for x in d.index.date if x<session_date},reverse=True)
            if today.empty:
                continue
            gates["session_data"]+=1
            if not prior_dates:
                continue
            gates["prior_session"]+=1
            prior=d[d.index.date==prior_dates[0]].between_time("09:30","16:00")
            if prior.empty:
                continue
            prior_close=float(prior["Close"].iloc[-1])
            if prior_close<=0:
                continue

            price=float(today["Close"].iloc[-1])
            day=(price/prior_close-1)*100
            rel=day-spy_change
            rvol=_same_time_rvol(d,today,session_date)
            vol=float(pd.to_numeric(today["Volume"],errors="coerce").fillna(0).sum())
            dollar=vol*price
            recent=today.tail(6)
            move30=((float(recent["Close"].iloc[-1])/float(recent["Close"].iloc[0])-1)*100
                    if len(recent)>=2 and float(recent["Close"].iloc[0]) else math.nan)
            session_high=float(today["High"].max())
            near_high=price>=session_high*0.985 if session_high>0 else False

            # Record cumulative gate survival so a zero-candidate run is diagnosable.
            if day < 2.0: continue
            gates["day_2pct"]+=1
            if rel < 1.25: continue
            gates["rel_1_25pct"]+=1
            if not math.isfinite(rvol) or rvol < 1.25: continue
            gates["rvol_1_25"]+=1
            if dollar < 20_000_000: continue
            gates["dollar_20m"]+=1
            if not near_high: continue
            gates["near_high"]+=1
            gates["qualified"]+=1

            # Theme-independent discovery: all gates above passed.
            qualifies=(
                day>=2.0
                and rel>=1.25
                and math.isfinite(rvol) and rvol>=1.25
                and dollar>=20_000_000
                and near_high
            )
            if not qualifies:
                continue

            score=max(0,min(100,
                45
                + min(day,12)*2.0
                + min(rel,10)*2.5
                + min(rvol,5)*5.0
                + (5 if math.isfinite(move30) and move30>0 else 0)
            ))
            rows.append({
                "ticker":ticker,
                "source":"BROAD_BREAKOUT",
                "theme":"UNCLASSIFIED / BROAD BREAKOUT",
                "theme_rotation_score":0.0,
                "rotation_leader_score":round(score,1),
                "rotation_leader":True,
                "broad_breakout_score":round(score,1),
                "last":round(price,2),
                "day_change_pct":round(day,2),
                "move_30m_pct":round(move30,2) if math.isfinite(move30) else math.nan,
                "intraday_volume":round(vol,0),
                "rel_vs_spy_pct":round(rel,2),
                "broad_rvol":round(rvol,2),
                "last_bar_et":today.index[-1].isoformat(),
            })

    out=pd.DataFrame(rows)
    if not out.empty:
        out=out.sort_values(["broad_breakout_score","rel_vs_spy_pct","day_change_pct"],ascending=[False,False,False]).head(top_n)
    out.to_csv(OUTPUT_DIR/"broad_breakout_discovery.csv",index=False)
    pd.DataFrame([{
        "updated_at_et":now.isoformat(timespec="seconds"),
        "universe_scanned":len(tickers),
        "scan_limit":scan_limit,
        "qualified_breakouts":len(out),
        "session_date":str(session_date),
        **gates,
        "mode":"THEME_INDEPENDENT_BROAD_BREAKOUT",
    }]).to_csv(OUTPUT_DIR/"broad_breakout_health.csv",index=False)
    return out


if __name__=="__main__":
    run()
