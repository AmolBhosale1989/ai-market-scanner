from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

ACTIVE_STATES={"active","trialing"}


@dataclass(frozen=True)
class SubscriptionState:
    plan: str
    status: str
    current_period_end: datetime | None=None

    @property
    def active(self) -> bool:
        if self.status.lower() not in ACTIVE_STATES:
            return False
        if self.current_period_end is None:
            return True
        end=self.current_period_end
        if end.tzinfo is None:
            end=end.replace(tzinfo=timezone.utc)
        return end>datetime.now(timezone.utc)


def effective_plan(subscription: SubscriptionState | None) -> str:
    if subscription is None or not subscription.active:
        return "FREE"
    plan=str(subscription.plan or "FREE").upper()
    return plan if plan in {"FREE","PRO","API"} else "FREE"
