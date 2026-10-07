from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import sys
import time

import pandas as pd
import pandas_market_calendars as mcal


MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_DELAY_SECONDS = 300


@dataclass(frozen=True)
class ContinuationPlan:
    dispatch: bool
    reason: str
    recovery_attempt: int = 0
    delay_seconds: int = 0


def should_continue_live_session(
    now_utc: datetime | pd.Timestamp | None = None,
    *,
    minimum_remaining_minutes: int = 20,
) -> tuple[bool, str]:
    """Decide whether the exchange session can accommodate another run.

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


def plan_continuation(
    now_utc: datetime | pd.Timestamp | None = None,
    *,
    result: str = "success",
    recovery_attempt: int = 0,
) -> ContinuationPlan:
    """Keep publication failure visible while allowing bounded new observations."""
    if result not in {"success", "failure", "cancelled", "skipped"}:
        raise ValueError("Unknown workflow result")
    if type(recovery_attempt) is not int or not 0 <= recovery_attempt <= MAX_RECOVERY_ATTEMPTS:
        raise ValueError("Invalid recovery attempt")
    if result in {"cancelled", "skipped"}:
        return ContinuationPlan(False, f"run_{result}")

    dispatch, reason = should_continue_live_session(now_utc)
    if not dispatch:
        return ContinuationPlan(False, reason)
    if result == "success":
        return ContinuationPlan(True, reason)
    if recovery_attempt == MAX_RECOVERY_ATTEMPTS:
        return ContinuationPlan(False, "recovery_limit_reached", recovery_attempt)
    return ContinuationPlan(
        True,
        f"recovery_{recovery_attempt + 1}_{reason}",
        recovery_attempt + 1,
        RECOVERY_DELAY_SECONDS,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plan the next market observation")
    parser.add_argument("--result", choices=["success", "failure", "cancelled", "skipped"], default="success")
    parser.add_argument("--recovery-attempt", type=int, choices=range(MAX_RECOVERY_ATTEMPTS + 1), default=0)
    parser.add_argument("--wait-before-retry", action="store_true")
    args = parser.parse_args(argv)
    plan = plan_continuation(result=args.result, recovery_attempt=args.recovery_attempt)
    if plan.dispatch and plan.delay_seconds and args.wait_before_retry:
        print(
            f"::warning::Production failed; waiting {plan.delay_seconds}s before recovery "
            f"attempt {plan.recovery_attempt}/{MAX_RECOVERY_ATTEMPTS}. Publication guards remain enforced.",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(plan.delay_seconds)
        # The exchange may enter its closing buffer during the backoff.
        plan = plan_continuation(result=args.result, recovery_attempt=args.recovery_attempt)
    if plan.reason == "recovery_limit_reached":
        print(
            "::error::Automatic production recovery exhausted after three attempts. "
            "Inspect the failed runs; no publication was forced. The scheduled fallback remains configured.",
            file=sys.stderr,
        )
    print(f"dispatch={'true' if plan.dispatch else 'false'}")
    print(f"reason={plan.reason}")
    print(f"recovery_attempt={plan.recovery_attempt}")


if __name__ == "__main__":
    main()
