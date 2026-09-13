from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import urlencode

import pandas as pd
import requests
import yfinance as yf

from ..catalysts import NEGATIVE_TERMS, POSITIVE_TERMS, _extract_news_item, _news_relevance
from .contracts import EventType, MarketEvent
from .health import parse_utc


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_TICKERS_FALLBACK_URL = (
    "https://raw.githubusercontent.com/jadchaar/sec-cik-mapper/"
    "main/mappings/stocks/ticker_to_cik.json"
)
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
NASDAQ_FILINGS_URL = "https://api.nasdaq.com/api/company/{ticker}/sec-filings?limit=50"
DEFAULT_SEC_USER_AGENT = "Market Hunt AmolBhosale1989@users.noreply.github.com"
MATERIAL_FORMS = {
    "8-K", "8-K/A", "6-K", "6-K/A", "10-K", "10-K/A", "10-Q", "10-Q/A",
    "NT 10-K", "NT 10-Q", "S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-1/A",
    "F-3", "F-3/A", "SC 13D", "SC 13D/A", "424B1", "424B2", "424B3",
    "424B4", "424B5", "424B7",
}
CATALYST_COLUMNS = [
    "event_id", "ticker", "observed_at_utc", "source", "age_hours",
    "source_event_id", "source_timestamp_utc", "ingested_at_utc", "provider",
    "headline", "form", "items", "scheduled_for_utc", "catalyst_type",
    "catalyst_bias", "materiality", "confidence", "negative_veto",
    "review_required", "classification_reason", "catalyst_score_bonus", "source_url",
]


@dataclass(frozen=True)
class CatalystPollResult:
    events: list[MarketEvent]
    active_frame: pd.DataFrame
    health: dict[str, Any]


class CatalystAdapter(Protocol):
    def poll(self, candidates: pd.DataFrame) -> CatalystPollResult: ...


def _utc(value: Any) -> datetime | None:
    parsed = parse_utc(str(value or ""))
    return parsed.astimezone(timezone.utc) if parsed else None


def _age_hours(value: Any, now: datetime) -> float | None:
    timestamp = _utc(value)
    if timestamp is None:
        return None
    return max(0.0, (now - timestamp).total_seconds() / 3600)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _stable_event_id(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}|{value}".encode()).hexdigest()[:24]
    return f"{namespace}:{digest}"


def parse_sec_ticker_map(payload: Mapping[str, Any]) -> dict[str, str]:
    """Parse both current SEC array format and the older numbered-object format."""
    output: dict[str, str] = {}
    fields, data = payload.get("fields"), payload.get("data")
    if isinstance(fields, list) and isinstance(data, list):
        positions = {str(name): index for index, name in enumerate(fields)}
        ticker_index, cik_index = positions.get("ticker"), positions.get("cik")
        if ticker_index is not None and cik_index is not None:
            for row in data:
                if not isinstance(row, list) or max(ticker_index, cik_index) >= len(row):
                    continue
                ticker = str(row[ticker_index] or "").strip().upper().replace(".", "-")
                try:
                    cik = f"{int(row[cik_index]):010d}"
                except (TypeError, ValueError):
                    continue
                if ticker:
                    output[ticker] = cik
        return output

    if payload and all(not isinstance(value, Mapping) for value in payload.values()):
        for raw_ticker, raw_cik in payload.items():
            ticker = str(raw_ticker).strip().upper().replace(".", "-")
            try:
                cik = f"{int(raw_cik):010d}"
            except (TypeError, ValueError):
                continue
            if ticker:
                output[ticker] = cik
        return output

    for value in payload.values():
        if not isinstance(value, Mapping):
            continue
        ticker = str(value.get("ticker", "")).strip().upper().replace(".", "-")
        try:
            cik = f"{int(value.get('cik_str')):010d}"
        except (TypeError, ValueError):
            continue
        if ticker:
            output[ticker] = cik
    return output


def classify_sec_filing(form: str, items: str = "") -> dict[str, Any]:
    form = str(form or "").strip().upper()
    item_set = {item.strip() for item in str(items or "").split(",") if item.strip()}
    result = {
        "catalyst_type": "SEC_FILING",
        "catalyst_bias": "NEUTRAL",
        "materiality": "MEDIUM",
        "confidence": "MEDIUM",
        "negative_veto": False,
        "review_required": True,
        "classification_reason": f"MATERIAL_FORM_{form.replace(' ', '_')}",
        "catalyst_score_bonus": 0,
    }
    explicit_8k_risks = {
        "1.03": "BANKRUPTCY_OR_RECEIVERSHIP",
        "3.01": "DELISTING_OR_LISTING_FAILURE",
        "4.02": "NON_RELIANCE_OR_RESTATEMENT",
        "2.05": "EXIT_OR_RESTRUCTURING_COSTS",
        "2.06": "MATERIAL_IMPAIRMENT",
    }
    for item, reason in explicit_8k_risks.items():
        if item in item_set:
            return {**result, "catalyst_bias": "BEARISH", "materiality": "HIGH",
                    "confidence": "HIGH", "negative_veto": True,
                    "review_required": False, "classification_reason": reason}
    if form.startswith("424B"):
        return {**result, "catalyst_type": "SEC_OFFERING", "catalyst_bias": "BEARISH",
                "materiality": "HIGH", "confidence": "HIGH", "negative_veto": True,
                "review_required": False, "classification_reason": "PROSPECTUS_OFFERING"}
    if form in {"NT 10-K", "NT 10-Q"}:
        return {**result, "catalyst_type": "SEC_LATE_FILING", "catalyst_bias": "BEARISH",
                "materiality": "HIGH", "confidence": "HIGH", "negative_veto": True,
                "review_required": False, "classification_reason": "LATE_PERIODIC_REPORT"}
    if form.startswith(("S-1", "S-3", "F-1", "F-3")):
        return {**result, "catalyst_type": "SEC_REGISTRATION", "catalyst_bias": "RISK",
                "materiality": "HIGH", "classification_reason": "SECURITIES_REGISTRATION"}
    if "2.02" in item_set:
        return {**result, "catalyst_type": "SEC_RESULTS", "materiality": "HIGH",
                "confidence": "HIGH", "classification_reason": "RESULTS_DISCLOSURE"}
    if "1.01" in item_set:
        return {**result, "catalyst_type": "SEC_MATERIAL_AGREEMENT", "materiality": "HIGH",
                "classification_reason": "MATERIAL_AGREEMENT", "catalyst_score_bonus": 4}
    if "5.02" in item_set:
        return {**result, "catalyst_type": "SEC_LEADERSHIP", "materiality": "MEDIUM",
                "classification_reason": "LEADERSHIP_CHANGE"}
    if form.startswith("10-"):
        return {**result, "catalyst_type": "SEC_PERIODIC_REPORT", "review_required": False,
                "classification_reason": "PERIODIC_FINANCIAL_REPORT"}
    if form.startswith("SC 13D"):
        return {**result, "catalyst_type": "SEC_OWNERSHIP", "catalyst_bias": "EVENT",
                "classification_reason": "BENEFICIAL_OWNERSHIP_CHANGE", "catalyst_score_bonus": 3}
    return result


def classify_news_headline(title: str) -> tuple[int, list[str]]:
    """Let an explicit negative phrase dominate mixed earnings headlines."""
    lowered = str(title or "").lower()
    positive = [(phrase, points) for phrase, points in POSITIVE_TERMS.items() if phrase in lowered]
    negative = [(phrase, points) for phrase, points in NEGATIVE_TERMS.items() if phrase in lowered]
    if sum(points for _, points in negative) <= -10:
        return sum(points for _, points in negative), [phrase for phrase, _ in negative]
    matches = [*positive, *negative]
    return sum(points for _, points in matches), [phrase for phrase, _ in matches]


def parse_sec_submissions(
    ticker: str,
    cik: str,
    payload: Mapping[str, Any],
    now: datetime | None = None,
    lookback_hours: float = 72.0,
) -> list[MarketEvent]:
    now = now or datetime.now(timezone.utc)
    recent = payload.get("filings", {}).get("recent", {})
    if not isinstance(recent, Mapping):
        return []
    accessions = recent.get("accessionNumber", [])
    output = []
    for index, accession in enumerate(accessions if isinstance(accessions, list) else []):
        def column(name: str, default: Any = "") -> Any:
            values = recent.get(name, [])
            return values[index] if isinstance(values, list) and index < len(values) else default

        form = str(column("form")).upper()
        if form not in MATERIAL_FORMS:
            continue
        accepted = column("acceptanceDateTime") or column("filingDate")
        accepted_at = _utc(accepted)
        if accepted_at is None or _age_hours(accepted_at.isoformat(), now) > lookback_hours:
            continue
        classification = classify_sec_filing(form, str(column("items")))
        accession_clean = str(accession).replace("-", "")
        primary = str(column("primaryDocument"))
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/{primary}"
            if primary else ""
        )
        source_id = str(accession)
        event = MarketEvent(
            event_type=EventType.CATALYST,
            ticker=ticker,
            signal_id=f"{ticker}|CATALYST|SEC|{source_id}",
            observed_at_utc=accepted_at.isoformat(),
            event_id=_stable_event_id("sec", source_id),
            source="sec-edgar-submissions",
            payload={
                "source_event_id": source_id,
                "source_timestamp_utc": accepted_at.isoformat(),
                "ingested_at_utc": now.isoformat(),
                "provider": "SEC_EDGAR",
                "form": form,
                "items": str(column("items")),
                "filing_date": str(column("filingDate")),
                "report_date": str(column("reportDate")),
                "primary_document": primary,
                "source_url": filing_url,
                **classification,
            },
        )
        output.append(event)
    return output


def parse_sec_search(
    ticker: str,
    cik: str,
    payload: Mapping[str, Any],
    now: datetime | None = None,
    lookback_hours: float = 72.0,
) -> list[MarketEvent]:
    """Normalize the SEC's official full-text search response as a submissions fallback."""
    now = now or datetime.now(timezone.utc)
    hits = payload.get("hits", {}).get("hits", [])
    if not isinstance(hits, list):
        return []
    output: list[MarketEvent] = []
    seen_accessions: set[str] = set()
    for hit in hits:
        source = hit.get("_source", {}) if isinstance(hit, Mapping) else {}
        if not isinstance(source, Mapping):
            continue
        form = str(source.get("form") or source.get("file_type") or "").upper()
        accession = str(source.get("adsh") or "").strip()
        filed_at = _utc(source.get("file_date"))
        if (
            form not in MATERIAL_FORMS
            or not accession
            or accession in seen_accessions
            or filed_at is None
            or _age_hours(filed_at.isoformat(), now) > lookback_hours
        ):
            continue
        seen_accessions.add(accession)
        raw_items = source.get("items", [])
        items = ",".join(str(item) for item in raw_items) if isinstance(raw_items, list) else str(raw_items or "")
        classification = classify_sec_filing(form, items)
        accession_clean = accession.replace("-", "")
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/"
        output.append(MarketEvent(
            event_type=EventType.CATALYST,
            ticker=ticker,
            signal_id=f"{ticker}|CATALYST|SEC|{accession}",
            observed_at_utc=filed_at.isoformat(),
            event_id=_stable_event_id("sec", accession),
            source="sec-edgar-full-text-search",
            payload={
                "source_event_id": accession,
                "source_timestamp_utc": filed_at.isoformat(),
                "source_timestamp_precision": "DATE",
                "ingested_at_utc": now.isoformat(),
                "provider": "SEC_EDGAR_SEARCH",
                "form": form,
                "items": items,
                "filing_date": str(source.get("file_date") or ""),
                "report_date": str(source.get("period_ending") or ""),
                "source_url": filing_url,
                **classification,
            },
        ))
    return output


def parse_nasdaq_filings(
    ticker: str,
    payload: Mapping[str, Any],
    now: datetime | None = None,
    lookback_hours: float = 72.0,
) -> list[MarketEvent]:
    """Normalize Nasdaq's public SEC filing index without inferring missing 8-K items."""
    now = now or datetime.now(timezone.utc)
    data = payload.get("data", {})
    rows = data.get("rows", []) if isinstance(data, Mapping) else []
    if not isinstance(rows, list):
        return []
    form_aliases = {"S-1A": "S-1/A", "S-3A": "S-3/A", "F-1A": "F-1/A", "F-3A": "F-3/A"}
    output: list[MarketEvent] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw_form = str(row.get("formType") or "").strip().upper()
        form = form_aliases.get(raw_form, raw_form)
        try:
            filed_at = datetime.strptime(str(row.get("filed") or ""), "%m/%d/%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if form not in MATERIAL_FORMS or _age_hours(filed_at.isoformat(), now) > lookback_hours:
            continue
        view = row.get("view", {})
        source_url = str(view.get("htmlLink") or "") if isinstance(view, Mapping) else ""
        source_id = source_url or f"{ticker}|{form}|{filed_at.date().isoformat()}"
        classification = classify_sec_filing(form)
        output.append(MarketEvent(
            event_type=EventType.CATALYST,
            ticker=ticker,
            signal_id=f"{ticker}|CATALYST|NASDAQ_SEC|{source_id}",
            observed_at_utc=filed_at.isoformat(),
            event_id=_stable_event_id("nasdaq-sec", source_id),
            source="nasdaq-sec-filings-index",
            payload={
                "source_event_id": source_id,
                "source_timestamp_utc": filed_at.isoformat(),
                "source_timestamp_precision": "DATE",
                "ingested_at_utc": now.isoformat(),
                "provider": "NASDAQ_SEC_INDEX",
                "headline": str(row.get("companyName") or ""),
                "form": form,
                "items": "",
                "filing_date": filed_at.date().isoformat(),
                "report_date": str(row.get("period") or ""),
                "source_url": source_url,
                **classification,
            },
        ))
    return output


class SecFilingAdapter:
    def __init__(
        self,
        cache_file: Path,
        session: requests.Session | None = None,
        user_agent: str | None = None,
        timeout_seconds: float = 12.0,
        max_workers: int = 4,
        lookback_hours: float = 72.0,
        cache_hours: float = 24.0,
        requests_per_second: float = 8.0,
        max_retries: int = 2,
    ):
        self.cache_file = Path(cache_file)
        self.session = session or requests.Session()
        self.user_agent = (user_agent or os.getenv("SEC_USER_AGENT") or DEFAULT_SEC_USER_AGENT).strip()
        self.timeout_seconds = timeout_seconds
        self.max_workers = max(1, min(int(max_workers), 5))
        self.lookback_hours = lookback_hours
        self.cache_hours = cache_hours
        self.request_interval = 1 / max(0.1, min(float(requests_per_second), 8.0))
        self.max_retries = max(0, min(int(max_retries), 3))
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self.ticker_map_source = ""

    def _get_json(self, url: str) -> Mapping[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            with self._request_lock:
                wait = self.request_interval - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
                self._last_request_at = time.monotonic()
            try:
                response = self.session.get(
                    url,
                    headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if attempt >= self.max_retries or status not in {429, 500, 502, 503, 504}:
                    raise
                time.sleep(min(2 ** attempt, 2))
        raise last_error or RuntimeError("SEC request failed")

    def _ticker_map(self, now: datetime) -> dict[str, str]:
        stale: dict[str, str] = {}
        if self.cache_file.exists():
            try:
                cached = json.loads(self.cache_file.read_text())
                stale = {str(key): str(value) for key, value in cached.get("tickers", {}).items()}
                age = _age_hours(cached.get("fetched_at_utc"), now)
                if age is not None and age <= self.cache_hours:
                    self.ticker_map_source = str(cached.get("source", "CACHE"))
                    return stale
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        try:
            tickers = parse_sec_ticker_map(self._get_json(SEC_TICKERS_URL))
            source = "SEC_OFFICIAL"
        except Exception:
            try:
                tickers = parse_sec_ticker_map(self._get_json(SEC_TICKERS_FALLBACK_URL))
                source = "SEC_CIK_MAPPER_GITHUB"
            except Exception:
                if stale:
                    self.ticker_map_source = "STALE_CACHE"
                    return stale
                raise
        if not tickers:
            if stale:
                self.ticker_map_source = "STALE_CACHE"
                return stale
            raise ValueError("SEC ticker map was empty")
        self.ticker_map_source = source
        _atomic_json(self.cache_file, {
            "fetched_at_utc": now.isoformat(), "source": source, "tickers": tickers,
        })
        return tickers

    def poll(self, candidates: pd.DataFrame, now: datetime | None = None) -> tuple[list[MarketEvent], dict[str, Any]]:
        started = time.monotonic()
        now = now or datetime.now(timezone.utc)
        symbols = [] if candidates is None or candidates.empty else list(dict.fromkeys(
            candidates["ticker"].astype(str).str.upper().str.replace(".", "-", regex=False)
        ))
        errors = 0
        events: list[MarketEvent] = []
        ticker_map = self._ticker_map(now)
        resolved = [(ticker, ticker_map.get(ticker)) for ticker in symbols]
        unresolved = sum(1 for _, cik in resolved if not cik)

        def fetch(item: tuple[str, str]) -> tuple[list[MarketEvent], str]:
            ticker, cik = item
            try:
                payload = self._get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
                if not isinstance(payload.get("filings", {}).get("recent"), Mapping):
                    raise ValueError("SEC submissions response was missing filings.recent")
                return parse_sec_submissions(ticker, cik, payload, now, self.lookback_hours), "SUBMISSIONS"
            except Exception:
                try:
                    start = (now - timedelta(hours=self.lookback_hours)).date().isoformat()
                    query = urlencode({
                        "ciks": cik,
                        "forms": ",".join(sorted(MATERIAL_FORMS)),
                        "dateRange": "custom",
                        "startdt": start,
                        "enddt": now.date().isoformat(),
                        "from": 0,
                        "size": 100,
                    })
                    payload = self._get_json(f"{SEC_SEARCH_URL}?{query}")
                    if not isinstance(payload.get("hits", {}).get("hits"), list):
                        raise ValueError("SEC search response was missing hits.hits")
                    return parse_sec_search(ticker, cik, payload, now, self.lookback_hours), "SEC_SEARCH"
                except Exception:
                    payload = self._get_json(NASDAQ_FILINGS_URL.format(ticker=ticker))
                    data = payload.get("data")
                    if not isinstance(data, Mapping) or not isinstance(data.get("rows"), list):
                        raise ValueError("Nasdaq filing response was missing data.rows")
                    return parse_nasdaq_filings(ticker, payload, now, self.lookback_hours), "NASDAQ_INDEX"

        valid = [(ticker, cik) for ticker, cik in resolved if cik]
        search_fallbacks = 0
        nasdaq_fallbacks = 0
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(valid)) or 1) as pool:
            futures = {pool.submit(fetch, item): item[0] for item in valid}
            for future in as_completed(futures):
                try:
                    fetched, provider_path = future.result()
                    events.extend(fetched)
                    search_fallbacks += int(provider_path == "SEC_SEARCH")
                    nasdaq_fallbacks += int(provider_path == "NASDAQ_INDEX")
                except Exception:
                    errors += 1
        health = {
            "provider": "SEC_EDGAR",
            "ticker_map_source": self.ticker_map_source,
            "requested": len(symbols),
            "resolved": len(valid),
            "unresolved": unresolved,
            "submissions_errors": search_fallbacks + nasdaq_fallbacks + errors,
            "search_fallbacks": search_fallbacks,
            "search_errors": nasdaq_fallbacks + errors,
            "nasdaq_fallbacks": nasdaq_fallbacks,
            "errors": errors,
            "events": len(events),
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
        return events, health


class YahooNewsCatalystAdapter:
    def __init__(self, max_workers: int = 4, max_tickers: int = 20, lookback_hours: float = 72.0):
        self.max_workers = max(1, min(int(max_workers), 8))
        self.max_tickers = max(0, int(max_tickers))
        self.lookback_hours = lookback_hours

    @staticmethod
    def _fetch_news(ticker: str) -> Iterable[Mapping[str, Any]]:
        obj = yf.Ticker(ticker)
        try:
            return obj.get_news(count=12)
        except TypeError:
            return obj.news

    def _ticker_events(self, ticker: str, company_name: str, now: datetime) -> list[MarketEvent]:
        output = []
        for raw in self._fetch_news(ticker) or []:
            item = _extract_news_item(raw)
            if not item or item.get("published") is None:
                continue
            relevant, relevance_reason = _news_relevance(ticker, company_name, item)
            age = _age_hours(item["published"].isoformat(), now)
            if not relevant or age is None or age > self.lookback_hours:
                continue
            score, terms = classify_news_headline(item["title"])
            if abs(score) < 8:
                continue
            bias = "BULLISH" if score > 0 else "BEARISH"
            event = MarketEvent(
                event_type=EventType.CATALYST,
                ticker=ticker,
                signal_id=f"{ticker}|CATALYST|NEWS|{item['published'].isoformat()}|{item['title'][:80]}",
                observed_at_utc=item["published"].isoformat(),
                event_id=_stable_event_id(
                    "news",
                    f"{ticker}|{item['published'].isoformat()}|{item.get('url', '')}|{item['title']}",
                ),
                source="yahoo-finance-news",
                payload={
                    "source_event_id": item.get("url") or f"{item['published'].isoformat()}|{item['title']}",
                    "source_timestamp_utc": item["published"].isoformat(),
                    "ingested_at_utc": now.isoformat(),
                    "provider": item.get("provider") or "YAHOO_FINANCE",
                    "headline": item["title"][:220],
                    "source_url": item.get("url", ""),
                    "matched_terms": terms,
                    "headline_signal": score,
                    "catalyst_type": "NEWS",
                    "catalyst_bias": bias,
                    "materiality": "HIGH" if abs(score) >= 14 else "MEDIUM",
                    "confidence": "MEDIUM",
                    "negative_veto": score <= -10,
                    "review_required": True,
                    "classification_reason": relevance_reason,
                    "catalyst_score_bonus": min(20, score) if score > 0 else 0,
                },
            )
            output.append(event)
        return output

    def poll(self, candidates: pd.DataFrame, now: datetime | None = None) -> tuple[list[MarketEvent], dict[str, Any]]:
        started = time.monotonic()
        now = now or datetime.now(timezone.utc)
        if candidates is None or candidates.empty or self.max_tickers == 0:
            return [], {"provider": "YAHOO_NEWS", "requested": 0, "errors": 0, "events": 0, "duration_ms": 0}
        subset = candidates.head(self.max_tickers)
        name_column = "company_name" if "company_name" in subset else ("name" if "name" in subset else None)
        inputs = [
            (str(row["ticker"]), str(row.get(name_column, "")) if name_column else "")
            for _, row in subset.iterrows()
        ]
        events: list[MarketEvent] = []
        errors = 0
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(inputs)) or 1) as pool:
            futures = {pool.submit(self._ticker_events, ticker, name, now): ticker for ticker, name in inputs}
            for future in as_completed(futures):
                try:
                    events.extend(future.result())
                except Exception:
                    errors += 1
        return events, {
            "provider": "YAHOO_NEWS", "requested": len(inputs), "errors": errors,
            "events": len(events), "duration_ms": round((time.monotonic() - started) * 1000),
        }


class LocalEventCalendarAdapter:
    """Normalizes the broad event-first earnings calendar into V4 events."""

    def __init__(self, path: Path, horizon_days: float = 3.25):
        self.path = Path(path)
        self.horizon_days = horizon_days

    def poll(self, candidates: pd.DataFrame, now: datetime | None = None) -> tuple[list[MarketEvent], dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        if not self.path.exists() or candidates is None or candidates.empty:
            return [], {"provider": "EVENT_CALENDAR", "requested": 0, "errors": 0,
                        "events": 0, "status": "NOT_AVAILABLE"}
        frame = pd.read_csv(self.path)
        if frame.empty or "ticker" not in frame or "event_date_utc" not in frame:
            return [], {"provider": "EVENT_CALENDAR", "requested": 0, "errors": 0,
                        "events": 0, "status": "EMPTY"}
        symbols = set(candidates["ticker"].astype(str).str.upper())
        output = []
        for _, row in frame.iterrows():
            ticker = str(row.get("ticker", "")).upper()
            if ticker not in symbols or str(row.get("event_type", "")).upper() != "EARNINGS":
                continue
            scheduled = _utc(row.get("event_date_utc"))
            if scheduled is None:
                continue
            days = (scheduled - now).total_seconds() / 86_400
            if not (-0.5 <= days <= self.horizon_days):
                continue
            source_id = f"{ticker}|EARNINGS|{scheduled.isoformat()}"
            output.append(MarketEvent(
                event_type=EventType.CATALYST,
                ticker=ticker,
                signal_id=f"{ticker}|CATALYST|CALENDAR|{scheduled.date().isoformat()}",
                observed_at_utc=now.isoformat(),
                event_id=source_id,
                source="market-hunt-event-calendar",
                payload={
                    "source_event_id": source_id,
                    "source_timestamp_utc": "",
                    "scheduled_for_utc": scheduled.isoformat(),
                    "ingested_at_utc": now.isoformat(),
                    "provider": str(row.get("event_source", "EVENT_CALENDAR")),
                    "catalyst_type": "UPCOMING_EARNINGS",
                    "catalyst_bias": "EVENT",
                    "materiality": "HIGH",
                    "confidence": "HIGH",
                    "negative_veto": False,
                    "review_required": False,
                    "classification_reason": "EARNINGS_WITHIN_72H",
                    "catalyst_score_bonus": 10,
                    "days_to_event": round(days, 2),
                },
            ))
        return output, {"provider": "EVENT_CALENDAR", "requested": len(symbols), "errors": 0,
                        "events": len(output), "status": "OK"}


def events_frame(events: Iterable[MarketEvent], now: datetime | None = None, lookback_hours: float = 72.0) -> pd.DataFrame:
    now = now or datetime.now(timezone.utc)
    rows = []
    for event in events:
        age = _age_hours(event.observed_at_utc, now)
        if age is None or age > lookback_hours:
            continue
        rows.append({
            "event_id": event.event_id,
            "ticker": event.ticker,
            "observed_at_utc": event.observed_at_utc,
            "source": event.source,
            "age_hours": round(age, 2),
            **dict(event.payload),
        })
    if not rows:
        return pd.DataFrame(columns=CATALYST_COLUMNS)
    return pd.DataFrame(rows).sort_values(["observed_at_utc", "ticker"], ascending=[False, True])


def load_recent_catalyst_events(
    path: Path,
    now: datetime | None = None,
    lookback_hours: float = 72.0,
) -> list[MarketEvent]:
    now = now or datetime.now(timezone.utc)
    output = []
    if not Path(path).exists():
        return output
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = MarketEvent.from_dict(json.loads(line))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        age = _age_hours(event.observed_at_utc, now)
        if event.event_type is EventType.CATALYST and age is not None and age <= lookback_hours:
            output.append(event)
    return output


def apply_catalyst_evidence(candidates: pd.DataFrame, active: pd.DataFrame) -> pd.DataFrame:
    output = candidates.copy()
    if output.empty:
        return output
    output["v4_catalyst_event_count"] = 0
    output["v4_catalyst_latest_at_utc"] = ""
    output["v4_catalyst_bias"] = ""
    output["v4_catalyst_reason"] = ""
    if active is None or active.empty:
        return output
    for ticker, group in active.groupby("ticker"):
        mask = output["ticker"].astype(str).str.upper().eq(str(ticker).upper())
        if not mask.any():
            continue
        ordered = group.sort_values("observed_at_utc", ascending=False)
        latest = ordered.iloc[0]
        veto = ordered.get("negative_veto", pd.Series(False, index=ordered.index)).fillna(False).astype(bool).any()
        bonus = pd.to_numeric(ordered.get("catalyst_score_bonus", 0), errors="coerce").fillna(0).max()
        existing_risk = output.loc[mask, "negative_catalyst_risk"].fillna(False).astype(bool) if "negative_catalyst_risk" in output else False
        output.loc[mask, "negative_catalyst_risk"] = existing_risk | veto
        if "catalyst_score" in output:
            current = pd.to_numeric(output.loc[mask, "catalyst_score"], errors="coerce").fillna(0)
            output.loc[mask, "catalyst_score"] = (current + max(0.0, float(bonus))).clip(upper=100)
        output.loc[mask, "v4_catalyst_event_count"] = len(ordered)
        output.loc[mask, "v4_catalyst_latest_at_utc"] = str(latest.get("observed_at_utc", ""))
        output.loc[mask, "v4_catalyst_bias"] = "BEARISH" if veto else str(latest.get("catalyst_bias", ""))
        output.loc[mask, "v4_catalyst_reason"] = str(latest.get("classification_reason", ""))
    return output


class CompositeCatalystAdapter:
    def __init__(self, adapters: list[Any]):
        self.adapters = adapters

    def poll(self, candidates: pd.DataFrame) -> CatalystPollResult:
        now = datetime.now(timezone.utc)
        events: list[MarketEvent] = []
        providers = []
        for adapter in self.adapters:
            try:
                provider_events, health = adapter.poll(candidates, now=now)
                events.extend(provider_events)
                providers.append(health)
            except Exception as exc:
                providers.append({"provider": type(adapter).__name__, "requested": len(candidates),
                                  "errors": len(candidates), "events": 0,
                                  "detail": f"{type(exc).__name__}"})
        unique = {event.event_id: event for event in events}
        active = events_frame(unique.values(), now=now)
        source_times = [_utc(event.observed_at_utc) for event in unique.values()]
        lags = [(now - value).total_seconds() for value in source_times if value is not None]
        health = {
            "checked_at_utc": now.isoformat(),
            "status": "DEGRADED" if any(int(item.get("errors", 0)) for item in providers) else "OK",
            "events": len(unique),
            "negative_veto_events": int(active.get("negative_veto", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not active.empty else 0,
            "review_required_events": int(active.get("review_required", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if not active.empty else 0,
            "source_lag_p95_seconds": round(float(pd.Series(lags).quantile(0.95)), 1) if lags else None,
            "providers": providers,
        }
        return CatalystPollResult(list(unique.values()), active, health)
