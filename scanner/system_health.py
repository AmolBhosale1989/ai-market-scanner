from __future__ import annotations

from datetime import datetime
import argparse
from zoneinfo import ZoneInfo
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR

NY = ZoneInfo("America/New_York")

PRODUCTION_CHECKS = [
    ("Warehouse Snapshot", "warehouse_snapshot.json", "as_of_utc", 20, "REGULAR"),
    ("V3 Live Production", "monitor_health.csv", "checked_at_et", 20, "REGULAR"),
    ("Themes", "theme_health.csv", "updated_at_et", 20, "REGULAR"),
    ("Sector Rotation", "sector_rotation_health.csv", "updated_at_et", 20, "REGULAR"),
    ("Momentum", "momentum_health.csv", "updated_at_et", 20, "REGULAR"),
    ("Order Flow", "order_flow_strategy_health.csv", "updated_at_et", 20, "REGULAR"),
]

SHADOW_CHECKS = [
    ("V4 Live Intelligence", "v4_worker_health.json", "generated_at_utc", 25, "REGULAR"),
    ("Broad Breakout", "broad_breakout_health.csv", "updated_at_et", 20, "REGULAR"),
    ("High Conviction Alerts", "high_conviction_alert_health.csv", "checked_at_et", 20, "REGULAR"),
    ("Premarket Discovery", "premarket_health.csv", "checked_at_et", 45, "PREMARKET"),
    ("Daily Pick Validation", "daily_top_pick_summary.csv", "updated_at_et", 90, "SESSION"),
]


def _read_timestamp(path: Path, field: str):
    if not path.exists() or path.stat().st_size == 0:
        return None, "MISSING"
    try:
        if path.suffix.lower() == ".json":
            obj=json.loads(path.read_text())
            raw=obj.get(field)
        else:
            df=pd.read_csv(path)
            if df.empty or field not in df.columns:
                return None, "INVALID"
            raw=df.iloc[-1].get(field)
        ts=pd.to_datetime(raw, errors="coerce", utc=True)
        if pd.isna(ts):
            return None, "INVALID"
        return ts, "OK"
    except Exception:
        return None, "INVALID"


def run() -> pd.DataFrame:
    now_et=datetime.now(NY)
    now_utc=pd.Timestamp.now(tz="UTC")
    open_et=pd.Timestamp(now_et.date(), tz=NY)+pd.Timedelta(hours=9,minutes=30)
    close_et=pd.Timestamp(now_et.date(), tz=NY)+pd.Timedelta(hours=16)
    now_et_ts=pd.Timestamp(now_et)
    market_open=bool(open_et <= now_et_ts <= close_et and now_et.weekday()<5)
    premarket_start=pd.Timestamp(now_et.date(), tz=NY)+pd.Timedelta(hours=4)
    premarket_open=bool(premarket_start <= now_et_ts < open_et and now_et.weekday()<5)

    rows=[]
    for module, filename, field, max_age, window in PRODUCTION_CHECKS + SHADOW_CHECKS:
        ts,status=_read_timestamp(OUTPUT_DIR/filename, field)
        age_min=None
        if ts is not None:
            age_min=max(0.0,(now_utc-ts).total_seconds()/60)
            should_be_fresh=(
                (window=="REGULAR" and market_open)
                or (window=="PREMARKET" and premarket_open)
                or (window=="SESSION" and (premarket_open or market_open))
            )
            if should_be_fresh and age_min > max_age:
                status="STALE"
            elif not should_be_fresh and status=="OK":
                status="OK"
        rows.append({
            "module":module,
            "file":filename,
            "last_update_utc":ts.isoformat() if ts is not None else "",
            "age_minutes":round(age_min,1) if age_min is not None else "",
            "max_age_minutes":max_age,
            "status":status,
            "market_open":market_open,
            "role":"PRODUCTION" if (module, filename, field, max_age, window) in PRODUCTION_CHECKS else "SHADOW",
            "checked_at_et":now_et.isoformat(timespec="seconds"),
        })

    out=pd.DataFrame(rows)
    out.to_csv(OUTPUT_DIR/"live_system_health.csv",index=False)
    production=out[out["role"]=="PRODUCTION"]
    bad=production["status"].isin(["STALE","MISSING","INVALID"])
    summary=pd.DataFrame([{
        "checked_at_et":now_et.isoformat(timespec="seconds"),
        "market_open":market_open,
        "modules_checked":len(production),
        "healthy_modules":int((production["status"]=="OK").sum()),
        "stale_modules":int((production["status"]=="STALE").sum()),
        "missing_or_invalid_modules":int(production["status"].isin(["MISSING","INVALID"]).sum()),
        "shadow_modules_checked":int((out["role"]=="SHADOW").sum()),
        "shadow_modules_unhealthy":int(((out["role"]=="SHADOW") & out["status"].isin(["STALE","MISSING","INVALID"])).sum()),
        "overall_status":"DEGRADED" if bad.any() and market_open else "OK",
    }])
    summary.to_csv(OUTPUT_DIR/"live_system_health_summary.csv",index=False)
    return out


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--require-production",action="store_true")
    args=p.parse_args()
    df=run()
    print(df.to_string(index=False))
    if args.require_production:
        bad=df[(df["role"]=="PRODUCTION") & df["status"].isin(["STALE","MISSING","INVALID"])]
        if not bad.empty:
            raise RuntimeError("PRODUCTION_HEALTH_FAILED: "+",".join(bad["module"].astype(str)))
