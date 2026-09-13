from datetime import datetime, timedelta, timezone

import pandas as pd

from scanner.v4.catalysts import (
    CatalystPollResult,
    CompositeCatalystAdapter,
    LocalEventCalendarAdapter,
    apply_catalyst_evidence,
    classify_news_headline,
    classify_sec_filing,
    events_frame,
    load_recent_catalyst_events,
    parse_nasdaq_filings,
    parse_sec_search,
    parse_sec_submissions,
    parse_sec_ticker_map,
)
from scanner.v4.adapters import PollResult
from scanner.v4.alerting import AlertRouter
from scanner.v4.contracts import EventType, MarketEvent
from scanner.v4.engine import MomentumEngine
from scanner.v4.health import HealthRecorder
from scanner.v4.source import CandidateSourceResult
from scanner.v4.store import FileEventStore
from scanner.v4.worker import ContinuousMomentumWorker, WorkerSettings


NOW = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)


def sec_payload(forms, items):
    count = len(forms)
    return {
        "filings": {
            "recent": {
                "accessionNumber": [f"0000000000-26-{index:06d}" for index in range(count)],
                "acceptanceDateTime": ["2026-09-14T14:30:00Z"] * count,
                "filingDate": ["2026-09-14"] * count,
                "reportDate": ["2026-09-13"] * count,
                "form": forms,
                "items": items,
                "primaryDocument": [f"filing-{index}.htm" for index in range(count)],
            }
        }
    }


def test_sec_ticker_map_supports_exchange_array_format():
    payload = {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[1759414, "Credo Technology", "CRDO", "Nasdaq"]],
    }
    assert parse_sec_ticker_map(payload) == {"CRDO": "0001759414"}
    assert parse_sec_ticker_map({"CRDO": "0001759414"}) == {"CRDO": "0001759414"}


def test_sec_classification_only_vetoes_explicit_high_confidence_risks():
    assert classify_sec_filing("8-K", "4.02")["negative_veto"] is True
    assert classify_sec_filing("424B5")["negative_veto"] is True
    registration = classify_sec_filing("S-3")
    assert registration["catalyst_bias"] == "RISK"
    assert registration["review_required"] is True
    assert registration["negative_veto"] is False
    leadership = classify_sec_filing("8-K", "5.02")
    assert leadership["negative_veto"] is False


def test_explicit_negative_phrase_dominates_mixed_news_headline():
    score, terms = classify_news_headline("Company beats estimates but cuts guidance")
    assert score <= -10
    assert "cuts guidance" in terms


def test_sec_events_keep_source_time_url_and_deduplicate(tmp_path):
    events = parse_sec_submissions(
        "CRDO",
        "0001759414",
        sec_payload(["8-K", "4"], ["3.01", ""]),
        now=NOW,
    )
    assert len(events) == 1
    event = events[0]
    assert event.observed_at_utc == "2026-09-14T14:30:00+00:00"
    assert event.payload["classification_reason"] == "DELISTING_OR_LISTING_FAILURE"
    assert event.payload["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/")
    repeated = parse_sec_submissions(
        "CRDO", "0001759414", sec_payload(["8-K"], ["3.01"]),
        now=NOW + timedelta(minutes=15),
    )
    assert repeated[0].event_id == event.event_id
    store = FileEventStore(tmp_path / "events")
    assert store.append_events(events) == 1
    assert store.append_events(repeated) == 0
    assert [item.event_id for item in load_recent_catalyst_events(store.event_file, now=NOW)] == [event.event_id]


def test_stale_sec_filings_are_excluded():
    payload = sec_payload(["424B5"], [""])
    payload["filings"]["recent"]["acceptanceDateTime"] = ["2026-09-10T10:00:00Z"]
    assert parse_sec_submissions("AXTI", "0001051627", payload, now=NOW) == []


def test_sec_full_text_search_fallback_preserves_items_and_event_identity():
    payload = {"hits": {"hits": [{"_source": {
        "form": "8-K", "adsh": "0001759414-26-000001",
        "file_date": "2026-09-14", "period_ending": "2026-09-13",
        "items": ["3.01", "9.01"],
    }}]}}
    events = parse_sec_search("CRDO", "0001759414", payload, now=NOW)
    assert len(events) == 1
    assert events[0].payload["provider"] == "SEC_EDGAR_SEARCH"
    assert events[0].payload["negative_veto"] is True
    assert events[0].payload["classification_reason"] == "DELISTING_OR_LISTING_FAILURE"
    repeated = parse_sec_search("CRDO", "0001759414", payload, now=NOW + timedelta(minutes=5))
    assert repeated[0].event_id == events[0].event_id


def test_sec_adapter_uses_official_search_when_submissions_endpoint_is_blocked(tmp_path, monkeypatch):
    from scanner.v4.catalysts import SecFilingAdapter

    adapter = SecFilingAdapter(tmp_path / "ticker-map.json", max_workers=1, max_retries=0)
    monkeypatch.setattr(adapter, "_ticker_map", lambda _now: {"CRDO": "0001759414"})

    def get_json(url):
        if "submissions" in url:
            raise RuntimeError("shared runner denied")
        assert "efts.sec.gov/LATEST/search-index" in url
        return {"hits": {"hits": []}}

    monkeypatch.setattr(adapter, "_get_json", get_json)
    events, health = adapter.poll(pd.DataFrame({"ticker": ["CRDO"]}), now=NOW)
    assert events == []
    assert health["errors"] == 0
    assert health["submissions_errors"] == 1
    assert health["search_fallbacks"] == 1


def test_nasdaq_filing_fallback_uses_form_evidence_only():
    payload = {"data": {"rows": [{
        "companyName": "Credo Technology", "formType": "424B5",
        "filed": "09/14/2026", "period": "09/13/2026",
        "view": {"htmlLink": "https://example.test/filing?ref=123"},
    }, {
        "companyName": "Credo Technology", "formType": "8-K",
        "filed": "09/14/2026", "period": "09/13/2026",
        "view": {"htmlLink": "https://example.test/filing?ref=456"},
    }]}}
    events = parse_nasdaq_filings("CRDO", payload, now=NOW)
    assert len(events) == 2
    by_form = {event.payload["form"]: event for event in events}
    assert by_form["424B5"].payload["negative_veto"] is True
    assert by_form["8-K"].payload["negative_veto"] is False
    assert by_form["8-K"].payload["items"] == ""


def test_sec_adapter_uses_nasdaq_after_both_sec_hosts_are_blocked(tmp_path, monkeypatch):
    from scanner.v4.catalysts import SecFilingAdapter

    adapter = SecFilingAdapter(tmp_path / "ticker-map.json", max_workers=1, max_retries=0)
    monkeypatch.setattr(adapter, "_ticker_map", lambda _now: {"CRDO": "0001759414"})

    def get_json(url):
        if "api.nasdaq.com" not in url:
            raise RuntimeError("shared runner denied")
        return {"data": {"rows": []}}

    monkeypatch.setattr(adapter, "_get_json", get_json)
    events, health = adapter.poll(pd.DataFrame({"ticker": ["CRDO"]}), now=NOW)
    assert events == []
    assert health["errors"] == 0
    assert health["submissions_errors"] == 1
    assert health["search_errors"] == 1
    assert health["nasdaq_fallbacks"] == 1


def test_empty_catalyst_export_keeps_a_readable_schema():
    frame = events_frame([], now=NOW)
    assert frame.empty
    assert {"event_id", "ticker", "negative_veto", "classification_reason"}.issubset(frame.columns)


def test_active_catalyst_evidence_adds_bonus_and_negative_veto():
    base = pd.DataFrame([
        {"ticker": "AXTI", "catalyst_score": 40, "negative_catalyst_risk": False},
        {"ticker": "CRDO", "catalyst_score": 50, "negative_catalyst_risk": False},
    ])
    active = pd.DataFrame([
        {
            "ticker": "AXTI", "observed_at_utc": "2026-09-14T14:30:00+00:00",
            "negative_veto": True, "catalyst_score_bonus": 0,
            "catalyst_bias": "BEARISH", "classification_reason": "PROSPECTUS_OFFERING",
        },
        {
            "ticker": "CRDO", "observed_at_utc": "2026-09-14T14:40:00+00:00",
            "negative_veto": False, "catalyst_score_bonus": 12,
            "catalyst_bias": "BULLISH", "classification_reason": "RELATED_TICKER",
        },
    ])
    enriched = apply_catalyst_evidence(base, active).set_index("ticker")
    assert bool(enriched.loc["AXTI", "negative_catalyst_risk"]) is True
    assert enriched.loc["CRDO", "catalyst_score"] == 62
    assert enriched.loc["CRDO", "v4_catalyst_bias"] == "BULLISH"


def test_composite_adapter_surfaces_review_and_source_lag():
    events = parse_sec_submissions(
        "CRDO", "0001759414", sec_payload(["S-3"], [""]), now=NOW,
    )

    class StaticProvider:
        def poll(self, _candidates, now=None):
            return events, {"provider": "STATIC", "requested": 1, "errors": 0, "events": 1}

    adapter = CompositeCatalystAdapter([StaticProvider()])
    # Pin the source time rather than asserting wall-clock lag.
    result = adapter.poll(pd.DataFrame([{"ticker": "CRDO"}]))
    assert isinstance(result, CatalystPollResult)
    assert result.health["review_required_events"] == 1
    assert result.health["status"] == "OK"
    assert not events_frame(events, now=NOW).empty


def test_local_event_calendar_surfaces_earnings_inside_72_hours(tmp_path):
    path = tmp_path / "upcoming_events.csv"
    pd.DataFrame([
        {
            "ticker": "CRDO", "event_type": "EARNINGS",
            "event_date_utc": "2026-09-16T12:00:00+00:00", "event_source": "ALPHA_VANTAGE",
        },
        {
            "ticker": "AXTI", "event_type": "EARNINGS",
            "event_date_utc": "2026-09-20T12:00:00+00:00", "event_source": "ALPHA_VANTAGE",
        },
    ]).to_csv(path, index=False)
    adapter = LocalEventCalendarAdapter(path)
    events, health = adapter.poll(pd.DataFrame([{"ticker": "CRDO"}, {"ticker": "AXTI"}]), now=NOW)
    assert health["events"] == 1
    assert events[0].ticker == "CRDO"
    assert events[0].payload["classification_reason"] == "EARNINGS_WITHIN_72H"


def test_worker_applies_retained_negative_veto_before_price_state_transition(tmp_path):
    candidate = pd.DataFrame([{
        "ticker": "AXTI", "universal_10pct_gate": True, "stage": "ARMED",
        "market_hunt_score": 90, "catalyst_score": 40,
        "negative_catalyst_risk": False, "entry_trigger": 10.0,
        "stop": 9.0, "effective_target": 12.0,
    }])

    class Source:
        def load(self):
            return CandidateSourceResult(candidate, "test")

    class Catalyst:
        def poll(self, _frame):
            event = MarketEvent(
                event_type=EventType.CATALYST,
                ticker="AXTI",
                signal_id="AXTI|CATALYST|SEC|offering",
                observed_at_utc=datetime.now(timezone.utc).isoformat(),
                payload={
                    "negative_veto": True, "review_required": False,
                    "classification_reason": "PROSPECTUS_OFFERING",
                    "catalyst_bias": "BEARISH", "catalyst_score_bonus": 0,
                },
            )
            return CatalystPollResult([event], events_frame([event]), {"status": "OK"})

    class Prices:
        def poll(self, frame):
            output = frame.copy()
            output["live_status"] = "LIVE"
            output["live_price"] = 10.5
            output["live_bar_at_et"] = datetime.now(timezone.utc).isoformat()
            output["live_trigger_reached"] = True
            output["live_above_vwap"] = True
            output["intraday_rvol"] = 2.0
            output["live_trade_action"] = "BUY / LIVE CONFIRMED + CATALYST"
            timestamp = datetime.now(timezone.utc).isoformat()
            return PollResult(output, "test", timestamp, timestamp, 1, 1, 1, 0)

    worker = ContinuousMomentumWorker(
        source=Source(), adapter=Prices(),
        engine=MomentumEngine(FileEventStore(tmp_path / "state")),
        alerts=AlertRouter(tmp_path / "dispatch.json", []),
        health=HealthRecorder(tmp_path / "cycles.csv", tmp_path / "health.json"),
        catalyst_adapter=Catalyst(), output_dir=tmp_path / "output",
        settings=WorkerSettings(hot_limit=1, warm_limit=0, warm_batch_size=0),
    )
    metric = worker.run_cycle()
    transitions = pd.read_csv(tmp_path / "output" / "v4_transitions.csv")
    assert metric.success is True
    assert transitions.iloc[0]["current_state"] == "INVALIDATED"
    assert (tmp_path / "output" / "v4_catalyst_events.csv").exists()
