"""Validate observation times, never calculation or publication timestamps."""
import pandas as pd
import pandas_market_calendars as mcal

SIGNAL_BAR_FIELDS = {
    'momentum_signals': 'last_bar_et',
    'rotation_leaders': 'last_bar_et',
    'sector_rotation': 'updated_at_et',  # producer records the source bar start
}


def signal_expiry_reason(datasets, now_utc=None):
    now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz='UTC')
    try:
        if pd.isna(now) or now.tzinfo is None:
            raise ValueError('invalid clock')
        schedule = mcal.get_calendar('NYSE').schedule(
            start_date=(now-pd.Timedelta(days=10)).date(), end_date=now.date())
        opened = schedule[pd.to_datetime(schedule.market_open, utc=True) <= now]
        if opened.empty:
            raise ValueError('no session')
        session = opened.iloc[-1]
        opened_at = pd.Timestamp(session.market_open)
        cutoff = min(now, pd.Timestamp(session.market_close))
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
