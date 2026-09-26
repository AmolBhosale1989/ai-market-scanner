from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


CHAIN=("provider","postgres","discovery","v3","confirmation","order_flow","risk","publication","dashboard_validation")
FRIDAY_AS_OF=datetime(2026,9,18,20,0,tzinfo=timezone.utc)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()


@dataclass(frozen=True)
class ChainEvent:
    stage: str
    status: str
    production_run_id: str
    warehouse_as_of_utc: str
    started_at_utc: str
    completed_at_utc: str
    input_hash: str
    output_hash: str


def validate_chain(events: list[ChainEvent]) -> None:
    if tuple(x.stage for x in events)!=CHAIN:
        raise RuntimeError("ACCEPTANCE_CHAIN_ORDER_FAILED")
    if any(x.status!="PASS" for x in events):
        raise RuntimeError("ACCEPTANCE_STAGE_FAILED")
    if len({x.production_run_id for x in events})!=1 or len({x.warehouse_as_of_utc for x in events})!=1:
        raise RuntimeError("ACCEPTANCE_PROVENANCE_FAILED")
    for previous,current in zip(events,events[1:]):
        if current.input_hash!=previous.output_hash:
            raise RuntimeError(f"ACCEPTANCE_DEPENDENCY_FAILED: {previous.stage}->{current.stage}")
        if current.started_at_utc<previous.completed_at_utc:
            raise RuntimeError(f"ACCEPTANCE_TIME_ORDER_FAILED: {previous.stage}->{current.stage}")


def deterministic_friday_session() -> dict:
    """Synthetic unit-test fixture only; this is not production acceptance."""
    run_id="friday-2026-09-18-acceptance"
    as_of=FRIDAY_AS_OF.isoformat()
    value={"bars":[{"ticker":"SPY","close":662.84,"volume":74_000_000},
                   {"ticker":"NVDA","close":176.64,"volume":181_000_000}],"session":"2026-09-18"}
    input_hash=_hash({"fixture":"provider-response-v1"})
    events=[]
    for index,stage in enumerate(CHAIN):
        started=FRIDAY_AS_OF+timedelta(seconds=index*2)
        # Each transformation is stage-labelled and deterministic, so a bypass,
        # reorder or changed input breaks the adjacent hash contract.
        output={"stage":stage,"input":input_hash,"value":value,"version":1}
        output_hash=_hash(output)
        events.append(ChainEvent(
            stage=stage,status="PASS",production_run_id=run_id,warehouse_as_of_utc=as_of,
            started_at_utc=started.isoformat(),completed_at_utc=(started+timedelta(seconds=1)).isoformat(),
            input_hash=input_hash,output_hash=output_hash,
        ))
        input_hash=output_hash
        value={"accepted_stage":stage,"upstream":value}
    validate_chain(events)
    return {"status":"PASS","fixture":"FRIDAY_2026-09-18","production_run_id":run_id,
            "warehouse_as_of_utc":as_of,"chain":[asdict(x) for x in events],"final_hash":input_hash}


def audit_current_run(run_id=None):
    """Read the actual run, stage DAG, immutable rows and source snapshot in one transaction."""
    import pandas as pd
    from . import control_plane as cp
    from .production_telemetry import REQUIRED_DATASETS
    from .signal_freshness import signal_expiry_reason
    from .stage_contract import read_and_validate_stages

    rid = run_id or cp.current_run_id()
    with cp._connect() as conn, conn.cursor() as cur:
        # The final pointer swap repeats integrity/freshness checks. This audit
        # is read-only and cannot make a failed or absent producer publishable.
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        lane, stages, now = read_and_validate_stages(cur, rid, acceptance_running=True)
        cur.execute("""SELECT dataset_name,dataset_version_id,content_hash,row_count
                       FROM dataset_version WHERE pipeline_run_id=%s AND status='AVAILABLE'
                       AND dataset_name=ANY(%s)""", (rid, list(REQUIRED_DATASETS)))
        versions = {row[0]: row[1:] for row in cur.fetchall()}
        missing = set(REQUIRED_DATASETS) - set(versions)
        if missing:
            raise RuntimeError("ACCEPTANCE_DATASET_MISSING: " + ",".join(sorted(missing)))
        payloads = cp._read_version_records(cur, (version[0] for version in versions.values()))
        datasets = {}
        for name, (version, digest, count) in versions.items():
            rows = payloads[version]
            if len(rows) != count or cp._hash(rows) != digest:
                raise RuntimeError(f"ACCEPTANCE_DATASET_CORRUPT: {name}")
            datasets[name] = rows
        snapshots = datasets["warehouse_snapshot"]
        if len(snapshots) != 1 or snapshots[0].get("status") != "PASS" or snapshots[0].get("production_run_id") != rid:
            raise RuntimeError("ACCEPTANCE_SNAPSHOT_PROVENANCE")
        anchor = pd.Timestamp(snapshots[0].get("as_of_utc"))
        gate = next(row for row in stages if row["stage_name"] == "warehouse_gate")
        if pd.isna(anchor) or anchor.tzinfo is None or not gate["started_at"] <= anchor <= gate["completed_at"]:
            raise RuntimeError("ACCEPTANCE_SNAPSHOT_TIME")
        reason = signal_expiry_reason(datasets, now_utc=now)
        if reason:
            raise RuntimeError("ACCEPTANCE_SIGNAL_FRESHNESS: " + reason)
        from .catalyst_pipeline import verify_coverage
        live_rows=datasets.get("live_universe",[])
        live_tickers=[row.get("ticker") for row in live_rows if row.get("ticker")]
        if not live_tickers:
            raise RuntimeError("ACCEPTANCE_CATALYST_UNIVERSE_EMPTY")
        verify_coverage(live_tickers,anchor=anchor.to_pydatetime())
        return {"status": "PASS", "production_run_id": rid, "lane": lane,
                "stages": len(stages), "datasets": len(versions),
                "warehouse_as_of_utc": anchor.isoformat()}


def main():
    result = audit_current_run()
    print("PRODUCTION_ACCEPTANCE_PASS " + json.dumps(result, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
