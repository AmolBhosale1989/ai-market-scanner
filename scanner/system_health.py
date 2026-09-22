from __future__ import annotations

from datetime import datetime
import argparse
from zoneinfo import ZoneInfo

import pandas as pd

from .session_contract import expected_market_data_session
from .control_plane import read_dataset, write_dataset

NY = ZoneInfo("America/New_York")

PRODUCTION_CHECKS = [
    ("Warehouse Snapshot", "warehouse_snapshot", "as_of_utc", 20, "REGULAR"),
    ("V3 Live Production", "monitor_health", "checked_at_et", 20, "REGULAR"),
    ("Themes", "theme_health", "updated_at_et", 20, "REGULAR"),
    ("Sector Rotation", "sector_rotation_health", "updated_at_et", 20, "REGULAR"),
    ("Momentum", "momentum_health", "updated_at_et", 20, "REGULAR"),
    ("Order Flow", "order_flow_strategy_health", "updated_at_et", 20, "REGULAR"),
]

SHADOW_CHECKS = [
    ("V4 Live Intelligence", "v4_worker_health", "generated_at_utc", 25, "REGULAR"),
    ("Broad Breakout", "broad_breakout_health", "updated_at_et", 20, "REGULAR"),
    ("High Conviction Alerts", "high_conviction_alert_health", "checked_at_et", 20, "REGULAR"),
    ("Premarket Discovery", "premarket_health", "checked_at_et", 45, "PREMARKET"),
    ("Daily Pick Validation", "daily_top_pick_summary", "updated_at_et", 90, "SESSION"),
    ("Quant Strategy Suite", "quant_shadow_health", "generated_at_utc", 1800, "SESSION"),
]


def _read_timestamp(dataset_name: str, field: str):
    frame=read_dataset(dataset_name,required=False)
    if frame.empty:
        return None, "MISSING"
    try:
        if field not in frame.columns:
            return None, "INVALID"
        raw=frame.iloc[-1].get(field)
        ts=pd.to_datetime(raw, errors="coerce", utc=True)
        if pd.isna(ts):
            return None, "INVALID"
        return ts, "OK"
    except Exception:
        return None, "INVALID"


def _last_record(name: str) -> dict:
    frame=read_dataset(name,required=False)
    return frame.iloc[-1].to_dict() if not frame.empty else {}


def _int(value) -> int:
    parsed=pd.to_numeric(value,errors="coerce")
    return int(parsed) if pd.notna(parsed) else -1


def _production_semantics(expected_session: str) -> dict[str, tuple[str,str]]:
    theme=_last_record("theme_health")
    sector=_last_record("sector_rotation_health")
    momentum=_last_record("momentum_health")
    order_flow=_last_record("order_flow_strategy_health")
    results={}

    def session_ok(row):
        return str(row.get("session_date","")).strip()==expected_session

    results["theme_health"]=(
        ("OK","") if session_ok(theme) and _int(theme.get("themes_ranked"))>0
        else ("INVALID",f"expected_session={expected_session} themes_ranked={_int(theme.get('themes_ranked'))}"))
    results["sector_rotation_health"]=(
        ("OK","") if session_ok(sector) and _int(sector.get("themes_scanned"))>0 and _int(sector.get("stocks_scanned"))>0
        else ("INVALID",f"expected_session={expected_session} themes={_int(sector.get('themes_scanned'))} stocks={_int(sector.get('stocks_scanned'))}"))
    momentum_inputs=_int(momentum.get("candidate_inputs"))
    momentum_evaluated=_int(momentum.get("leaders_evaluated"))
    results["momentum_health"]=(
        ("OK","") if session_ok(momentum) and momentum_inputs>=0 and momentum_evaluated==momentum_inputs
        else ("INVALID",f"expected_session={expected_session} inputs={momentum_inputs} evaluated={momentum_evaluated}"))
    flow_expected=_int(order_flow.get("expected_inputs"))
    flow_evaluated=_int(order_flow.get("evaluated"))
    results["order_flow_strategy_health"]=(
        ("OK","") if session_ok(order_flow) and flow_expected==momentum_inputs and flow_evaluated==flow_expected
        else ("INVALID",f"expected_session={expected_session} expected={flow_expected} evaluated={flow_evaluated}"))
    return results


def run() -> pd.DataFrame:
    now_et=datetime.now(NY)
    now_utc=pd.Timestamp.now(tz="UTC")
    open_et=pd.Timestamp(now_et.date(), tz=NY)+pd.Timedelta(hours=9,minutes=30)
    close_et=pd.Timestamp(now_et.date(), tz=NY)+pd.Timedelta(hours=16)
    now_et_ts=pd.Timestamp(now_et)
    market_open=bool(open_et <= now_et_ts <= close_et and now_et.weekday()<5)
    premarket_start=pd.Timestamp(now_et.date(), tz=NY)+pd.Timedelta(hours=4)
    premarket_open=bool(premarket_start <= now_et_ts < open_et and now_et.weekday()<5)
    expected_session=str(expected_market_data_session(now_utc))
    semantics=_production_semantics(expected_session)

    rows=[]
    for module, filename, field, max_age, window in PRODUCTION_CHECKS + SHADOW_CHECKS:
        ts,status=_read_timestamp(filename, field)
        semantic_status,semantic_detail=semantics.get(filename,("OK",""))
        if status=="OK" and semantic_status!="OK":
            status=semantic_status
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
            "dataset":filename,
            "last_update_utc":ts.isoformat() if ts is not None else "",
            "age_minutes":round(age_min,1) if age_min is not None else "",
            "max_age_minutes":max_age,
            "status":status,
            "market_open":market_open,
            "role":"PRODUCTION" if (module, filename, field, max_age, window) in PRODUCTION_CHECKS else "SHADOW",
            "checked_at_et":now_et.isoformat(timespec="seconds"),
            "expected_session":expected_session,
            "semantic_detail":semantic_detail,
        })

    out=pd.DataFrame(rows)
    write_dataset("live_system_health",out,entity_key="module")
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
    write_dataset("live_system_health_summary",summary,entity_key=None)
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
