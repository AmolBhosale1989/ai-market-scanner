from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd

from .control_plane import current_run_id, write_dataset
from .dashboard_data import read_dashboard_datasets

FEED_DATASETS = (
    'recommended_trades', 'liquid_leaders', 'watchlist', 'latest_scan',
    'upcoming_events', 'trending_themes', 'scan_health', 'event_status',
    'intraday_live', 'monitor_health', 'performance_summary', 'probability_calibration',
)


def _records(frames, name: str, limit: int | None=None):
    df=frames[name][0]
    if limit is not None:
        df=df.head(limit)
    return json.loads(df.where(pd.notna(df),None).to_json(orient="records"))


def build_product_feed():
    """Create a stable, versioned JSON contract for future apps/API clients."""
    frames = read_dashboard_datasets(FEED_DATASETS, run_id=current_run_id())
    def records(name, limit):
        return _records(frames, name, limit)
    payload={
        "schema_version":"1.0",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "research_only":True,
        "market_hunt":{
            "recommendations":records("recommended_trades",20),
            "liquid_leaders":records("liquid_leaders",50),
            "watchlist":records("watchlist",50),
            "opportunities":records("latest_scan",50),
            "upcoming_events":records("upcoming_events",100),
            "themes":records("trending_themes",20),
            "scan_health":records("scan_health",1),
            "event_status":records("event_status",1),
            "live_monitor":records("intraday_live",50),
            "monitor_health":records("monitor_health",1),
            "performance":records("performance_summary",1),
            "calibration":records("probability_calibration",100),
        },
    }
    write_dataset("product_feed",pd.DataFrame([payload]),entity_key=None)
    return payload


if __name__=="__main__":
    p=build_product_feed()
    print(f"Product feed built with {len(p['market_hunt']['opportunities'])} opportunities.")
