from __future__ import annotations

import os
import uuid

import pandas as pd
import pytest

import scanner.control_plane as control_plane
from scanner.bitemporal_warehouse import _connect


pytestmark = pytest.mark.postgres_integration


def _require_database() -> None:
    if not os.getenv("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")


def _complete_stage(run_id: str, name: str = "integration") -> None:
    control_plane.stage_started(name, 10, run_id=run_id)
    control_plane.stage_finished(name, run_id=run_id)


def _write_snapshot(run_id: str, as_of: str) -> None:
    control_plane.write_dataset(
        "warehouse_snapshot",
        pd.DataFrame([{"status": "PASS", "as_of_utc": as_of}]),
        entity_key=None,
        run_id=run_id,
    )


def test_atomic_publication_fail_closed_and_event_idempotency():
    _require_database()
    control_plane.migrate()
    mode = f"integration-{uuid.uuid4()}"
    as_of = "2026-09-18T20:00:00+00:00"

    first = control_plane.start_run(mode, source_commit="integration-first")
    _write_snapshot(first, as_of)
    control_plane.write_dataset(
        "alpha",
        pd.DataFrame([{"ticker": "TEST", "score": 91.5}]),
        run_id=first,
    )
    _complete_stage(first)
    published = control_plane.publish(mode, ("warehouse_snapshot", "alpha"), run_id=first)

    assert published["pipeline_run_id"] == first
    assert control_plane.publication_info(mode)["production_run_id"] == first
    assert control_plane.publication_info(mode)["warehouse_as_of_utc"] == as_of
    frame = control_plane.read_dataset("alpha", published_mode=mode)
    assert frame.to_dict("records") == [{"score": 91.5, "ticker": "TEST"}]

    missing = control_plane.start_run(mode, source_commit="integration-missing")
    _write_snapshot(missing, as_of)
    _complete_stage(missing)
    with pytest.raises(RuntimeError, match="missing=alpha"):
        control_plane.publish(mode, ("warehouse_snapshot", "alpha"), run_id=missing)
    assert control_plane.publication_info(mode)["production_run_id"] == first

    unfinished = control_plane.start_run(mode, source_commit="integration-unfinished")
    _write_snapshot(unfinished, as_of)
    control_plane.write_dataset("alpha", pd.DataFrame([{"ticker": "TEST2"}]), run_id=unfinished)
    control_plane.stage_started("integration", 10, run_id=unfinished)
    with pytest.raises(RuntimeError, match="stages=integration"):
        control_plane.publish(mode, ("warehouse_snapshot", "alpha"), run_id=unfinished)
    assert control_plane.publication_info(mode)["production_run_id"] == first

    corrupt = control_plane.start_run(mode, source_commit="integration-corrupt")
    _write_snapshot(corrupt, as_of)
    version = control_plane.write_dataset(
        "alpha", pd.DataFrame([{"ticker": "TEST3"}]), run_id=corrupt
    )
    _complete_stage(corrupt)
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """UPDATE dataset_row SET payload='{"ticker":"TAMPERED"}'::jsonb
               WHERE dataset_version_id=%s AND row_ordinal=0""",
            (version["dataset_version_id"],),
        )
    with pytest.raises(RuntimeError, match="corrupt=alpha"):
        control_plane.publish(mode, ("warehouse_snapshot", "alpha"), run_id=corrupt)
    assert control_plane.publication_info(mode)["production_run_id"] == first

    namespace = f"integration-{uuid.uuid4()}"
    event = {"event_id": "event-1", "observed_at_utc": as_of, "value": 7}
    assert control_plane.append_events(namespace, [event], run_id=first) == 1
    assert control_plane.append_events(namespace, [event], run_id=first) == 0
    assert control_plane.read_events(namespace) == [event]

    assert control_plane.append_state(namespace, "state", {"value": 1}, run_id=first) == 1
    assert control_plane.append_state(namespace, "state", {"value": 2}, run_id=first) == 2
    assert control_plane.read_state(namespace, "state") == {"value": 2}


def test_migrations_are_versioned_idempotent_and_fail_closed(tmp_path):
    _require_database()
    control_plane.migrate()
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT migration_name,checksum FROM schema_migration
               WHERE migration_name IN ('001_bitemporal_warehouse.sql',
                                        '002_postgres_control_plane.sql')
               ORDER BY migration_name"""
        )
        before = cursor.fetchall()

    control_plane.migrate()
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT migration_name,checksum FROM schema_migration
               WHERE migration_name IN ('001_bitemporal_warehouse.sql',
                                        '002_postgres_control_plane.sql')
               ORDER BY migration_name"""
        )
        after = cursor.fetchall()

    assert [row[0] for row in before] == [
        "001_bitemporal_warehouse.sql",
        "002_postgres_control_plane.sql",
    ]
    assert after == before

    suffix = uuid.uuid4().hex
    good_name = f"900_{suffix}_good.sql"
    bad_name = f"901_{suffix}_bad.sql"
    (tmp_path / good_name).write_text(
        f"CREATE TABLE migration_probe_{suffix} (id integer PRIMARY KEY);"
    )
    (tmp_path / bad_name).write_text(
        f"INSERT INTO migration_probe_{suffix}(missing_column) VALUES (1);"
    )

    with pytest.raises(Exception):
        control_plane.migrate(tmp_path)

    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT migration_name FROM schema_migration
               WHERE migration_name IN (%s,%s) ORDER BY migration_name""",
            (good_name, bad_name),
        )
        recorded = [row[0] for row in cursor.fetchall()]

    assert recorded == [good_name]
