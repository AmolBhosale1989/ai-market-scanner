"""Validate observation times, never calculation or publication timestamps."""
import pandas as pd
import pandas_market_calendars as mcal

from .config import CORE_INTRADAY_MARKET_SYMBOLS, ROTATION_REQUIRED_SYMBOLS

CRITICAL_SIGNAL_SYMBOLS = frozenset(CORE_INTRADAY_MARKET_SYMBOLS + ROTATION_REQUIRED_SYMBOLS)

SIGNAL_BAR_FIELDS = {
    'momentum_signals': 'last_bar_et',
    'rotation_leaders': 'last_bar_et',
    'sector_rotation': 'updated_at_et',  # producer records the source bar start
    'intraday_live': 'live_bar_at_et',
    'v3_live_snapshot': 'last_bar_et',
    'v4_live_snapshot': 'live_bar_at_et',
    'trending_themes': 'live_bar_at_et',
}

# Current recommendations can repeat a symbol from a checked signal family.
# Historical journals, catalogues and source observations must not be pruned.
CURRENT_SIGNAL_OUTPUTS = frozenset(SIGNAL_BAR_FIELDS) | frozenset((
    'all_candidates', 'latest_scan', 'recommended_trades', 'liquid_leaders',
    'watchlist', 'v3_live_discovery', 'legendary_setups', 'legendary_consensus',
    'order_flow_strategy', 'premarket_discovery', 'v4_monitor_shortlist',
))
FEED_SIGNAL_OUTPUTS = {
    'recommendations': ('recommended_trades', 20),
    'liquid_leaders': ('liquid_leaders', 50), 'watchlist': ('watchlist', 50),
    'opportunities': ('latest_scan', 50), 'themes': ('trending_themes', 20),
    'live_monitor': ('intraday_live', 50),
}


def _session_window(now_utc):
    now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz='UTC')
    if pd.isna(now) or now.tzinfo is None:
        raise ValueError('invalid clock')
    schedule = mcal.get_calendar('NYSE').schedule(
        start_date=(now-pd.Timedelta(days=10)).date(), end_date=now.date())
    opened = schedule[pd.to_datetime(schedule.market_open, utc=True) <= now]
    if opened.empty:
        raise ValueError('no session')
    session = opened.iloc[-1]
    return pd.Timestamp(session.market_open), min(now, pd.Timestamp(session.market_close))


def _symbol(row):
    for key in ('ticker', 'etf'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ''


def filter_noncritical_signal_rows(datasets, now_utc):
    """Build a publication projection; never relabel stale observations as fresh.

    Only valid, completed but expired non-critical observations are excludable.
    Missing/invalid/future timestamps, identities and missing datasets still
    fail closed. The strict reader below continues to reject any stale payload.
    """
    opened_at, cutoff = _session_window(now_utc)
    expired = set()
    exclusions = []
    for name, field in SIGNAL_BAR_FIELDS.items():
        if name not in datasets:
            raise ValueError(f'Signal input dataset missing: {name}.')
        for row in datasets[name]:
            symbol = _symbol(row)
            if not symbol:
                raise ValueError(f'Signal symbol missing: {name}.')
            try:
                start = pd.Timestamp(row.get(field))
                if pd.isna(start) or start.tzinfo is None:
                    raise ValueError('invalid timestamp')
            except (ValueError, TypeError) as exc:
                raise ValueError(f'Signal observation timestamp invalid: {name}/{symbol}.') from exc
            end = start + pd.Timedelta(minutes=5)
            if end > cutoff:
                raise ValueError(f'Signal inputs incomplete or future: {name}/{symbol}.')
            if start < opened_at or cutoff-end > pd.Timedelta(minutes=10):
                if symbol in CRITICAL_SIGNAL_SYMBOLS:
                    raise ValueError(f'Signal inputs expired or incomplete: {name}/{symbol}, bar end {end.isoformat()}.')
                expired.add(symbol)
                exclusions.append(dict(dataset=name, symbol=symbol, bar_end_utc=end.tz_convert('UTC').isoformat(),
                                       reason='expired_noncritical'))
    if not expired:
        return datasets, exclusions
    filtered = dict(datasets)
    for name in CURRENT_SIGNAL_OUTPUTS.intersection(datasets):
        filtered[name] = [row for row in datasets[name] if _symbol(row) not in expired]
    # product_feed embeds copies; pruning only the top-level datasets would
    # leave stale rows visible to API/front-end consumers of this payload.
    if 'product_feed' in datasets:
        feed = []
        for original in datasets['product_feed']:
            row = dict(original)
            if isinstance(row.get('market_hunt'), dict):
                market = dict(row['market_hunt'])
                for key, (source, limit) in FEED_SIGNAL_OUTPUTS.items():
                    if key in market and source in filtered:
                        market[key] = filtered[source][:limit]
                row['market_hunt'] = market
            feed.append(row)
        filtered['product_feed'] = feed
    return filtered, exclusions


def signal_expiry_reason(datasets, now_utc=None):
    try:
        opened_at, cutoff = _session_window(now_utc)
        for name, field in SIGNAL_BAR_FIELDS.items():
            if name not in datasets:
                return f'Signal input dataset missing: {name}.'
            frame = pd.DataFrame(datasets[name])
            if frame.empty:
                continue  # Valid no-signal state; warehouse checks still apply.
            if field not in frame:
                return f'Signal observation timestamp missing: {name}.{field}.'
            for row in frame.to_dict('records'):
                start = pd.Timestamp(row.get(field))
                if pd.isna(start) or start.tzinfo is None:
                    return f'Signal observation timestamp invalid: {name}.'
                end = start + pd.Timedelta(minutes=5)
                if start < opened_at or end > cutoff or cutoff-end > pd.Timedelta(minutes=10):
                    symbol = row.get('ticker', row.get('etf', '?'))
                    return f'Signal inputs expired or incomplete: {name}/{symbol}, bar end {end.isoformat()}.'
    except (ValueError, TypeError, KeyError, AttributeError):
        return 'Signal observation timestamps or market clock could not be validated.'
    return ''
