from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd

from .control_plane import read_dataset, write_dataset


def _records(name: str, limit: int | None=None):
    df=read_dataset(name,required=False)
    if limit is not None:
        df=df.head(limit)
    return json.loads(df.where(pd.notna(df),None).to_json(orient="records"))


def build_product_feed():
    """Create a stable, versioned JSON contract for future apps/API clients."""
    payload={
        "schema_version":"1.0",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "research_only":True,
        "market_hunt":{
            "recommendations":_records("recommended_trades",20),
            "liquid_leaders":_records("liquid_leaders",50),
            "watchlist":_records("watchlist",50),
            "opportunities":_records("latest_scan",50),
            "upcoming_events":_records("upcoming_events",100),
            "themes":_records("trending_themes",20),
            "scan_health":_records("scan_health",1),
            "event_status":_records("event_status",1),
            "live_monitor":_records("intraday_live",50),
            "monitor_health":_records("monitor_health",1),
            "performance":_records("performance_summary",1),
            "calibration":_records("probability_calibration",100),
        },
    }
    write_dataset("product_feed",pd.DataFrame([payload]),entity_key=None)
    return payload


if __name__=="__main__":
    p=build_product_feed()
    print(f"Product feed built with {len(p['market_hunt']['opportunities'])} opportunities.")
