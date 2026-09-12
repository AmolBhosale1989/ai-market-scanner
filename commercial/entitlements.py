from __future__ import annotations

PLAN_ENTITLEMENTS = {
    "FREE": {
        "opportunities_limit": 5,
        "events_limit": 5,
        "live_monitor": False,
        "alerts": False,
        "performance_analytics": False,
        "api_access": False,
    },
    "PRO": {
        "opportunities_limit": 50,
        "events_limit": 100,
        "live_monitor": True,
        "alerts": True,
        "performance_analytics": True,
        "api_access": False,
    },
    "API": {
        "opportunities_limit": 500,
        "events_limit": 500,
        "live_monitor": True,
        "alerts": True,
        "performance_analytics": True,
        "api_access": True,
    },
}


def normalize_plan(plan: str | None) -> str:
    p=str(plan or "FREE").strip().upper()
    return p if p in PLAN_ENTITLEMENTS else "FREE"


def entitlements_for(plan: str | None):
    p=normalize_plan(plan)
    return {"plan":p,**PLAN_ENTITLEMENTS[p]}


def feature_allowed(plan: str | None, feature: str) -> bool:
    value=entitlements_for(plan).get(feature,False)
    return bool(value) if isinstance(value,bool) else bool(value)
