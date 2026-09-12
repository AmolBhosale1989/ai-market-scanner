from __future__ import annotations

from datetime import datetime, timezone
import math
import time
import pandas as pd
import yfinance as yf

from .config import CATALYST_LOOKBACK_HOURS, CATALYST_LOOKAHEAD_DAYS

POSITIVE_TERMS = {
    "raises guidance": 18, "raised guidance": 18, "guidance raised": 18,
    "beats estimates": 16, "beat estimates": 16, "beats expectations": 16,
    "record revenue": 12, "record sales": 12, "contract": 10, "award": 10,
    "order": 8, "partnership": 10, "strategic partnership": 12,
    "approval": 14, "fda approval": 20, "clearance": 12,
    "launch": 8, "buyback": 10, "repurchase": 10, "upgrade": 8,
    "price target raised": 8, "expands": 6, "expansion": 6,
}
NEGATIVE_TERMS = {
    "cuts guidance": -20, "cut guidance": -20, "guidance cut": -20,
    "misses estimates": -16, "missed estimates": -16, "misses expectations": -16,
    "offering": -14, "secondary offering": -16, "dilution": -16,
    "downgrade": -10, "price target cut": -8, "recall": -14,
    "investigation": -14, "lawsuit": -10, "fraud": -20,
    "bankruptcy": -25, "chapter 11": -25, "restatement": -14,
}

def _to_utc_datetime(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) and math.isfinite(value):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.to_pydatetime()
    except Exception:
        return None

def _extract_news_item(item):
    content = item.get("content") if isinstance(item, dict) else None
    src = content if isinstance(content, dict) else item
    if not isinstance(src, dict):
        return None

    title = str(src.get("title") or "").strip()
    provider = src.get("provider")
    if isinstance(provider, dict):
        provider = provider.get("displayName") or provider.get("name")
    provider = str(provider or item.get("publisher") or "").strip() if isinstance(item, dict) else ""

    published = (
        src.get("pubDate")
        or src.get("publicationDate")
        or src.get("displayTime")
        or (item.get("providerPublishTime") if isinstance(item, dict) else None)
    )
    published_dt = _to_utc_datetime(published)
    if not title:
        return None
    return {"title": title, "provider": provider, "published": published_dt}

def _headline_signal(title: str):
    t = title.lower()
    score = 0
    matched = []
    for phrase, points in POSITIVE_TERMS.items():
        if phrase in t:
            score += points
            matched.append(phrase)
    for phrase, points in NEGATIVE_TERMS.items():
        if phrase in t:
            score += points
            matched.append(phrase)
    return score, matched

def _next_earnings_days(ticker_obj):
    now = pd.Timestamp.now(tz="UTC")
    try:
        ed = ticker_obj.get_earnings_dates(limit=8)
        if isinstance(ed, pd.DataFrame) and len(ed):
            for idx in ed.index:
                ts = pd.Timestamp(idx)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                else:
                    ts = ts.tz_convert("UTC")
                delta = (ts - now).total_seconds() / 86400
                if delta >= -0.5:
                    return round(delta, 1)
    except Exception:
        pass

    try:
        cal = ticker_obj.calendar
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date") or cal.get("EarningsDate")
            if raw:
                if not isinstance(raw, (list, tuple)):
                    raw = [raw]
                future = []
                for x in raw:
                    ts = pd.Timestamp(x)
                    if ts.tzinfo is None:
                        ts = ts.tz_localize("UTC")
                    else:
                        ts = ts.tz_convert("UTC")
                    delta = (ts - now).total_seconds() / 86400
                    if delta >= -0.5:
                        future.append(delta)
                if future:
                    return round(min(future), 1)
    except Exception:
        pass
    return math.nan

def analyze_catalyst(ticker: str):
    now = datetime.now(timezone.utc)
    obj = yf.Ticker(ticker)
    items = []
    try:
        try:
            raw_news = obj.get_news(count=10)
        except TypeError:
            raw_news = obj.news
        for raw in raw_news or []:
            parsed = _extract_news_item(raw)
            if parsed:
                items.append(parsed)
    except Exception:
        items = []

    best = None
    best_value = -10_000
    latest_age = math.nan
    for item in items:
        published = item["published"]
        age_hours = math.nan
        recency = 0
        if published:
            age_hours = max(0.0, (now - published).total_seconds() / 3600)
            if age_hours <= 12:
                recency = 24
            elif age_hours <= 24:
                recency = 18
            elif age_hours <= CATALYST_LOOKBACK_HOURS:
                recency = 10
            elif age_hours <= 168:
                recency = 4

        signal, matched = _headline_signal(item["title"])
        value = recency + abs(signal)
        if best is None or value > best_value:
            best_value = value
            best = {**item, "age_hours": age_hours, "signal": signal, "matched": matched}
        if math.isfinite(age_hours):
            latest_age = age_hours if not math.isfinite(latest_age) else min(latest_age, age_hours)

    earnings_days = _next_earnings_days(obj)
    earnings_score = 0
    if math.isfinite(earnings_days):
        if 0 <= earnings_days <= 3:
            earnings_score = 24
        elif earnings_days <= CATALYST_LOOKAHEAD_DAYS:
            earnings_score = 18
        elif earnings_days <= 14:
            earnings_score = 8

    news_score = 0
    bias = "NEUTRAL"
    headline = ""
    provider = ""
    age_hours = math.nan
    catalyst_type = "NONE"

    if best:
        headline = best["title"]
        provider = best["provider"]
        age_hours = best["age_hours"]
        signal = best["signal"]
        recency = 0
        if math.isfinite(age_hours):
            if age_hours <= 12: recency = 24
            elif age_hours <= 24: recency = 18
            elif age_hours <= CATALYST_LOOKBACK_HOURS: recency = 10
            elif age_hours <= 168: recency = 4
        news_score = max(-30, min(45, signal + recency))
        if signal >= 8:
            bias = "BULLISH"
            catalyst_type = "NEWS"
        elif signal <= -8:
            bias = "BEARISH"
            catalyst_type = "NEGATIVE NEWS"
        elif recency >= 10:
            catalyst_type = "FRESH NEWS"

    catalyst_score = max(0, min(100, 20 + news_score + earnings_score))
    negative_risk = bool(best and best["signal"] <= -10 and math.isfinite(age_hours) and age_hours <= CATALYST_LOOKBACK_HOURS)

    if math.isfinite(earnings_days) and 0 <= earnings_days <= CATALYST_LOOKAHEAD_DAYS:
        catalyst_type = "UPCOMING EARNINGS" if catalyst_type == "NONE" else f"{catalyst_type} + EARNINGS"
        if bias == "NEUTRAL":
            bias = "EVENT"

    if catalyst_type == "NONE" and not items and not math.isfinite(earnings_days):
        status = "NO DATA"
    elif negative_risk:
        status = "NEGATIVE RISK"
    elif catalyst_score >= 45:
        status = "STRONG"
    elif catalyst_score >= 30:
        status = "ACTIVE"
    else:
        status = "WEAK"

    return {
        "catalyst_score": int(round(catalyst_score)),
        "catalyst_status": status,
        "catalyst_type": catalyst_type,
        "catalyst_bias": bias,
        "catalyst_headline": headline[:220],
        "catalyst_provider": provider[:80],
        "catalyst_age_hours": round(age_hours, 1) if math.isfinite(age_hours) else math.nan,
        "earnings_days": earnings_days,
        "negative_catalyst_risk": negative_risk,
    }

def enrich_candidates(df: pd.DataFrame, limit: int):
    if df.empty:
        return df
    out = df.copy()
    defaults = {
        "catalyst_score": 0, "catalyst_status": "NOT CHECKED", "catalyst_type": "NONE",
        "catalyst_bias": "NEUTRAL", "catalyst_headline": "", "catalyst_provider": "",
        "catalyst_age_hours": math.nan, "earnings_days": math.nan, "negative_catalyst_risk": False,
    }
    for col, value in defaults.items():
        out[col] = value

    eligible = out[out["stage"].isin(["CONFIRMED", "ARMED", "FORMING", "DISCOVER"])].copy()
    eligible = eligible.sort_values(["stage_rank", "rank_score"], ascending=[False, False]).head(limit)

    for idx, row in eligible.iterrows():
        ticker = str(row["ticker"])
        try:
            cat = analyze_catalyst(ticker)
            for k, v in cat.items():
                out.at[idx, k] = v
        except Exception as e:
            out.at[idx, "catalyst_status"] = "ERROR"
            out.at[idx, "catalyst_headline"] = f"Catalyst lookup failed: {type(e).__name__}"
        time.sleep(0.05)
    return out
