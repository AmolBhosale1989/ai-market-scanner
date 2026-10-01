import copy

import pytest

from scanner.signal_freshness import (
    CRITICAL_SIGNAL_SYMBOLS, SIGNAL_BAR_FIELDS,
    filter_noncritical_signal_rows, signal_expiry_reason,
)

NOW = '2026-09-25T19:09:33Z'
FRESH = '2026-09-25T18:55:00Z'
STALE = '2026-09-25T18:50:00Z'


def empty():
    return {name: [] for name in SIGNAL_BAR_FIELDS}


@pytest.mark.parametrize('name', SIGNAL_BAR_FIELDS)
def test_stale_optional_rows_are_removed_not_relabelled(name):
    data = empty()
    field = SIGNAL_BAR_FIELDS[name]
    fresh = {'ticker': 'SPY', field: FRESH, 'as_of_utc': NOW, 'pg_snapshot': '100:100:'}
    data[name] = [{'ticker': 'SKYY', field: STALE}, fresh]
    original = copy.deepcopy(data)
    filtered, excluded = filter_noncritical_signal_rows(data, NOW)
    assert data == original
    assert filtered[name] == [fresh]
    assert excluded[0]['symbol'] == 'SKYY'
    assert not signal_expiry_reason(filtered, NOW)
    assert signal_expiry_reason(data, NOW)  # Dashboard validation stays strict.


@pytest.mark.parametrize('symbol', sorted(CRITICAL_SIGNAL_SYMBOLS))
def test_each_of_the_21_critical_benchmarks_remains_fatal(symbol):
    assert len(CRITICAL_SIGNAL_SYMBOLS) == 21
    assert 'SKYY' not in CRITICAL_SIGNAL_SYMBOLS
    data = empty()
    data['sector_rotation'] = [{'etf': symbol, 'updated_at_et': STALE}]
    with pytest.raises(ValueError, match=f'sector_rotation/{symbol}'):
        filter_noncritical_signal_rows(data, NOW)


@pytest.mark.parametrize('stamp', [None, 'bad', '2026-09-25T18:50:00', '2026-09-25T19:10:00Z'])
def test_optional_does_not_mean_invalid_or_future_is_allowed(stamp):
    data = empty()
    data['sector_rotation'] = [{'etf': 'SKYY', 'updated_at_et': stamp}]
    with pytest.raises(ValueError):
        filter_noncritical_signal_rows(data, NOW)


def test_empty_missing_and_exact_limit_are_distinct():
    data = empty()
    data['trending_themes'] = [{'etf': 'SKYY', 'live_bar_at_et': FRESH}]
    assert filter_noncritical_signal_rows(data, '2026-09-25T19:10:00Z')[0] == data
    filtered, _ = filter_noncritical_signal_rows(data, '2026-09-25T19:10:00.001Z')
    assert filtered['trending_themes'] == []
    assert not signal_expiry_reason(filtered, '2026-09-25T19:10:00.001Z')
    with pytest.raises(ValueError, match='dataset missing'):
        filter_noncritical_signal_rows({}, NOW)
    data['trending_themes'][0].pop('etf')
    with pytest.raises(ValueError, match='symbol missing'):
        filter_noncritical_signal_rows(data, NOW)


def test_nested_feed_and_current_recommendations_cannot_leak_excluded_symbol():
    data = empty()
    stale = {'ticker': 'MKSI', 'live_bar_at_et': STALE}
    data['intraday_live'] = [stale]
    data['recommended_trades'] = [{'ticker': 'MKSI'}, {'ticker': 'AAPL'}]
    data['trending_themes'] = [{'etf': 'SKYY', 'live_bar_at_et': STALE}]
    data['product_feed'] = [{'market_hunt': {
        'live_monitor': [stale], 'recommendations': data['recommended_trades'],
        'themes': data['trending_themes'], 'performance': [{'ticker': 'MKSI'}],
    }}]
    data['paper_journal'] = [{'ticker': 'MKSI', 'realized_pnl': 5}]
    filtered, _ = filter_noncritical_signal_rows(data, NOW)
    feed = filtered['product_feed'][0]['market_hunt']
    assert feed['live_monitor'] == feed['themes'] == []
    assert feed['recommendations'] == [{'ticker': 'AAPL'}]
    assert feed['performance'] == [{'ticker': 'MKSI'}]
    assert filtered['paper_journal'] == data['paper_journal']
