"""Wall-clock expiry of an immutable published snapshot (no provider fallback)."""
import pandas as pd
import pandas_market_calendars as mcal

from .config import LIVE_INTRADAY_MAX_AGE_MINUTES


def regular_market_open(now_utc=None) -> bool:
    """A valid closing snapshot is research, not a currently executable signal."""
    try:
        now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
        if pd.isna(now) or now.tzinfo is None:
            return False
        day = now.tz_convert("America/New_York").date()
        schedule = mcal.get_calendar("NYSE").schedule(start_date=day, end_date=day)
        return bool(not schedule.empty and schedule.iloc[0].market_open <= now < schedule.iloc[0].market_close)
    except (ValueError, TypeError, AttributeError, KeyError):
        return False


def publication_expiry_reason(manifest: dict, now_utc=None) -> str:
    if manifest.get("status") != "PUBLISHED":
        return "No validated PostgreSQL publication is available."
    try:
        now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
        observed = pd.Timestamp(manifest.get("warehouse_as_of_utc"))
        published = pd.Timestamp(manifest.get("published_at_utc"))
        if any(pd.isna(x) or x.tzinfo is None for x in (now, observed, published)):
            raise ValueError("Missing or timezone-free timestamp")
        if observed > published or published > now:
            raise ValueError("Future or inconsistent timestamp")
        schedule = mcal.get_calendar("NYSE").schedule(
            start_date=(now - pd.Timedelta(days=10)).date(), end_date=now.date())
        opened = schedule[pd.to_datetime(schedule.market_open, utc=True) <= now]
        if opened.empty:
            raise ValueError("Cannot resolve NYSE session")
        session = opened.iloc[-1]
        market_open, market_close = pd.Timestamp(session.market_open), pd.Timestamp(session.market_close)
        if now < market_close:
            if observed < market_open:
                return "Published inputs are from before the current market session."
            if (now - observed).total_seconds() > LIVE_INTRADAY_MAX_AGE_MINUTES * 60:
                return "Published inputs have exceeded the live freshness limit."
        elif observed < market_close:
            return "A publication validated after the latest market close is required."
    except (ValueError, TypeError, AttributeError, KeyError):
        return "Publication timestamps or market calendar could not be validated."
    return ""
