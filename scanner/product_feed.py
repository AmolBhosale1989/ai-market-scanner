from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from .config import OUTPUT_DIR


def _records(name: str, limit: int | None=None):
    path=OUTPUT_DIR/name
    if not path.exists():
        return []
    try:
        df=pd.read_csv(path)
    except Exception:
        return []
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
            "recommendations":_records("recommended_trades.csv",20),
            "liquid_leaders":_records("liquid_leaders.csv",50),
            "watchlist":_records("watchlist.csv",50),
            "opportunities":_records("latest_scan.csv",50),
            "upcoming_events":_records("upcoming_events.csv",100),
            "themes":_records("trending_themes.csv",20),
            "scan_health":_records("scan_health.csv",1),
            "event_status":_records("event_status.csv",1),
            "live_monitor":_records("intraday_live.csv",50),
            "monitor_health":_records("monitor_health.csv",1),
            "performance":_records("performance_summary.csv",1),
            "calibration":_records("probability_calibration.csv",100),
        },
    }
    path=OUTPUT_DIR/"product_feed.json"
    path.write_text(json.dumps(payload,indent=2,allow_nan=False))
    return payload


if __name__=="__main__":
    p=build_product_feed()
    print(f"Product feed built with {len(p['market_hunt']['opportunities'])} opportunities.")
