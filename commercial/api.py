from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from .entitlements import entitlements_for

ROOT=Path(__file__).resolve().parents[1]
FEED=ROOT/"outputs"/"product_feed.json"

app=FastAPI(title="Market Hunt API",version="0.1.0")


def _load_feed() -> dict[str,Any]:
    if not FEED.exists():
        return {
            "schema_version":"1.0",
            "research_only":True,
            "market_hunt":{},
        }
    try:
        return json.loads(FEED.read_text())
    except Exception:
        return {
            "schema_version":"1.0",
            "research_only":True,
            "market_hunt":{},
        }


def _authorize(authorization: str | None):
    expected=os.getenv("MARKET_HUNT_API_TOKEN","").strip()
    if not expected:
        raise HTTPException(status_code=503,detail="API authentication is not configured.")
    token=""
    if authorization and authorization.lower().startswith("bearer "):
        token=authorization[7:].strip()
    if token!=expected:
        raise HTTPException(status_code=401,detail="Unauthorized")


@app.get("/health")
def health():
    feed=_load_feed()
    return {
        "status":"ok",
        "schema_version":feed.get("schema_version","1.0"),
        "research_only":True,
        "feed_available":FEED.exists(),
    }


@app.get("/v1/opportunities")
def opportunities(
    authorization: str | None=Header(default=None),
    x_market_hunt_plan: str | None=Header(default="API"),
):
    _authorize(authorization)
    feed=_load_feed()
    ent=entitlements_for(x_market_hunt_plan)
    rows=feed.get("market_hunt",{}).get("opportunities",[])
    return {
        "schema_version":feed.get("schema_version","1.0"),
        "research_only":True,
        "plan":ent["plan"],
        "data":rows[:int(ent["opportunities_limit"])],
    }


@app.get("/v1/events")
def events(
    authorization: str | None=Header(default=None),
    x_market_hunt_plan: str | None=Header(default="API"),
):
    _authorize(authorization)
    feed=_load_feed()
    ent=entitlements_for(x_market_hunt_plan)
    rows=feed.get("market_hunt",{}).get("upcoming_events",[])
    return {
        "schema_version":feed.get("schema_version","1.0"),
        "research_only":True,
        "plan":ent["plan"],
        "data":rows[:int(ent["events_limit"])],
    }


@app.get("/v1/themes")
def themes(authorization: str | None=Header(default=None)):
    _authorize(authorization)
    feed=_load_feed()
    return {
        "schema_version":feed.get("schema_version","1.0"),
        "research_only":True,
        "data":feed.get("market_hunt",{}).get("themes",[]),
    }


@app.get("/v1/performance")
def performance(authorization: str | None=Header(default=None)):
    _authorize(authorization)
    feed=_load_feed()
    return {
        "schema_version":feed.get("schema_version","1.0"),
        "research_only":True,
        "performance":feed.get("market_hunt",{}).get("performance",[]),
        "calibration":feed.get("market_hunt",{}).get("calibration",[]),
    }
