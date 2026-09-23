import pytest

from scanner.dashboard_freshness import publication_expiry_reason


def manifest(at):
    return {"status": "PUBLISHED", "warehouse_as_of_utc": at, "published_at_utc": at}


@pytest.mark.parametrize("at,now", [
    ("2026-09-23T15:00Z", "2026-09-23T15:10Z"),
    ("2026-09-23T20:05Z", "2026-09-24T10:00Z"),
    ("2026-09-25T20:05Z", "2026-09-27T10:00Z"),
    ("2026-11-27T18:05Z", "2026-11-28T10:00Z"),
])
def test_current_publication_and_completed_session_pass(at, now):
    assert publication_expiry_reason(manifest(at), now) == ""


@pytest.mark.parametrize("at,now", [
    ("2026-09-23T15:00Z", "2026-09-23T15:16Z"),
    ("2026-09-22T20:05Z", "2026-09-23T13:31Z"),
    ("2026-09-23T19:59Z", "2026-09-23T20:01Z"),
    ("2026-09-23T20:05Z", "2026-09-24T13:31Z"),
    ("2026-09-23T20:05Z", "2026-09-23T19:00Z"),
    ("2026-09-23T20:05", "2026-09-24T10:00Z"),
    (None, "2026-09-24T10:00Z"),
])
def test_stale_future_and_missing_publication_are_blocked(at, now):
    assert publication_expiry_reason(manifest(at), now)


def test_missing_publication_is_blocked():
    assert publication_expiry_reason({})


def test_dashboard_stops_before_rendering_signals_when_expired():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert source.index("publication_expiry_reason(production_manifest)") < source.index("st.stop()") < source.index("st.tabs(")
