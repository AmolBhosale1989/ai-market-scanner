from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import time
import pandas as pd
from .warehouse import request_dataset

from .config import (
    CATALYST_LOOKBACK_HOURS,
    CATALYST_LOOKAHEAD_DAYS,
    CATALYST_CONTEXT_MAX_HOURS,
)

POSITIVE_TERMS={
    "raises guidance":18,"raised guidance":18,"guidance raised":18,
    "beats estimates":16,"beat estimates":16,"beats expectations":16,
    "record revenue":12,"record sales":12,"contract":10,"award":10,
    "order":8,"partnership":10,"strategic partnership":12,
    "approval":14,"fda approval":20,"clearance":12,
    "launch":8,"buyback":10,"repurchase":10,"upgrade":8,
    "price target raised":8,"expands":6,"expansion":6,
}
NEGATIVE_TERMS={
    "cuts guidance":-20,"cut guidance":-20,"guidance cut":-20,
    "misses estimates":-16,"missed estimates":-16,"misses expectations":-16,
    "offering":-14,"secondary offering":-16,"dilution":-16,
    "downgrade":-10,"price target cut":-8,"recall":-14,
    "investigation":-14,"lawsuit":-10,"fraud":-20,
    "bankruptcy":-25,"chapter 11":-25,"restatement":-14,
}
COMPANY_STOPWORDS={
    "inc","incorporated","corp","corporation","company","co","ltd","limited",
    "plc","holdings","holding","group","common","stock","shares","share",
    "class","ordinary","depositary","adr","the","new",
}

def _to_utc_datetime(value):
    if value is None: return None
    try:
        if isinstance(value,(int,float)) and math.isfinite(value):
            return datetime.fromtimestamp(value,tz=timezone.utc)
        ts=pd.Timestamp(value)
        if pd.isna(ts): return None
        ts=ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return ts.to_pydatetime()
    except Exception:
        return None

def _normalize_symbol(value):
    return str(value or "").strip().upper().replace(".","-")

def _extract_related_tickers(item,src):
    values=[]
    for obj in (item,src):
        if not isinstance(obj,dict): continue
        for key in ("relatedTickers","related_tickers","tickers"):
            raw=obj.get(key)
            if isinstance(raw,(list,tuple,set)): values.extend(raw)
        finance=obj.get("finance")
        if isinstance(finance,dict):
            raw=finance.get("relatedTickers") or finance.get("tickers")
            if isinstance(raw,(list,tuple,set)): values.extend(raw)
    out=set()
    for value in values:
        if isinstance(value,dict):
            value=value.get("symbol") or value.get("ticker")
        sym=_normalize_symbol(value)
        if sym: out.add(sym)
    return sorted(out)

def _extract_news_item(item):
    content=item.get("content") if isinstance(item,dict) else None
    src=content if isinstance(content,dict) else item
    if not isinstance(src,dict): return None
    title=str(src.get("title") or "").strip()
    provider=src.get("provider")
    if isinstance(provider,dict):
        provider=provider.get("displayName") or provider.get("name")
    provider=str(provider or item.get("publisher") or "").strip() if isinstance(item,dict) else ""
    published=(src.get("pubDate") or src.get("publicationDate") or src.get("displayTime")
               or (item.get("providerPublishTime") if isinstance(item,dict) else None))
    canonical=src.get("canonicalUrl") or src.get("clickThroughUrl") or src.get("url")
    if isinstance(canonical,dict):
        canonical=canonical.get("url")
    if not canonical and isinstance(item,dict):
        canonical=item.get("link") or item.get("url")
    if not title: return None
    return {
        "title":title,
        "provider":provider,
        "published":_to_utc_datetime(published),
        "related_tickers":_extract_related_tickers(item,src),
        "url":str(canonical or "").strip(),
    }

def _company_aliases(company_name):
    text=re.sub(r"[^A-Za-z0-9 ]+"," ",str(company_name or "")).lower()
    tokens=[t for t in text.split() if t and t not in COMPANY_STOPWORDS and len(t)>=3]
    if not tokens: return []
    aliases=[]
    for alias in (" ".join(tokens[:3])," ".join(tokens[:2]) if len(tokens)>=2 else "",tokens[0]):
        if len(alias)>=4 and alias not in aliases: aliases.append(alias)
    return aliases

def _news_relevance(ticker,company_name,item):
    target=_normalize_symbol(ticker)
    related={_normalize_symbol(x) for x in item.get("related_tickers",[]) if x}
    if related:
        return (True,"RELATED_TICKER") if target in related else (False,"RELATED_TICKER_MISMATCH")

    title=item.get("title","")
    normalized_title=re.sub(r"[^A-Za-z0-9 ]+"," ",title).lower()
    padded=f" {normalized_title} "
    for alias in _company_aliases(company_name):
        if f" {alias} " in padded:
            return True,"COMPANY_NAME"

    raw_ticker=target.replace("-","")
    if len(raw_ticker)>=3 and re.search(rf"(?<![A-Za-z0-9])\$?{re.escape(target)}(?![A-Za-z0-9])",title,re.I):
        return True,"TICKER_IN_TITLE"
    return False,"NO_COMPANY_MATCH"

def _headline_signal(title):
    t=title.lower()
    score=0
    matched=[]
    for phrase,points in POSITIVE_TERMS.items():
        if phrase in t:
            score+=points; matched.append(phrase)
    for phrase,points in NEGATIVE_TERMS.items():
        if phrase in t:
            score+=points; matched.append(phrase)
    return score,matched

def _freshness(age_hours):
    if not math.isfinite(age_hours):
        return 0.0,0,"UNKNOWN",False
    if age_hours<=24:
        return 1.0,18,"FRESH_24H",True
    if age_hours<=CATALYST_LOOKBACK_HOURS:
        return 0.80,10,"FRESH_72H",True
    if age_hours<=CATALYST_CONTEXT_MAX_HOURS:
        return 0.25,2,"CONTEXT_7D",False
    return 0.0,0,"HISTORICAL",False

def _next_earnings_days(ticker_obj):
    now=pd.Timestamp.now(tz="UTC")
    try:
        ed=ticker_obj.get_earnings_dates(limit=8)
        if isinstance(ed,pd.DataFrame) and len(ed):
            for idx in ed.index:
                ts=pd.Timestamp(idx)
                ts=ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                delta=(ts-now).total_seconds()/86400
                if delta>=-0.5: return round(delta,1)
    except Exception:
        pass
    try:
        cal=ticker_obj.calendar
        if isinstance(cal,dict):
            raw=cal.get("Earnings Date") or cal.get("EarningsDate")
            if raw:
                if not isinstance(raw,(list,tuple)): raw=[raw]
                future=[]
                for x in raw:
                    ts=pd.Timestamp(x)
                    ts=ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                    delta=(ts-now).total_seconds()/86400
                    if delta>=-0.5: future.append(delta)
                if future: return round(min(future),1)
    except Exception:
        pass
    return math.nan

def analyze_catalyst(ticker,company_name=""):
    # Catalyst/news/earnings data is supplied only by the warehouse manager.
    data=request_dataset("catalyst_context",consumer="catalysts",tickers=(str(ticker),),max_age_minutes=60)
    if data is None or data.empty:
        return {
            "catalyst_score":0,"catalyst_status":"WAREHOUSE DATA UNAVAILABLE","catalyst_type":"NONE",
            "catalyst_bias":"NEUTRAL","catalyst_headline":"","catalyst_provider":"",
            "catalyst_age_hours":math.nan,"catalyst_relevance":"NO WAREHOUSE DATA",
            "catalyst_fresh":False,"news_freshness_status":"UNAVAILABLE","catalyst_materiality":"NONE",
            "rejected_news_count":0,"earnings_days":math.nan,"negative_catalyst_risk":False,
        }
    row=data.iloc[0]
    return {k:row.get(k) for k in [
        "catalyst_score","catalyst_status","catalyst_type","catalyst_bias","catalyst_headline",
        "catalyst_provider","catalyst_age_hours","catalyst_relevance","catalyst_fresh",
        "news_freshness_status","catalyst_materiality","rejected_news_count","earnings_days",
        "negative_catalyst_risk"
    ]}

def enrich_candidates(df: pd.DataFrame,limit: int):
    """Legacy DataFrame boundary backed by the strict multi-provider warehouse contract."""
    if df.empty:
        return df
    from datetime import timedelta
    from .consumer_snapshot import consumer_anchor
    from .catalyst_contract import CatalystState,ProviderRequirement,resolve_catalyst_state

    out=df.copy()
    defaults={
        "catalyst_score":0,"catalyst_status":"NOT CHECKED","catalyst_type":"NONE",
        "catalyst_bias":"NEUTRAL","catalyst_headline":"","catalyst_provider":"",
        "catalyst_age_hours":math.nan,"catalyst_relevance":"NOT CHECKED",
        "catalyst_fresh":False,"news_freshness_status":"NOT CHECKED",
        "catalyst_materiality":"NONE","rejected_news_count":0,
        "earnings_days":math.nan,"negative_catalyst_risk":False,
        "catalyst_gate_ok":False,"catalyst_gate_reason":"NOT CHECKED",
        "sec_status":"NOT CHECKED","yahoo_status":"NOT CHECKED","earnings_status":"NOT CHECKED",
        "catalyst_provider_states":"",
    }
    for col,value in defaults.items():
        out[col]=value

    anchor=consumer_anchor()
    if anchor is None:
        raise RuntimeError("CATALYST_CONTEXT_ANCHOR_REQUIRED")
    anchor=pd.Timestamp(anchor)
    requirements=(
        ProviderRequirement("YAHOO_NEWS",timedelta(minutes=15)),
        ProviderRequirement("SEC_EDGAR",timedelta(minutes=15)),
        ProviderRequirement("ALPHA_VANTAGE",timedelta(hours=24)),
    )
    eligible=out[out["stage"].isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])].copy()
    eligible=eligible.sort_values(["stage_rank","rank_score"],ascending=[False,False]).head(limit)
    start_time=(anchor-pd.Timedelta(hours=CATALYST_CONTEXT_MAX_HOURS)).to_pydatetime()

    for idx,row in eligible.iterrows():
        ticker=str(row["ticker"])
        result=resolve_catalyst_state(ticker=ticker,as_of=anchor.to_pydatetime(),
                                      start_time=start_time,requirements=requirements)
        out.at[idx,"catalyst_status"]=result.status.value
        out.at[idx,"catalyst_gate_reason"]=result.reason
        states=dict(result.provider_states or {})
        out.at[idx,"sec_status"]=states.get("SEC_EDGAR","UNAVAILABLE")
        out.at[idx,"yahoo_status"]=states.get("YAHOO_NEWS","UNAVAILABLE")
        out.at[idx,"earnings_status"]=states.get("ALPHA_VANTAGE","UNAVAILABLE")
        out.at[idx,"catalyst_provider_states"]=";".join(f"{k}={v}" for k,v in sorted(states.items()))
        out.at[idx,"catalyst_gate_ok"]=result.status in {CatalystState.AVAILABLE,CatalystState.NO_EVENT}
        if result.status in {CatalystState.UNAVAILABLE,CatalystState.STALE}:
            # Blindness is not neutral market evidence. Preserve legacy columns
            # for downstream schema compatibility while forcing the trade gate off.
            out.at[idx,"news_freshness_status"]=result.status.value
            continue
        events=result.events
        if events is None or events.empty:
            out.at[idx,"catalyst_relevance"]="CONFIRMED NO NEW EVENT"
            out.at[idx,"news_freshness_status"]="FRESH CHECK"
            out.at[idx,"catalyst_fresh"]=True
            continue
        # Phase-1 cutover preserves event evidence without reintroducing direct
        # provider scoring. Existing V4 classification fields are carried in payload.
        payloads=[x if isinstance(x,dict) else {} for x in events.get("payload",pd.Series(dtype=object))]
        negative=any(bool(x.get("negative_veto",False)) for x in payloads)
        bonuses=[pd.to_numeric(x.get("catalyst_score_bonus",0),errors="coerce") for x in payloads]
        bonus=max([float(x) for x in bonuses if pd.notna(x)] or [0.0])
        latest=payloads[-1] if payloads else {}
        out.at[idx,"catalyst_score"]=max(0,min(100,20+bonus))
        out.at[idx,"negative_catalyst_risk"]=negative
        out.at[idx,"catalyst_type"]=str(latest.get("catalyst_type","CATALYST"))
        out.at[idx,"catalyst_bias"]="BEARISH" if negative else str(latest.get("catalyst_bias","EVENT"))
        out.at[idx,"catalyst_headline"]=str(latest.get("headline",""))[:220]
        out.at[idx,"catalyst_provider"]="MULTI_PROVIDER"
        out.at[idx,"catalyst_relevance"]="PIT VERIFIED"
        out.at[idx,"catalyst_fresh"]=True
        out.at[idx,"news_freshness_status"]="FRESH CHECK"
    return out

