from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
import re
import pandas as pd
import yfinance as yf

from .config import EVENT_SCAN_LIMIT, EVENT_LOOKAHEAD_DAYS, EVENT_MAX_WORKERS, EVENT_NEWS_SCAN_LIMIT, OUTPUT_DIR
from .catalysts import _extract_news_item, _news_relevance


EVENT_PATTERNS = [
    ("FDA_PDUFA", ["pdufa","fda decision","fda action date","regulatory decision"]),
    ("INVESTOR_DAY", ["investor day","analyst day","capital markets day"]),
    ("CONFERENCE", ["conference","fireside chat","to present","presentation"]),
    ("PRODUCT_LAUNCH", ["product launch","launch event","will launch","unveils"]),
    ("CLINICAL_DATA", ["clinical data","trial data","study results","topline data"]),
    ("CONTRACT_AWARD", ["contract award","award announcement","contract decision"]),
]

_MONTH_RE = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"

def _explicit_event_date(title: str):
    """Parse an explicit month/day date from a headline; no vague 'next week' guesses."""
    now=pd.Timestamp.now(tz="UTC")
    m=re.search(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",title,re.I)
    if not m:
        m=re.search(rf"\b(\d{{1,2}})\s+{_MONTH_RE}\b",title,re.I)
        if m:
            day=int(m.group(1)); month=m.group(2)
        else:
            return None
    else:
        month=m.group(1); day=int(m.group(2))
    try:
        ts=pd.Timestamp(f"{month} {day} {now.year}",tz="UTC")
        if ts < now-pd.Timedelta(days=1):
            ts=pd.Timestamp(f"{month} {day} {now.year+1}",tz="UTC")
        return ts
    except Exception:
        return None

def _news_forward_event(ticker: str, company_name: str):
    obj=yf.Ticker(ticker)
    try:
        try:
            raw=obj.get_news(count=12)
        except TypeError:
            raw=obj.news
    except Exception:
        return None

    now=pd.Timestamp.now(tz="UTC")
    candidates=[]
    for item in raw or []:
        parsed=_extract_news_item(item)
        if not parsed:
            continue
        relevant,_=_news_relevance(ticker,company_name,parsed)
        if not relevant:
            continue
        title=parsed["title"]
        lower=title.lower()
        event_type=None
        for kind,phrases in EVENT_PATTERNS:
            if any(p in lower for p in phrases):
                event_type=kind
                break
        if not event_type:
            continue
        ts=_explicit_event_date(title)
        if ts is None:
            continue
        delta=(ts-now).total_seconds()/86400
        if not (0 <= delta <= EVENT_LOOKAHEAD_DAYS):
            continue
        priority="HIGH" if delta<=3 else ("MEDIUM" if delta<=5 else "WATCH")
        candidates.append({
            "ticker":ticker,
            "event_type":event_type,
            "event_date_utc":ts.isoformat(),
            "days_to_event":round(delta,2),
            "event_priority":priority,
            "event_source":"ANNOUNCED_NEWS",
            "event_headline":title[:220],
            "event_provider":parsed.get("provider","")[:80],
        })
    if not candidates:
        return None
    return min(candidates,key=lambda x:x["days_to_event"])

def _to_utc(value):
    try:
        ts=pd.Timestamp(value)
        if pd.isna(ts):
            return None
        ts=ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts
    except Exception:
        return None


def _global_earnings_events(tradable_df: pd.DataFrame):
    """Use Yahoo's global earnings calendar, then intersect with our tradable universe."""
    if tradable_df is None or tradable_df.empty or not hasattr(yf,"Calendars"):
        return []

    now=pd.Timestamp.now(tz="UTC")
    end=now+pd.Timedelta(days=EVENT_LOOKAHEAD_DAYS)
    symbols=set(tradable_df["ticker"].astype(str))
    name_map=dict(zip(tradable_df["ticker"].astype(str),tradable_df.get("name",pd.Series([""]*len(tradable_df))).astype(str)))
    adv_map=dict(zip(
        tradable_df["ticker"].astype(str),
        pd.to_numeric(tradable_df.get("avg_dollar_volume20",0),errors="coerce").fillna(0)
    ))

    rows=[]
    try:
        cal=yf.Calendars(start=now.date(),end=end.date())
        for offset in range(0,500,100):
            try:
                df=cal.get_earnings_calendar(
                    limit=100,offset=offset,filter_most_active=False,force=True
                )
            except TypeError:
                df=cal.get_earnings_calendar(limit=100,offset=offset,filter_most_active=False)
            if df is None or len(df)==0:
                break

            x=df.reset_index()
            symbol_col=next((k for k in ["Symbol","symbol","ticker","Ticker"] if k in x.columns),None)
            date_col=next((k for k in ["Event Start Date","startdatetime","Start Date","Earnings Date"] if k in x.columns),None)
            if symbol_col is None or date_col is None:
                continue

            for _,r in x.iterrows():
                ticker=str(r.get(symbol_col,"")).strip().upper().replace(".","-")
                if ticker not in symbols:
                    continue
                ts=_to_utc(r.get(date_col))
                if ts is None:
                    continue
                delta=(ts-now).total_seconds()/86400
                if not (-0.25 <= delta <= EVENT_LOOKAHEAD_DAYS):
                    continue
                priority="HIGH" if delta<=3 else ("MEDIUM" if delta<=5 else "WATCH")
                row={
                    "ticker":ticker,
                    "event_type":"EARNINGS",
                    "event_date_utc":ts.isoformat(),
                    "days_to_event":round(delta,2),
                    "event_priority":priority,
                    "event_source":"YAHOO_GLOBAL_CALENDAR",
                    "company_name":name_map.get(ticker,str(r.get("Company","") or "")),
                    "avg_dollar_volume20":round(float(adv_map.get(ticker,0)),0),
                }
                if "EPS Estimate" in x.columns and pd.notna(r.get("EPS Estimate")):
                    row["eps_estimate"]=r.get("EPS Estimate")
                if "Marketcap" in x.columns and pd.notna(r.get("Marketcap")):
                    row["event_market_cap"]=r.get("Marketcap")
                rows.append(row)

            if len(df)<100:
                break
    except Exception as e:
        print(f"Global earnings calendar unavailable: {type(e).__name__}: {e}")
        return []

    # One row per ticker/event date.
    dedup={}
    for row in rows:
        key=(row["ticker"],row["event_date_utc"])
        dedup[key]=row
    return list(dedup.values())


def _calendar_earnings(ticker: str):
    obj=yf.Ticker(ticker)
    now=pd.Timestamp.now(tz="UTC")
    dates=[]

    try:
        cal=obj.calendar
        if isinstance(cal,dict):
            raw=cal.get("Earnings Date") or cal.get("EarningsDate")
            if raw is not None:
                raw=raw if isinstance(raw,(list,tuple)) else [raw]
                for x in raw:
                    ts=_to_utc(x)
                    if ts is not None:
                        dates.append(ts)
    except Exception:
        pass

    # Always query the earnings-date history/calendar as a second source.
    # Yahoo's compact calendar can contain stale/past dates, so treating any
    # calendar response as authoritative can incorrectly suppress future events.
    try:
        ed=obj.get_earnings_dates(limit=12)
        if isinstance(ed,pd.DataFrame) and len(ed):
            for idx in ed.index:
                ts=_to_utc(idx)
                if ts is not None:
                    dates.append(ts)
    except Exception:
        pass

    # De-duplicate after combining both Yahoo sources.
    unique={}
    for ts in dates:
        unique[ts.isoformat()]=ts
    dates=list(unique.values())

    future=[]
    for ts in dates:
        delta=(ts-now).total_seconds()/86400
        if -0.5<=delta<=EVENT_LOOKAHEAD_DAYS:
            future.append((delta,ts))

    if not future:
        return None

    delta,ts=min(future,key=lambda x:x[0])
    if delta<=3:
        priority="HIGH"
    elif delta<=5:
        priority="MEDIUM"
    else:
        priority="WATCH"

    return {
        "ticker":ticker,
        "event_type":"EARNINGS",
        "event_date_utc":ts.isoformat(),
        "days_to_event":round(delta,2),
        "event_priority":priority,
        "event_source":"Yahoo Finance",
    }

def build_event_watchlist(tradable_df: pd.DataFrame):
    if tradable_df is None or tradable_df.empty:
        out=pd.DataFrame()
        out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
        return out

    u=tradable_df.copy()
    if "tradable" in u.columns:
        u=u[u["tradable"].astype(bool)]
    if "avg_dollar_volume20" in u.columns:
        u=u.sort_values("avg_dollar_volume20",ascending=False)

    u=u.head(EVENT_SCAN_LIMIT).copy()
    name_map=dict(zip(u["ticker"].astype(str),u.get("name",pd.Series([""]*len(u))).astype(str)))
    adv_map=dict(zip(u["ticker"].astype(str),pd.to_numeric(u.get("avg_dollar_volume20",0),errors="coerce").fillna(0)))

    # Preferred path: one global Yahoo earnings-calendar query, intersected with
    # the liquid universe. This avoids unreliable per-ticker future-date calls.
    rows=_global_earnings_events(u)

    # Backward-compatible fallback only when the global calendar produced no rows.
    if not rows:
        with ThreadPoolExecutor(max_workers=EVENT_MAX_WORKERS) as ex:
            futures={ex.submit(_calendar_earnings,str(t)):str(t) for t in u["ticker"].astype(str)}
            for fut in as_completed(futures):
                ticker=futures[fut]
                try:
                    row=fut.result()
                    if row:
                        row["company_name"]=name_map.get(ticker,"")
                        row["avg_dollar_volume20"]=round(float(adv_map.get(ticker,0)),0)
                        rows.append(row)
                except Exception:
                    continue

    # Conservative non-earnings layer: only announced future events with an
    # explicit calendar date in the headline are accepted.
    news_subset=u.head(EVENT_NEWS_SCAN_LIMIT)
    with ThreadPoolExecutor(max_workers=EVENT_MAX_WORKERS) as ex:
        futures={
            ex.submit(_news_forward_event,str(t),name_map.get(str(t),"")):str(t)
            for t in news_subset["ticker"].astype(str)
        }
        for fut in as_completed(futures):
            ticker=futures[fut]
            try:
                row=fut.result()
                if row:
                    row["company_name"]=name_map.get(ticker,"")
                    row["avg_dollar_volume20"]=round(float(adv_map.get(ticker,0)),0)
                    rows.append(row)
            except Exception:
                continue

    out=pd.DataFrame(rows)
    if not out.empty:
        order={"HIGH":3,"MEDIUM":2,"WATCH":1}
        out["_priority"]=out["event_priority"].map(order).fillna(0)
        out=out.sort_values(["_priority","days_to_event","avg_dollar_volume20"],ascending=[False,True,False])
        out=out.drop(columns=["_priority"]).reset_index(drop=True)
    out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
    return out

def merge_technical_context(events: pd.DataFrame, candidates: pd.DataFrame):
    if events is None or events.empty:
        return events
    out=events.copy()
    if candidates is None or candidates.empty:
        return out

    cols=[
        "ticker","price","stage","technical_stage","pattern","technical_score","risk_score",
        "entry_trigger","entry_model","stop","effective_target","effective_rr",
        "runway_to_next_resistance_pct","market_regime_state","market_regime_score",
    ]
    keep=[c for c in cols if c in candidates.columns]
    if "ticker" not in keep:
        return out
    tech=candidates[keep].drop_duplicates("ticker")
    out=out.merge(tech,on="ticker",how="left")
    out["pre_event_setup_state"]=out.get("stage",pd.Series(index=out.index,dtype=object)).fillna("NO TECHNICAL SETUP")
    out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
    return out
