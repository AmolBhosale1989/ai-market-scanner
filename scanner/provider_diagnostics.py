"""Observe provider results without filling gaps or altering market data."""
import pandas as pd


def describe_response(symbol, frame, *, period, interval, started, received, elapsed):
    record = dict(symbol=symbol, requested_period=period, requested_interval=interval,
        request_started_at=started.isoformat(), response_received_at=received.isoformat(),
        download_call_seconds=round(elapsed, 6), provider_reported_time=None,
        response_headers_available=False, transport='yfinance.download',
        returned_rows=0 if frame is None else len(frame))
    # yf.download exposes frames, not HTTP headers or its resolved request window.
    # Never label local receipt time as provider time or infer absent trades.
    if frame is None or frame.empty or interval != '5m':
        return record
    times = pd.to_datetime(frame.index, utc=True, errors='coerce')
    completed = times.notna() & (times + pd.Timedelta(minutes=5) <= received)
    if not completed.any():
        record['completed_rows'] = 0
        return record
    selected = frame.loc[completed].copy()
    selected.index = times[completed]
    selected = selected.sort_index()
    last = selected.index[-1]
    volume = pd.to_numeric(pd.Series([selected.iloc[-1].get('Volume')]), errors='coerce').iloc[0]
    record.update(completed_rows=len(selected), latest_bar_start=last.isoformat(),
        latest_bar_end=(last+pd.Timedelta(minutes=5)).isoformat(),
        latest_bar_volume=None if pd.isna(volume) else float(volume),
        is_zero_volume=None if pd.isna(volume) else bool(volume == 0),
        age_at_response_seconds=(received-last-pd.Timedelta(minutes=5)).total_seconds())
    # Gaps only between observed bars in the same regular session: no overnight,
    # premarket, or not-yet-complete buckets and no claim about trade activity.
    local = selected.index.tz_convert('America/New_York')
    mask = (local.date == local[-1].date()) & (local.hour*60+local.minute >= 570) & (local.hour*60+local.minute < 960)
    observed = selected.index[mask].unique()
    if len(observed):
        gaps = pd.date_range(observed.min(), observed.max(), freq='5min').difference(observed)
        record['unreturned_internal_buckets'] = [t.isoformat() for t in gaps]
    return record
