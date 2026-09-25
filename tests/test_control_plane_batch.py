from contextlib import contextmanager
import os
import uuid

import pandas as pd
import pytest

from scanner import control_plane as cp


@pytest.mark.postgres_integration  # Exercise the real reader, bypassing the autouse in-memory API.
def test_consumer_reads_metadata_and_rows_in_one_statement(monkeypatch):
    records = [{"ticker": "B", "price": 11.5}, {"ticker": "A", "price": None}]
    calls = []
    connections = []

    class Cursor:
        def execute(self, sql, params):
            calls.append((sql, params))

        def fetchall(self):
            return [(cp._hash(records), len(records), row) for row in records]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        connections.append(1)
        yield Connection()

    monkeypatch.setattr(cp, "_connect", connect)
    result = cp.read_dataset("prices", run_id="fixed-run")
    pd.testing.assert_frame_equal(result, pd.DataFrame(records))
    assert len(connections) == len(calls) == 1
    assert calls[0][1] == ("fixed-run", "prices")


def test_publication_payload_batch_preserves_empty_datasets_and_order():
    calls = []

    class Cursor:
        def execute(self, sql, params):
            calls.append(params)

        def fetchall(self):
            return [(12, {"ticker": "B"}), (12, {"ticker": "A"}), (18, {"value": None})]

    result = cp._read_version_records(Cursor(), [12, 16, 18])
    assert result == {12: [{"ticker": "B"}, {"ticker": "A"}], 16: [], 18: [{"value": None}]}
    assert calls == [([12, 16, 18],)]


@pytest.fixture
def database():
    if not os.getenv("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    cp.migrate()
    return f"batch-test-{uuid.uuid4()}"


def finish(run_id):
    cp.stage_started("integration", 1, run_id=run_id)
    cp.stage_finished("integration", run_id=run_id)


@pytest.mark.postgres_integration
def test_seed_and_publish_exact_versions_empty_rows_metadata_and_rollback(database):
    original = cp.start_run(database)
    rows = pd.DataFrame([{"ticker": "Z", "score": 1.5}, {"ticker": "A", "score": 0.0}])
    source = cp.write_dataset("prices", rows, schema_version=7, metadata={"source": "observed"}, run_id=original)
    cp.write_dataset("empty", pd.DataFrame(), run_id=original)
    finish(original)
    first = cp.publish(database, ["prices", "empty"], run_id=original)

    target = cp.start_run(database)
    assert cp.seed_from_publication(database, ["empty", "prices", "prices"], run_id=target) == 2
    pd.testing.assert_frame_equal(cp.read_dataset("prices", run_id=target), rows, check_like=True)
    assert cp.read_dataset("empty", run_id=target).empty
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT schema_version,row_count,content_hash,metadata FROM dataset_version WHERE pipeline_run_id=%s AND dataset_name='prices'", (target,))
        assert cur.fetchone() == (7, 2, source["content_hash"], {"source": "observed"})
    # Seeding a missing dependency copies nothing, and never moves the head.
    missing = cp.start_run(database)
    with pytest.raises(RuntimeError, match="SEED_BLOCKED: missing=absent"):
        cp.seed_from_publication(database, ["prices", "absent"], run_id=missing)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM dataset_version WHERE pipeline_run_id=%s", (missing,))
        assert cur.fetchone()[0] == 0
    assert cp.publication_info(database)["publication_snapshot_id"] == first["publication_snapshot_id"]
    finish(target)
    cp.publish(database, ["empty", "prices"], run_id=target)
    assert cp.publication_info(database)["production_run_id"] == target
    pd.testing.assert_frame_equal(cp.read_dataset("prices", published_mode=database), rows, check_like=True)
    pd.testing.assert_frame_equal(cp.read_dataset("prices", run_id=original), rows, check_like=True)


@pytest.mark.postgres_integration
@pytest.mark.parametrize("tamper", ["UPDATE dataset_row SET payload='{}'::jsonb WHERE dataset_version_id=%s", "DELETE FROM dataset_row WHERE dataset_version_id=%s"])
def test_batched_publication_and_read_reject_corruption_without_head_change(database, tamper):
    original = cp.start_run(database)
    cp.write_dataset("alpha", pd.DataFrame([{"ticker": "SAFE"}]), run_id=original)
    finish(original)
    cp.publish(database, ["alpha"], run_id=original)
    target = cp.start_run(database)
    cp.seed_from_publication(database, ["alpha"], run_id=target)
    version = cp.write_dataset("omega", pd.DataFrame([{"ticker": "TAMPER"}]), run_id=target)
    finish(target)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute(tamper, (version["dataset_version_id"],))
    with pytest.raises(RuntimeError, match="DATASET_CORRUPT: omega"):
        cp.read_dataset("omega", run_id=target)
    with pytest.raises(RuntimeError, match="corrupt=omega"):
        cp.publish(database, ["alpha", "omega"], run_id=target)
    assert cp.publication_info(database)["production_run_id"] == original


@pytest.mark.postgres_integration
def test_empty_and_missing_datasets_keep_distinct_contracts(database):
    run = cp.start_run(database)
    cp.write_dataset("empty", pd.DataFrame(), run_id=run)
    assert cp.read_dataset("empty", run_id=run).empty
    assert cp.read_dataset("absent", run_id=run, required=False).empty
    with pytest.raises(RuntimeError, match="DATASET_UNAVAILABLE: absent"):
        cp.read_dataset("absent", run_id=run)
