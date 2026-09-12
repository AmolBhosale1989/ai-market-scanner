from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
import os
import re
from io import StringIO
import pandas as pd
import requests
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


def _parse_alpha_vantage_calendar(body: str, tradable_df: pd.DataFrame, now=None):
    """Parse Alpha Vantage earnings-calendar CSV into Market Hunt event rows."""
    if tradable_df is None or tradable_df.empty or not str(body or "").strip():
        return []
    now=now or pd.Timestamp.now(tz="UTC")
    body=str(body).strip()
    if body.startswith("{"):
        return []

    try:
        df=pd.read_csv(StringIO(body))
    except Exception:
        return []
    if df.empty:
        return []

    symbol_col=next((x for x in ["symbol","Symbol","ticker","Ticker"] if x in df.columns),None)
    date_col=next((x for x in ["reportDate","report_date","date","Date"] if x in df.columns),None)
    if symbol_col is None or date_col is None:
        return []

    symbols=set(tradable_df["ticker"].astype(str))
    name_map=dict(zip(
        tradable_df["ticker"].astype(str),
        tradable_df.get("name",pd.Series([""]*len(tradable_df))).astype(str)
    ))
    adv_map=dict(zip(
        tradable_df["ticker"].astype(str),
        pd.to_numeric(tradable_df.get("avg_dollar_volume20",0),errors="coerce").fillna(0)
    ))

    rows=[]
    for _,r in df.iterrows():
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
            "event_source":"ALPHA_VANTAGE",
            "company_name":name_map.get(ticker,""),
            "avg_dollar_volume20":round(float(adv_map.get(ticker,0)),0),
        }
        for src,dst in [
            ("estimate","eps_estimate"),
            ("fiscalDateEnding","fiscal_date_ending"),
            ("currency","earnings_currency"),
            ("timeOfTheDay","earnings_report_time"),
        ]:
            if src in df.columns and pd.notna(r.get(src)):
                row[dst]=r.get(src)
        rows.append(row)

    dedup={}
    for row in rows:
        dedup[(row["ticker"],row["event_date_utc"])]=row
    return list(dedup.values())


def _alpha_vantage_earnings_events(tradable_df: pd.DataFrame):
    """Fetch the broad earnings calendar once from Alpha Vantage, then intersect with our liquid universe."""
    if tradable_df is None or tradable_df.empty:
        return []

    api_key=os.getenv("ALPHA_VANTAGE_API_KEY","").strip()
    if not api_key:
        print("Alpha Vantage earnings calendar disabled: ALPHA_VANTAGE_API_KEY is not configured.")
        return []

    now=pd.Timestamp.now(tz="UTC")

    try:
        r=requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function":"EARNINGS_CALENDAR",
                "horizon":"3month",
                "apikey":api_key,
            },
            timeout=30,
        )
        r.raise_for_status()
        body=r.text.strip()
        if not body:
            print("Alpha Vantage earnings calendar returned an empty response.")
            return []

        # Alpha Vantage may return JSON for quota/auth errors even though the
        # successful earnings-calendar response is CSV.
        if body.startswith("{"):
            try:
                payload=r.json()
            except Exception:
                payload={}
            message=payload.get("Information") or payload.get("Note") or payload.get("Error Message") or "unexpected JSON response"
            print(f"Alpha Vantage earnings calendar unavailable: {message}")
            return []

    except Exception as e:
        print(f"Alpha Vantage earnings calendar unavailable: {type(e).__name__}: {e}")
        return []

    rows=_parse_alpha_vantage_calendar(body,tradable_df,now=now)
    if not rows and body and not body.startswith("{"):
        print("Alpha Vantage earnings calendar returned no matching liquid events in the next 7 days.")
    return rows

def _write_event_status(status: str, count: int, detail: str=""):
    pd.DataFrame([{
        "provider":"ALPHA_VANTAGE",
        "configured":bool(os.getenv("ALPHA_VANTAGE_API_KEY","").strip()),
        "status":status,
        "event_count":int(count),
        "detail":detail,
        "checked_at_utc":pd.Timestamp.now(tz="UTC").isoformat(),
    }]).to_csv(OUTPUT_DIR/"event_status.csv",index=False)


def build_event_watchlist(tradable_df: pd.DataFrame):
    if tradable_df is None or tradable_df.empty:
        out=pd.DataFrame(columns=["ticker","company_name","event_type","event_date_utc","days_to_event","event_priority","event_source"])
        out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
        _write_event_status("NO_UNIVERSE",0,"Tradable universe was empty.")
        return out

    u=tradable_df.copy()
    if "tradable" in u.columns:
        u=u[u["tradable"].astype(bool)]
    if "avg_dollar_volume20" in u.columns:
        u=u.sort_values("avg_dollar_volume20",ascending=False)

    u=u.head(EVENT_SCAN_LIMIT).copy()
    name_map=dict(zip(u["ticker"].astype(str),u.get("name",pd.Series([""]*len(u))).astype(str)))
    adv_map=dict(zip(u["ticker"].astype(str),pd.to_numeric(u.get("avg_dollar_volume20",0),errors="coerce").fillna(0)))

    # Earnings are event-first and provider-first: one Alpha Vantage calendar
    # request is intersected with the liquid universe. We intentionally do not
    # fall back to Yahoo earnings endpoints because they have produced repeated
    # authorization/crumb failures in GitHub Actions.
    rows=_alpha_vantage_earnings_events(u)

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

    event_columns=[
        "ticker","company_name","event_type","event_date_utc","days_to_event",
        "event_priority","event_source","avg_dollar_volume20","event_headline",
        "event_provider","eps_estimate","fiscal_date_ending","earnings_currency","earnings_report_time"
    ]
    out=pd.DataFrame(rows)
    if out.empty:
        out=pd.DataFrame(columns=event_columns)
    else:
        order={"HIGH":3,"MEDIUM":2,"WATCH":1}
        out["_priority"]=out["event_priority"].map(order).fillna(0)
        out=out.sort_values(["_priority","days_to_event","avg_dollar_volume20"],ascending=[False,True,False])
        out=out.drop(columns=["_priority"]).reset_index(drop=True)
    out.to_csv(OUTPUT_DIR/"upcoming_events.csv",index=False)
    configured=bool(os.getenv("ALPHA_VANTAGE_API_KEY","").strip())
    earnings_count=int((out["event_type"].eq("EARNINGS")).sum()) if "event_type" in out.columns else 0
    if not configured:
        _write_event_status("KEY_MISSING",len(out),"Add GitHub Actions secret ALPHA_VANTAGE_API_KEY to enable earnings calendar.")
    elif earnings_count==0:
        _write_event_status("NO_UPCOMING_EARNINGS",len(out),"Provider connected; no matching liquid earnings events were returned inside the 7-day window.")
    else:
        _write_event_status("OK",len(out),f"{earnings_count} earnings events plus any explicit-date news events.")
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
