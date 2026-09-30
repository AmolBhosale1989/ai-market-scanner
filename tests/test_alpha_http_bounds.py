from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from contextlib import nullcontext, contextmanager

import pytest
import requests

from scanner import catalyst_data_plane as plane
from scanner.catalyst_adapters import AlphaVantageCalendarBatch
from scanner import catalyst_ingestion as ingestion
from scanner.v4.catalysts import SecFilingAdapter


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv('ALPHA_VANTAGE_API_KEY', 'test-private-key')
    get = Mock()
    sleep = Mock()
    monkeypatch.setattr(plane.requests, 'get', get)
    monkeypatch.setattr(plane.time, 'sleep', sleep)
    return get, sleep


@pytest.mark.parametrize('error', [requests.ConnectTimeout, requests.ReadTimeout])
def test_timeout_attempts_are_bounded_and_key_is_not_logged(http, error, capsys):
    get, sleep = http
    get.side_effect = error('url?apikey=test-private-key')
    with pytest.raises(RuntimeError, match='ALPHA_VANTAGE_HTTP_FAILED') as exc:
        plane._alpha_calendar_fetch()
    assert get.call_count == 2
    assert all(c.kwargs['timeout'] == (3.0, 5.0) for c in get.call_args_list)
    assert all(c.kwargs['allow_redirects'] is False for c in get.call_args_list)
    sleep.assert_called_once_with(1.0)
    assert 'test-private-key' not in str(exc.value) + capsys.readouterr().out


@pytest.mark.parametrize('status,attempts', [(429,1),(403,1),(503,2)])
def test_http_failure_has_capped_backoff(http, status, attempts):
    get, sleep = http
    response = Mock(status_code=status, headers={'Retry-After': '86400'})
    response.raise_for_status.side_effect = requests.HTTPError('secret URL', response=response)
    get.return_value = response
    with pytest.raises(RuntimeError, match=f'status={status}'):
        plane._alpha_calendar_fetch()
    assert get.call_count == attempts
    assert sleep.call_count == attempts-1
    assert response.close.call_count == attempts


def test_transient_failure_then_calendar_success(http):
    get, sleep = http
    response = Mock(status_code=200, text='symbol,reportDate\nAAA,2026-09-29\n')
    get.side_effect = [requests.ConnectTimeout(), response]
    assert plane._alpha_calendar_fetch() == response.text
    response.close.assert_called_once()


def test_soft_rate_limit_does_not_write_no_event(http, monkeypatch):
    get, sleep = http
    get.return_value = Mock(status_code=200, text='{"Information":"rate limit"}')
    start = Mock()
    monkeypatch.setattr(ingestion, 'start_run', start)
    with pytest.raises(RuntimeError, match='SOFT_ERROR_RESPONSE'):
        ingestion.ingest_alpha_vantage_batch(AlphaVantageCalendarBatch(plane._alpha_calendar_fetch),
            ['AAA'], anchor=datetime.now(timezone.utc), lookforward=timedelta(days=14))
    start.assert_not_called()
    sleep.assert_not_called()
    assert get.call_count == 1


def test_sec_rate_limit_does_not_sleep_or_retry():
    response = Mock(status_code=429)
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    session = Mock()
    session.get.return_value = response
    adapter = SecFilingAdapter(session=session, max_retries=99)
    with pytest.raises(requests.HTTPError):
        adapter._get_json('https://data.sec.gov/submissions/test')
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs['timeout'] == (3.0,5.0)


def test_persistence_progress_only_follows_completed_writes(monkeypatch, capsys):
    adapter = Mock(provider_name='ALPHA_VANTAGE')
    adapter.fetch_and_index.return_value = {}
    monkeypatch.setattr(ingestion, 'bounded_persistence', lambda: nullcontext(Mock()))
    from scanner import catalyst_bulk
    write = Mock(side_effect=RuntimeError('DB stalled'))
    monkeypatch.setattr(catalyst_bulk, 'persist_alpha', write)
    with pytest.raises(RuntimeError, match='DB stalled'):
        ingestion.ingest_alpha_vantage_batch(adapter,['AAA','BBB'],
            anchor=datetime.now(timezone.utc),lookforward=timedelta(days=14))
    output = capsys.readouterr().out
    assert 'CATALYST_FETCH_COMPLETE provider=ALPHA_VANTAGE' in output
    write.assert_called_once()
    assert 'committed=' not in output
    assert 'CATALYST_PERSIST_COMPLETE' not in output


def test_persistence_complete_waits_for_commit(monkeypatch, capsys):
    from scanner import catalyst_bulk
    adapter = Mock(provider_name='ALPHA_VANTAGE')
    adapter.fetch_and_index.return_value = {}

    @contextmanager
    def failed_commit():
        yield Mock()
        raise RuntimeError('commit uncertain')

    monkeypatch.setattr(ingestion, 'bounded_persistence', failed_commit)
    monkeypatch.setattr(catalyst_bulk, 'persist_alpha', Mock(return_value=[{}, {}]))
    with pytest.raises(RuntimeError, match='commit uncertain'):
        ingestion.ingest_alpha_vantage_batch(adapter, ['AAA', 'BBB'],
            anchor=datetime.now(timezone.utc), lookforward=timedelta(days=14))
    output = capsys.readouterr().out
    assert 'CATALYST_PERSIST_COMPLETE' not in output
    assert 'committed=' not in output
