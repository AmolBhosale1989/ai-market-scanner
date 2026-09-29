from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from scanner.v4.catalysts import SecFilingAdapter, YahooNewsCatalystAdapter


@pytest.mark.parametrize('error', [requests.ConnectTimeout, requests.ReadTimeout])
def test_yahoo_socket_timeout_and_two_attempt_cap(monkeypatch, error):
    post = Mock(side_effect=error('stalled'))
    monkeypatch.setattr('scanner.v4.catalysts.requests.post', post)
    sleep = Mock()
    monkeypatch.setattr('scanner.v4.catalysts.time.sleep', sleep)
    adapter = YahooNewsCatalystAdapter(max_workers=100)
    events, health = adapter.poll(pd.DataFrame({'ticker': ['AAA']}))
    assert adapter.max_workers == 8
    assert post.call_count == 2
    assert all(c.kwargs['timeout'] == (3.0, 5.0) for c in post.call_args_list)
    assert all(c.kwargs['allow_redirects'] is False for c in post.call_args_list)
    assert sleep.call_args_list == [((1.0,), {})]
    assert events == []
    assert health['failed_tickers'] == ['AAA']
    assert health['successful_checks'] == []


def test_yahoo_429_does_not_honor_unbounded_retry_after(monkeypatch):
    response = Mock(status_code=429, headers={'Retry-After': '86400'})
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    post = Mock(return_value=response)
    monkeypatch.setattr('scanner.v4.catalysts.requests.post', post)
    sleep = Mock()
    monkeypatch.setattr('scanner.v4.catalysts.time.sleep', sleep)
    with pytest.raises(requests.HTTPError):
        YahooNewsCatalystAdapter._fetch_news('AAA')
    assert post.call_count == 2
    sleep.assert_called_once_with(1.0)


@pytest.mark.parametrize('payload', [{}, {'data': {'tickerStream': {'stream': None}}}])
def test_yahoo_invalid_payload_is_not_no_event(monkeypatch, payload):
    response = Mock()
    response.json.return_value = payload
    monkeypatch.setattr('scanner.v4.catalysts.requests.post', Mock(return_value=response))
    with pytest.raises(ValueError, match='tickerStream'):
        YahooNewsCatalystAdapter._fetch_news('AAA')


def test_yahoo_valid_response_preserves_news_and_filters_ads(monkeypatch):
    item = {'content': {'title': 'Company wins contract', 'pubDate': '2026-09-28T00:00:00Z'}}
    response = Mock()
    response.json.return_value = {'data': {'tickerStream': {'stream': [item, {'ad': True}]}}}
    post = Mock(return_value=response)
    monkeypatch.setattr('scanner.v4.catalysts.requests.post', post)
    assert YahooNewsCatalystAdapter._fetch_news('AAA') == [item]
    assert post.call_args.kwargs['json']['serviceConfig']['s'] == ['AAA']


@pytest.mark.parametrize('error', [requests.ConnectTimeout, requests.ReadTimeout])
def test_sec_socket_timeout_and_retry_cap(monkeypatch, error):
    session = Mock()
    session.get.side_effect = error('stalled')
    monkeypatch.setattr('scanner.v4.catalysts.time.sleep', lambda _: None)
    adapter = SecFilingAdapter(session=session, max_retries=100, max_workers=100)
    with pytest.raises(error):
        adapter._get_json('https://data.sec.gov/submissions/test')
    assert adapter.max_workers == 5
    assert session.get.call_count == 2
    assert all(c.kwargs['timeout'] == (3.0, 5.0) for c in session.get.call_args_list)
    session.get.reset_mock()
    with pytest.raises(error):
        adapter._get_proxied_json('https://data.sec.gov/submissions/test')
    session.get.assert_called_once()
    assert session.get.call_args.kwargs['timeout'] == (3.0, 5.0)
    assert session.get.call_args.kwargs['allow_redirects'] is False


def test_optional_coverage_warns_but_keeps_snapshot_predicate(monkeypatch, capsys):
    from scanner import catalyst_pipeline as gate
    t0 = datetime(2026, 9, 28, tzinfo=timezone.utc)
    rows = [('AAA', 'ALPHA_VANTAGE', t0),
            ('AAA', 'YAHOO_NEWS', t0-timedelta(minutes=16))]
    cur = Mock()
    cur.__enter__ = Mock(return_value=cur)
    cur.__exit__ = Mock(return_value=False)
    cur.fetchall.return_value = rows
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.cursor.return_value = cur
    monkeypatch.setattr('scanner.database.connection', lambda: conn)
    assert gate.verify_coverage(['AAA'], anchor=t0, pg_snapshot='10:20:15')
    sql, params = cur.execute.call_args.args
    assert 'pg_visible_in_snapshot(c.writer_xid,%s::pg_snapshot)' in sql
    assert '10:20:15' in params
    output = capsys.readouterr().out
    for provider in ("YAHOO_NEWS", "SEC_EDGAR"):
        assert f'CATALYST_COVERAGE_WARNING provider={provider} missing=1' in output
    cur.fetchall.return_value = [('AAA', p, t0) for p in gate.OPTIONAL_PROVIDERS]
    cur.fetchall.return_value = []
    assert gate.verify_coverage(['AAA'], anchor=t0, pg_snapshot='10:20:15')
    assert 'provider=ALPHA_VANTAGE missing=1' in capsys.readouterr().out
