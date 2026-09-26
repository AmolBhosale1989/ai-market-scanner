from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re

import pandas as pd
import pytest

from scanner import control_plane as cp
from scanner.production_acceptance import audit_current_run
from scanner.production_telemetry import REQUIRED_DATASETS
from scanner.stage_contract import FULL_STAGES, LIVE_DEPENDENCIES, validate_stages


START = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)


def stage_rows(start=START, lane="live", accepting=False):
    deps = LIVE_DEPENDENCIES if lane == "live" else {
        name: (() if i == 0 else (FULL_STAGES[i-1],)) for i, name in enumerate(FULL_STAGES)}
    rows = {}
    for name, parents in deps.items():
        begin = max((rows[p]["completed_at"] for p in parents), default=start)
        rows[name] = {"stage_name": name, "status": "PASS", "started_at": begin,
                      "completed_at": begin + timedelta(seconds=2)}
    if accepting:
        rows["acceptance"].update(status="STARTED", completed_at=None)
    return list(rows.values())


@pytest.mark.parametrize("lane", ["live", "full"])
def test_real_workflow_stages_and_dependency_contract_match(lane):
    source = Path(".github/workflows/production.yml").read_text()
    declared = set(re.findall(r"^\s+stage ([a-z0-9_]+) \d+ ", source, re.M))
    assert declared == set(FULL_STAGES) | set(LIVE_DEPENDENCIES)
    rows = stage_rows(lane=lane)
    assert validate_stages(rows, run_started_at=START, now=START + timedelta(minutes=5)) == lane
    if lane == "live":
        by_name = {r["stage_name"]: r for r in rows}
        assert by_name["v4_live"]["started_at"] == by_name["sector_rotation"]["started_at"]


def test_missing_producer_cannot_be_hidden_by_passed_remaining_stages():
    rows = [r for r in stage_rows() if r["stage_name"] != "v3_live"]
    with pytest.raises(RuntimeError, match="STAGE_SET: missing=v3_live"):
        validate_stages(rows, run_started_at=START, now=START + timedelta(minutes=5))


@pytest.mark.parametrize("status", ["STARTED", "FAILED"])
def test_nonpassing_producer_blocks_acceptance(status):
    rows = stage_rows(accepting=True)
    next(r for r in rows if r["stage_name"] == "sector_rotation")["status"] = status
    with pytest.raises(RuntimeError, match="STAGE_FAILED: sector_rotation"):
        validate_stages(rows, run_started_at=START, now=START + timedelta(minutes=5), acceptance_running=True)


def test_parallel_lane_rejects_consumer_starting_before_dependency_commits():
    rows = stage_rows()
    next(r for r in rows if r["stage_name"] == "momentum")["started_at"] = START
    with pytest.raises(RuntimeError, match="DEPENDENCY: sector_rotation->momentum"):
        validate_stages(rows, run_started_at=START, now=START + timedelta(minutes=5))


def test_only_running_acceptance_can_check_itself_and_not_publish_yet():
    rows = stage_rows(accepting=True)
    assert validate_stages(rows, run_started_at=START, now=START + timedelta(minutes=5), acceptance_running=True) == "live"
    with pytest.raises(RuntimeError, match="STAGE_FAILED: acceptance"):
        validate_stages(rows, run_started_at=START, now=START + timedelta(minutes=5))


@pytest.fixture
def real_run():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("requires isolated PostgreSQL")
    cp.migrate()
    rid = cp.start_run("audit-test")
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp()")
        start = cur.fetchone()[0] - timedelta(minutes=5)
        cur.execute("UPDATE pipeline_run SET started_at=%s WHERE pipeline_run_id=%s", (start, rid))
        rows = stage_rows(start, accepting=True)
        cur.executemany("""INSERT INTO pipeline_stage(pipeline_run_id,stage_name,stage_order,status,started_at,completed_at)
                            VALUES (%s,%s,%s,%s,%s,%s)""",
                        [(rid, r["stage_name"], i, r["status"], r["started_at"], r["completed_at"])
                         for i, r in enumerate(rows)])
    anchor = next(r["started_at"] for r in rows if r["stage_name"] == "warehouse_gate") + timedelta(seconds=1)
    live_tickers=["AAPL","MSFT"]
    for name in REQUIRED_DATASETS:
        if name == "warehouse_snapshot":
            records=[{"status":"PASS","production_run_id":rid,"as_of_utc":anchor.isoformat()}]
        elif name == "live_universe":
            records=[{"ticker":ticker} for ticker in live_tickers]
        else:
            records=[]
        cp.write_dataset(name,pd.DataFrame(records),entity_key=None,run_id=rid)
    from scanner.catalyst_warehouse import ingest_catalyst_batch
    from scanner.bitemporal_warehouse import start_run,finish_run
    for provider in ("YAHOO_NEWS","SEC_EDGAR","ALPHA_VANTAGE"):
        for ticker in live_tickers:
            warehouse_run=start_run(provider,"CATALYST_CONTEXT",{"ticker":ticker,"fixture":True})
            ingest_catalyst_batch(provider=provider,ticker=ticker,warehouse_run_id=warehouse_run,events=[])
            finish_run(warehouse_run,"AVAILABLE",{"events":0,"fixture":True})
    return rid


@pytest.mark.postgres_integration
def test_acceptance_reads_real_postgres_run_and_rejects_missing_or_tampered_data(real_run):
    result = audit_current_run(real_run)
    assert result["production_run_id"] == real_run
    assert result["datasets"] == len(REQUIRED_DATASETS)
    # Deliberate corruption is confined to this isolated test run/database.
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE dataset_version SET content_hash='tampered' WHERE pipeline_run_id=%s AND dataset_name='v4_live_snapshot'", (real_run,))
    with pytest.raises(RuntimeError, match="DATASET_CORRUPT: v4_live_snapshot"):
        audit_current_run(real_run)


@pytest.mark.postgres_integration
def test_missing_stage_blocks_acceptance_and_final_transaction_even_with_all_datasets(real_run, monkeypatch):
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM pipeline_stage WHERE pipeline_run_id=%s AND stage_name='sector_rotation'", (real_run,))
    with pytest.raises(RuntimeError, match="STAGE_SET: missing=sector_rotation"):
        audit_current_run(real_run)
    from scanner import warehouse_gate
    # This test isolates the transaction's stage guard, not the independently tested market gate.
    monkeypatch.setattr(warehouse_gate, "validate_publication_freshness", lambda rid: None)
    with pytest.raises(RuntimeError, match="STAGE_SET: missing=sector_rotation"):
        cp.publish("production", REQUIRED_DATASETS, run_id=real_run)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM publication_snapshot WHERE pipeline_run_id=%s", (real_run,))
        assert cur.fetchone()[0] == 0


@pytest.mark.postgres_integration
def test_wrong_snapshot_run_is_rejected_despite_valid_content_hash(real_run):
    snapshot = cp.read_dataset("warehouse_snapshot", run_id=real_run)
    snapshot["production_run_id"] = "another-run"
    cp.write_dataset("warehouse_snapshot", snapshot, entity_key=None, run_id=real_run)
    with pytest.raises(RuntimeError, match="SNAPSHOT_PROVENANCE"):
        audit_current_run(real_run)
