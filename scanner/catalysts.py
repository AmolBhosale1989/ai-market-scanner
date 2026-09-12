from __future__ import annotations

from datetime import datetime, timezone
import math
import re
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

COMPANY_STOPWORDS = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd", "limited",
    "plc", "holdings", "holding", "group", "common", "stock", "shares", "share",
    "class", "ordinary", "depositary", "adr", "the", "new"
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

def _normalize_symbol(value: str) -> str:
    return str(value or "").strip().upper().replace(".", "-")

def _extract_related_tickers(item, src):
    values = []
    for obj in (item, src):
        if not isinstance(obj, dict):
            continue
        for key in ("relatedTickers", "related_tickers", "tickers"):
            raw = obj.get(key)
            if isinstance(raw, (list, tuple, set)):
                values.extend(raw)
        finance = obj.get("finance")
        if isinstance(finance, dict):
            raw = finance.get("relatedTickers") or finance.get("tickers")
            if isinstance(raw, (list, tuple, set)):
                values.extend(raw)

    out = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("symbol") or value.get("ticker")
        sym = _normalize_symbol(value)
        if sym:
            out.add(sym)
    return sorted(out)

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

    return {
        "title": title,
        "provider": provider,
        "published": published_dt,
        "related_tickers": _extract_related_tickers(item, src),
    }

def _company_aliases(company_name: str):
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", str(company_name or "")).lower()
    tokens = [t for t in text.split() if t and t not in COMPANY_STOPWORDS and len(t) >= 3]
    if not tokens:
        return []

    aliases = []
    # Strongest signal is the leading company phrase, e.g. "kirby" or
    # "berkshire hathaway". Keep it short enough for headline matching.
    phrase = " ".join(tokens[:3])
    if len(phrase) >= 4:
        aliases.append(phrase)

    if len(tokens) >= 2:
        two = " ".join(tokens[:2])
        if two not in aliases:
            aliases.append(two)

    first = tokens[0]
    if len(first) >= 5 and first not in aliases:
        aliases.append(first)

    return aliases

def _news_relevance(ticker: str, company_name: str, item):
    target = _normalize_symbol(ticker)
    related = {_normalize_symbol(x) for x in item.get("related_tickers", []) if x}

    # When Yahoo supplies related tickers, use them as the strongest relevance
    # gate. This prevents a peer-company headline from being assigned to the
    # candidate merely because it appeared in the same news feed.
    if related:
        if target in related:
            return True, "RELATED_TICKER"
        return False, "RELATED_TICKER_MISMATCH"

    title = item.get("title", "")
    normalized_title = re.sub(r"[^A-Za-z0-9 ]+", " ", title).lower()
    padded = f" {normalized_title} "

    for alias in _company_aliases(company_name):
        if f" {alias} " in padded:
            return True, "COMPANY_NAME"

    # Ticker-in-title is allowed only for non-trivial symbols to avoid false
    # matches such as A, ON, IT, or ALL in normal English.
    raw_ticker = target.replace("-", "")
    if len(raw_ticker) >= 3:
        if re.search(rf"(?<![A-Za-z0-9])\$?{re.escape(target)}(?![A-Za-z0-9])", title, re.I):
            return True, "TICKER_IN_TITLE"

    return False, "NO_COMPANY_MATCH"

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

def analyze_catalyst(ticker: str, company_name: str = ""):
    now = datetime.now(timezone.utc)
    obj = yf.Ticker(ticker)
    relevant_items = []
    rejected_count = 0

    try:
        try:
            raw_news = obj.get_news(count=12)
        except TypeError:
            raw_news = obj.news

        for raw in raw_news or []:
            parsed = _extract_news_item(raw)
            if not parsed:
                continue
            relevant, reason = _news_relevance(ticker, company_name, parsed)
            parsed["relevance_reason"] = reason
            if relevant:
                relevant_items.append(parsed)
            else:
                rejected_count += 1
    except Exception:
        relevant_items = []

    best = None
    best_value = -10_000
    for item in relevant_items:
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
    relevance = "NO VERIFIED NEWS"

    if best:
        headline = best["title"]
        provider = best["provider"]
        age_hours = best["age_hours"]
        relevance = best.get("relevance_reason", "VERIFIED")
        signal = best["signal"]
        recency = 0
        if math.isfinite(age_hours):
            if age_hours <= 12:
                recency = 24
            elif age_hours <= 24:
                recency = 18
            elif age_hours <= CATALYST_LOOKBACK_HOURS:
                recency = 10
            elif age_hours <= 168:
                recency = 4

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
    negative_risk = bool(
        best
        and best["signal"] <= -10
        and math.isfinite(age_hours)
        and age_hours <= CATALYST_LOOKBACK_HOURS
    )

    if math.isfinite(earnings_days) and 0 <= earnings_days <= CATALYST_LOOKAHEAD_DAYS:
        catalyst_type = "UPCOMING EARNINGS" if catalyst_type == "NONE" else f"{catalyst_type} + EARNINGS"
        if bias == "NEUTRAL":
            bias = "EVENT"

    if catalyst_type == "NONE" and not relevant_items and not math.isfinite(earnings_days):
        status = "NO VERIFIED CATALYST"
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
        "catalyst_relevance": relevance,
        "rejected_news_count": int(rejected_count),
        "earnings_days": earnings_days,
        "negative_catalyst_risk": negative_risk,
    }

def enrich_candidates(df: pd.DataFrame, limit: int):
    if df.empty:
        return df

    out = df.copy()
    defaults = {
        "catalyst_score": 0,
        "catalyst_status": "NOT CHECKED",
        "catalyst_type": "NONE",
        "catalyst_bias": "NEUTRAL",
        "catalyst_headline": "",
        "catalyst_provider": "",
        "catalyst_age_hours": math.nan,
        "catalyst_relevance": "NOT CHECKED",
        "rejected_news_count": 0,
        "earnings_days": math.nan,
        "negative_catalyst_risk": False,
    }
    for col, value in defaults.items():
        out[col] = value

    eligible = out[out["stage"].isin(["CONFIRMED", "ARMED", "FORMING", "DISCOVER"])].copy()
    eligible = eligible.sort_values(["stage_rank", "rank_score"], ascending=[False, False]).head(limit)

    for idx, row in eligible.iterrows():
        ticker = str(row["ticker"])
        company_name = str(row.get("company_name", "") or "")
        try:
            cat = analyze_catalyst(ticker, company_name=company_name)
            for k, v in cat.items():
                out.at[idx, k] = v
        except Exception as e:
            out.at[idx, "catalyst_status"] = "ERROR"
            out.at[idx, "catalyst_headline"] = f"Catalyst lookup failed: {type(e).__name__}"
        time.sleep(0.05)

    return out
