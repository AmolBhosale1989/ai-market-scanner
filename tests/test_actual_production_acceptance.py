from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re

import pandas as pd
import pytest

from scanner import control_plane as cp
from scanner.production_acceptance import audit_current_run
from scanner.production_telemetry import REQUIRED_DATASETS
from scanner.stage_contract import FULL_STAGES, FULL_DEPENDENCIES, LIVE_DEPENDENCIES, validate_stages


START = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)


def stage_rows(start=START, lane="live", accepting=False):
    deps = LIVE_DEPENDENCIES if lane == "live" else FULL_DEPENDENCIES
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
def real_run(monkeypatch):
    if not os.getenv("DATABASE_URL"):
        pytest.skip("requires isolated PostgreSQL")
    from scanner.consumer_snapshot import capture_boundary
    from scanner.catalyst_warehouse import ingest_catalyst_batch
    from scanner.bitemporal_warehouse import start_run, finish_run
    from scanner.catalogue_snapshot import CATALOGUE_DATASETS
    cp.migrate()
    rid = cp.start_run("audit-test")
    live_tickers = ["AAPL", "MSFT"]
    with cp._connect() as conn, conn.cursor() as cur:
        for ticker in live_tickers:
            cur.execute("INSERT INTO instrument(canonical_symbol) VALUES (%s) ON CONFLICT DO NOTHING", (ticker,))
        cur.execute("SELECT clock_timestamp()")
        checked_at = cur.fetchone()[0]
    for provider in ("YAHOO_NEWS", "SEC_EDGAR", "ALPHA_VANTAGE"):
        for ticker in live_tickers:
            wr = start_run(provider, "CATALYST_CONTEXT", {"ticker": ticker, "fixture": True})
            ingest_catalyst_batch(provider=provider, ticker=ticker, warehouse_run_id=wr,
                                  events=[], checked_at=checked_at)
            finish_run(wr, "AVAILABLE", {"events": 0})
    for name in CATALOGUE_DATASETS:
        cp.write_dataset(name, pd.DataFrame({"ticker": live_tickers}), run_id=rid)
    # Evidence and catalogues COMMIT before the real visibility boundary.
    anchor, visibility = capture_boundary(run_id=rid)
    cp.write_dataset("warehouse_snapshot", pd.DataFrame([{
        "status": "PASS", "production_run_id": rid, "as_of_utc": anchor.isoformat(),
        "pg_snapshot": visibility,
    }]), entity_key=None, run_id=rid)
    monkeypatch.setenv("PRODUCTION_RUN_ID", rid)
    monkeypatch.setenv("WAREHOUSE_CONSUMER_SNAPSHOT", "1")
    for name in REQUIRED_DATASETS:
        if name not in CATALOGUE_DATASETS and name != "warehouse_snapshot":
            cp.write_dataset(name, pd.DataFrame(), entity_key=None, run_id=rid)
    # Compress fixture stage durations to microseconds around the captured gate.
    rows = stage_rows(START, accepting=True)
    gate = next(row for row in rows if row["stage_name"] == "warehouse_gate")
    midpoint = gate["started_at"] + (gate["completed_at"] - gate["started_at"]) / 2
    start = anchor.to_pydatetime() - (midpoint - START) / 1_000_000
    for row in rows:
        for key in ("started_at", "completed_at"):
            if row[key] is not None:
                row[key] = start + (row[key] - START) / 1_000_000
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE pipeline_run SET started_at=%s WHERE pipeline_run_id=%s", (start, rid))
        cur.executemany("""INSERT INTO pipeline_stage
            (pipeline_run_id,stage_name,stage_order,status,started_at,completed_at)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            [(rid, r["stage_name"], i, r["status"], r["started_at"], r["completed_at"])
             for i, r in enumerate(rows)])
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


@pytest.mark.postgres_integration
@pytest.mark.parametrize("with_optional", [False, True])
def test_optional_catalyst_acceptance_publication_and_dashboard(real_run, monkeypatch, with_optional):
    from scanner.production_telemetry import OPTIONAL_DATASETS, finalize
    from scanner.dashboard_data import read_dashboard_datasets
    from scanner import warehouse_gate
    if with_optional:
        for name in OPTIONAL_DATASETS:
            cp.write_dataset(name, pd.DataFrame([{"status": "unavailable"}]),
                             entity_key=None, run_id=real_run)
    assert audit_current_run(real_run)["status"] == "PASS"
    # Price freshness is independently tested; retain real stage/hash/pointer checks.
    monkeypatch.setattr(warehouse_gate, "validate_publication_freshness", lambda rid: None)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE pipeline_stage SET status='PASS', completed_at=clock_timestamp() "
                    "WHERE pipeline_run_id=%s AND stage_name='acceptance'", (real_run,))
    result = finalize()
    names = {row["name"] for row in result["datasets"]}
    assert set(REQUIRED_DATASETS) <= names
    assert (set(OPTIONAL_DATASETS) <= names) == with_optional
    assert cp.publication_info("production")["pipeline_run_id"] == real_run
    dashboard = read_dashboard_datasets(OPTIONAL_DATASETS, run_id=real_run)
    for name in OPTIONAL_DATASETS:
        frame, source = dashboard[name]
        assert frame.empty == (not with_optional)
        assert source == ("postgresql" if with_optional else "unavailable")
    seeded_run = cp.start_run("optional-seed-test")
    assert cp.seed_from_publication("production", REQUIRED_DATASETS, run_id=seeded_run,
                                    optional_datasets=OPTIONAL_DATASETS) == len(names)


@pytest.mark.postgres_integration
def test_optional_catalyst_corruption_still_blocks_acceptance_and_publication(real_run, monkeypatch):
    from scanner.production_telemetry import finalize
    from scanner import warehouse_gate
    cp.write_dataset("event_status", pd.DataFrame(), entity_key=None, run_id=real_run)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE dataset_version SET content_hash='tampered' "
                    "WHERE pipeline_run_id=%s AND dataset_name='event_status'", (real_run,))
    with pytest.raises(RuntimeError, match="DATASET_CORRUPT: event_status"):
        audit_current_run(real_run)
    monkeypatch.setattr(warehouse_gate, "validate_publication_freshness", lambda rid: None)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE pipeline_stage SET status='PASS', completed_at=clock_timestamp() "
                    "WHERE pipeline_run_id=%s AND stage_name='acceptance'", (real_run,))
    with pytest.raises(RuntimeError, match="corrupt=event_status"):
        finalize()


@pytest.mark.postgres_integration
def test_missing_core_dataset_remains_fatal(real_run):
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE dataset_version SET status='FAILED' "
                    "WHERE pipeline_run_id=%s AND dataset_name='recommended_trades'", (real_run,))
    with pytest.raises(RuntimeError, match="ACCEPTANCE_DATASET_MISSING: recommended_trades"):
        audit_current_run(real_run)
