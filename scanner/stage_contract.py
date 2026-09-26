"""Execution dependencies for the production workflow, including its parallel lane."""
from datetime import datetime


FULL_STAGES = (
    "master_universe", "critical_daily_warehouse", "critical_daily_gate",
    "daily_warehouse", "daily_gate", "daily_prepare", "live_plan",
    "intraday_warehouse", "warehouse_gate", "catalyst_ingestion", "daily_scan", "v3_live",
    "theme_live", "sector_rotation", "broad_breakout", "momentum", "order_flow",
    "order_flow_validation", "v31_challenger", "paper_performance",
    "discovery_products", "quant_shadow", "v4_live", "v4_outcomes", "v5_model",
    "v6_model", "v7_allocator", "v4_shadow", "evidence", "v7_research", "social",
    "operational_health", "audit", "acceptance",
)
LIVE_DEPENDENCIES = {
    "seed_snapshot": (),
    "intraday_warehouse": ("seed_snapshot",),
    "warehouse_gate": ("intraday_warehouse",),
    "catalyst_ingestion": ("warehouse_gate",),
    **{name: ("catalyst_ingestion",) for name in (
        "broad_breakout", "theme_live", "sector_rotation", "premarket", "v4_live")},
    "v3_live": ("broad_breakout", "premarket"),
    "momentum": ("sector_rotation", "broad_breakout"),
    "order_flow": ("momentum",),
    **{name: ("order_flow", "v3_live", "theme_live") for name in (
        "order_flow_validation", "v31_challenger", "paper_performance",
        "daily_pick", "high_conviction_alerts")},
    "discovery_products": ("order_flow_validation", "v31_challenger",
                           "paper_performance", "daily_pick", "high_conviction_alerts"),
    "operational_health": ("discovery_products", "v4_live"),
    "audit": ("operational_health",),
    "acceptance": ("audit",),
}


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeError("ACCEPTANCE_INVALID_STAGE_TIME")
    return value


def validate_stages(rows, *, run_started_at, now, acceptance_running=False):
    """Reject absent stages as well as failures; numeric stage order is not a DAG."""
    stages = {row["stage_name"]: row for row in rows}
    if len(stages) != len(rows):
        raise RuntimeError("ACCEPTANCE_DUPLICATE_STAGE")
    lane = "live" if "seed_snapshot" in stages else "full"
    dependencies = LIVE_DEPENDENCIES if lane == "live" else {
        name: (() if index == 0 else (FULL_STAGES[index-1],))
        for index, name in enumerate(FULL_STAGES)
    }
    missing, extra = set(dependencies) - set(stages), set(stages) - set(dependencies)
    if missing or extra:
        raise RuntimeError(f"ACCEPTANCE_STAGE_SET: missing={','.join(sorted(missing))}; unexpected={','.join(sorted(extra))}")
    for name, row in stages.items():
        running = acceptance_running and name == "acceptance"
        if row["status"] != ("STARTED" if running else "PASS"):
            raise RuntimeError(f"ACCEPTANCE_STAGE_FAILED: {name}/{row['status']}")
        start = _aware(row["started_at"])
        end = now if running else _aware(row["completed_at"])
        if not run_started_at <= start <= end <= now:
            raise RuntimeError(f"ACCEPTANCE_STAGE_TIME: {name}")
        for parent in dependencies[name]:
            if _aware(stages[parent]["completed_at"]) > start:
                raise RuntimeError(f"ACCEPTANCE_DEPENDENCY: {parent}->{name}")
    return lane


def read_and_validate_stages(cur, run_id, *, acceptance_running=False):
    cur.execute("SELECT started_at FROM pipeline_run WHERE pipeline_run_id=%s", (run_id,))
    run = cur.fetchone()
    if run is None:
        raise RuntimeError("ACCEPTANCE_RUN_MISSING")
    cur.execute("""SELECT stage_name,status,started_at,completed_at FROM pipeline_stage
                   WHERE pipeline_run_id=%s""", (run_id,))
    rows = [dict(zip(("stage_name", "status", "started_at", "completed_at"), row))
            for row in cur.fetchall()]
    cur.execute("SELECT clock_timestamp()")
    now = cur.fetchone()[0]
    lane = validate_stages(rows, run_started_at=run[0], now=now,
                           acceptance_running=acceptance_running)
    return lane, rows, now
