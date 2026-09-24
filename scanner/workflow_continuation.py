from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pandas_market_calendars as mcal


def should_continue_live_session(
    now_utc: datetime | pd.Timestamp | None = None,
    *,
    minimum_remaining_minutes: int = 20,
) -> tuple[bool, str]:
    """Decide whether a successful live run should queue one successor.

    The exchange calendar, rather than a weekday/hour approximation, handles
    holidays and early closes. The final buffer prevents a new pipeline from
    starting too close to the closing bell.
    """
    now = pd.Timestamp(now_utc or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(
        start_date=(now - pd.Timedelta(days=1)).date(),
        end_date=(now + pd.Timedelta(days=1)).date(),
    )
    if schedule.empty:
        return False, "no_exchange_session"

    active = schedule[
        (pd.to_datetime(schedule["market_open"], utc=True) <= now)
        & (pd.to_datetime(schedule["market_close"], utc=True) >= now)
    ]
    if active.empty:
        return False, "market_closed"

    close = pd.Timestamp(active.iloc[-1]["market_close"]).tz_convert("UTC")
    remaining = (close - now).total_seconds() / 60.0
    if remaining < minimum_remaining_minutes:
        return False, f"closing_buffer_{remaining:.1f}m"
    return True, f"market_open_{remaining:.1f}m_remaining"


def main() -> None:
    dispatch, reason = should_continue_live_session()
    print(f"dispatch={'true' if dispatch else 'false'}")
    print(f"reason={reason}")


if __name__ == "__main__":
    main()
