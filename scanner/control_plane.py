from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .bitemporal_warehouse import _connect


def _json_default(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    raise TypeError(type(value).__name__)


def _clean(value):
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _clean(value.item())
    # PostgreSQL JSONB stores -0.0 as the mathematically equivalent 0.0.
    # Canonicalize signed zero before hashing so the integrity digest is stable
    # across the database round trip.  All other finite values remain exact.
    if isinstance(value, float) and value == 0.0:
        return 0.0
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _canonical(value) -> str:
    return json.dumps(_clean(value), sort_keys=True, separators=(",", ":"), default=_json_default)


def _hash(value) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def current_run_id(required: bool = True) -> str:
    value = os.getenv("PRODUCTION_RUN_ID", "").strip()
    if not value and required:
        raise RuntimeError("CONTROL_PLANE_RUN_REQUIRED: PRODUCTION_RUN_ID is not configured")
    return value


def _migration_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(sql_dir: Path | None = None) -> None:
    """Apply each immutable migration once, committing only after its ledger row.

    A session advisory lock serializes migration attempts outside GitHub Actions.
    Every file is its own transaction: a failed migration is rolled back and is
    never recorded as applied, so production initialization remains fail-closed.
    """
    root = sql_dir or Path(__file__).resolve().parents[1] / "sql"
    paths = sorted(root.glob("*.sql"))
    with _connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s,0))",
                    ("market-hunt-schema-migrations",),
                )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migration (
                           migration_name TEXT PRIMARY KEY,
                           checksum TEXT NOT NULL,
                           applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                       )"""
                )
            conn.commit()

            for path in paths:
                checksum = _migration_checksum(path)
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT checksum FROM schema_migration WHERE migration_name=%s",
                            (path.name,),
                        )
                        row = cur.fetchone()
                        if row is not None:
                            if row[0] != checksum:
                                raise RuntimeError(
                                    f"CONTROL_PLANE_MIGRATION_CHECKSUM_MISMATCH: {path.name}"
                                )
                            conn.rollback()
                            continue
                        cur.execute(path.read_text())
                        cur.execute(
                            """INSERT INTO schema_migration(migration_name,checksum)
                               VALUES (%s,%s)""",
                            (path.name, checksum),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        finally:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
                    ("market-hunt-schema-migrations",),
                )
            conn.commit()


def start_run(mode: str, *, source_commit: str = "", warehouse_as_of: datetime | None = None) -> str:
    run_id = str(uuid.uuid4())
    as_of = warehouse_as_of or datetime.now(timezone.utc)
    source = source_commit or os.getenv("GITHUB_SHA", "") or "LOCAL"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO pipeline_run
               (pipeline_run_id,mode,source_commit,warehouse_as_of,status)
               VALUES (%s,%s,%s,%s,'STARTED')""",
            (run_id, mode, source, as_of),
        )
    return run_id


def fail_run(message: str, run_id: str | None = None) -> None:
    rid = run_id or current_run_id()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE pipeline_run SET status='FAILED',completed_at=now(),error_detail=%s
               WHERE pipeline_run_id=%s AND status<>'PUBLISHED'""",
            (message, rid),
        )


def stage_started(stage_name: str, stage_order: int, run_id: str | None = None) -> None:
    rid = run_id or current_run_id()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO pipeline_stage
               (pipeline_run_id,stage_name,stage_order,status)
               VALUES (%s,%s,%s,'STARTED')
               ON CONFLICT (pipeline_run_id,stage_name) DO UPDATE SET
                 stage_order=EXCLUDED.stage_order,status='STARTED',started_at=now(),completed_at=NULL""",
            (rid, stage_name, stage_order),
        )


def stage_finished(
    stage_name: str,
    *,
    status: str = "PASS",
    input_hash: str = "",
    output_hash: str = "",
    row_count: int | None = None,
    detail: Mapping | None = None,
    run_id: str | None = None,
) -> None:
    rid = run_id or current_run_id()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE pipeline_stage SET status=%s,completed_at=now(),input_hash=%s,
                 output_hash=%s,row_count=%s,detail=%s::jsonb
               WHERE pipeline_run_id=%s AND stage_name=%s""",
            (status, input_hash or None, output_hash or None, row_count,
             _canonical(detail or {}), rid, stage_name),
        )
        if cur.rowcount != 1:
            raise RuntimeError(f"CONTROL_PLANE_STAGE_UNKNOWN: {stage_name}")


def write_dataset(
    dataset_name: str,
    frame: pd.DataFrame,
    *,
    entity_key: str | None = "ticker",
    schema_version: int = 1,
    metadata: Mapping | None = None,
    run_id: str | None = None,
) -> dict:
    rid = run_id or current_run_id()
    records = [_clean(row) for row in frame.to_dict(orient="records")]
    digest = _hash(records)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO dataset_version
               (pipeline_run_id,dataset_name,schema_version,status,metadata)
               VALUES (%s,%s,%s,'WRITING',%s::jsonb)
               ON CONFLICT (pipeline_run_id,dataset_name) DO UPDATE SET
                 schema_version=EXCLUDED.schema_version,status='WRITING',row_count=0,
                 content_hash=NULL,completed_at=NULL,metadata=EXCLUDED.metadata
               RETURNING dataset_version_id""",
            (rid, dataset_name, schema_version, _canonical(metadata or {})),
        )
        version_id = int(cur.fetchone()[0])
        cur.execute("DELETE FROM dataset_row WHERE dataset_version_id=%s", (version_id,))
        if records:
            cur.executemany(
                """INSERT INTO dataset_row
                   (dataset_version_id,row_ordinal,entity_key,payload)
                   VALUES (%s,%s,%s,%s::jsonb)""",
                [
                    (version_id, index, str(row.get(entity_key, "")) if entity_key else None, _canonical(row))
                    for index, row in enumerate(records)
                ],
            )
        cur.execute(
            """UPDATE dataset_version SET status='AVAILABLE',row_count=%s,
                 content_hash=%s,completed_at=now() WHERE dataset_version_id=%s""",
            (len(records), digest, version_id),
        )
    return {"dataset_version_id": version_id, "row_count": len(records), "content_hash": digest}


def _dataset_version(dataset_name: str, run_id: str | None, mode: str | None):
    if bool(run_id) == bool(mode):
        raise ValueError("specify exactly one of run_id or mode")
    if run_id:
        sql = """SELECT dataset_version_id,pipeline_run_id,content_hash,row_count
                 FROM dataset_version WHERE pipeline_run_id=%s AND dataset_name=%s
                   AND status='AVAILABLE'"""
        params = (run_id, dataset_name)
    else:
        sql = """SELECT dv.dataset_version_id,dv.pipeline_run_id,dv.content_hash,dv.row_count
                 FROM publication_head ph
                 JOIN publication_snapshot ps ON ps.publication_snapshot_id=ph.publication_snapshot_id
                 JOIN publication_dataset pdx ON pdx.publication_snapshot_id=ps.publication_snapshot_id
                 JOIN dataset_version dv ON dv.dataset_version_id=pdx.dataset_version_id
                 WHERE ph.mode=%s AND pdx.dataset_name=%s AND ps.status='PUBLISHED'"""
        params = (mode, dataset_name)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def read_dataset(
    dataset_name: str,
    *,
    run_id: str | None = None,
    published_mode: str | None = None,
    required: bool = True,
) -> pd.DataFrame:
    rid = run_id or (current_run_id(False) if not published_mode else None)
    if not rid and not published_mode:
        if required:
            raise RuntimeError("CONTROL_PLANE_RUN_REQUIRED: no current run or published mode")
        return pd.DataFrame()
    version = _dataset_version(dataset_name, rid, published_mode)
    if version is None:
        if required:
            raise RuntimeError(f"CONTROL_PLANE_DATASET_UNAVAILABLE: {dataset_name}")
        return pd.DataFrame()
    version_id, _, expected_hash, expected_rows = version
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM dataset_row WHERE dataset_version_id=%s ORDER BY row_ordinal",
            (version_id,),
        )
        records = [row[0] for row in cur.fetchall()]
    if len(records) != int(expected_rows) or _hash(records) != expected_hash:
        raise RuntimeError(f"CONTROL_PLANE_DATASET_CORRUPT: {dataset_name}")
    return pd.DataFrame(records)


def append_state(namespace: str, document_key: str, payload, run_id: str | None = None) -> int:
    rid = run_id or current_run_id(False) or None
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"{namespace}\x1f{document_key}",),
        )
        cur.execute(
            """SELECT COALESCE(max(revision),0)+1 FROM state_document
               WHERE namespace=%s AND document_key=%s""",
            (namespace, document_key),
        )
        revision = int(cur.fetchone()[0])
        cur.execute(
            """INSERT INTO state_document(namespace,document_key,revision,pipeline_run_id,payload)
               VALUES (%s,%s,%s,%s,%s::jsonb)""",
            (namespace, document_key, revision, rid, _canonical(payload)),
        )
    return revision


def read_state(namespace: str, document_key: str, default=None):
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT payload FROM state_document WHERE namespace=%s AND document_key=%s
               ORDER BY revision DESC LIMIT 1""",
            (namespace, document_key),
        )
        row = cur.fetchone()
    return row[0] if row else default


def append_events(
    namespace: str,
    events: Iterable[Mapping],
    *,
    key_field: str = "event_id",
    observed_field: str = "observed_at_utc",
    run_id: str | None = None,
) -> int:
    """Insert immutable events once; duplicate event keys are harmless retries."""
    rid = run_id or current_run_id(False) or None
    rows = []
    for event in events:
        payload = _clean(event)
        key = str(payload.get(key_field, "")).strip()
        if not key:
            raise RuntimeError(f"CONTROL_PLANE_EVENT_KEY_MISSING: {namespace}.{key_field}")
        rows.append((namespace, key, rid, payload.get(observed_field) or None, _canonical(payload)))
    if not rows:
        return 0
    inserted = 0
    with _connect() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """INSERT INTO event_record(namespace,event_key,pipeline_run_id,observed_at,payload)
                   VALUES (%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (namespace,event_key) DO NOTHING RETURNING 1""",
                row,
            )
            inserted += int(cur.fetchone() is not None)
    return inserted


def read_events(namespace: str, *, limit: int | None = None) -> list[dict]:
    sql = """SELECT payload FROM event_record WHERE namespace=%s
             ORDER BY observed_at NULLS LAST,created_at,event_key"""
    params: list = [namespace]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(int(limit))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [row[0] for row in cur.fetchall()]


def write_record(dataset_name: str, payload: Mapping, *, schema_version: int = 1,
                 run_id: str | None = None) -> dict:
    return write_dataset(dataset_name, pd.DataFrame([_clean(payload)]), entity_key=None,
                         schema_version=schema_version, run_id=run_id)


def read_record(dataset_name: str, *, run_id: str | None = None,
                published_mode: str | None = None, required: bool = True) -> dict:
    frame = read_dataset(dataset_name, run_id=run_id, published_mode=published_mode,
                         required=required)
    return frame.iloc[-1].to_dict() if not frame.empty else {}


def seed_from_publication(mode: str, dataset_names: Iterable[str],
                          run_id: str | None = None) -> int:
    """Copy an immutable published snapshot into a new run before selective refresh."""
    rid = run_id or current_run_id()
    names = tuple(dict.fromkeys(dataset_names))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT pdx.dataset_name,dv.dataset_version_id,dv.schema_version,
                      dv.row_count,dv.content_hash,dv.metadata
               FROM publication_head ph
               JOIN publication_snapshot ps ON ps.publication_snapshot_id=ph.publication_snapshot_id
               JOIN publication_dataset pdx ON pdx.publication_snapshot_id=ps.publication_snapshot_id
               JOIN dataset_version dv ON dv.dataset_version_id=pdx.dataset_version_id
               WHERE ph.mode=%s AND ps.status='PUBLISHED' AND pdx.dataset_name=ANY(%s)""",
            (mode, list(names)),
        )
        source = {row[0]: row[1:] for row in cur.fetchall()}
        missing = sorted(set(names) - set(source))
        if missing:
            raise RuntimeError("CONTROL_PLANE_SEED_BLOCKED: missing=" + ",".join(missing))
        for name in names:
            source_id, schema_version, row_count, digest, metadata = source[name]
            cur.execute(
                """INSERT INTO dataset_version
                   (pipeline_run_id,dataset_name,schema_version,status,row_count,content_hash,completed_at,metadata)
                   VALUES (%s,%s,%s,'AVAILABLE',%s,%s,now(),%s::jsonb)
                   RETURNING dataset_version_id""",
                (rid, name, schema_version, row_count, digest, _canonical(metadata)),
            )
            target_id = int(cur.fetchone()[0])
            cur.execute(
                """INSERT INTO dataset_row(dataset_version_id,row_ordinal,entity_key,payload)
                   SELECT %s,row_ordinal,entity_key,payload FROM dataset_row
                   WHERE dataset_version_id=%s""",
                (target_id, source_id),
            )
    return len(names)


def record_health(module_name: str, status: str, metrics: Mapping | None = None,
                  detail: str = "", session_date: date | None = None,
                  run_id: str | None = None) -> None:
    rid = run_id or current_run_id()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO health_observation
               (pipeline_run_id,module_name,status,session_date,metrics,detail)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
            (rid, module_name, status, session_date, _canonical(metrics or {}), detail or None),
        )


def publish(mode: str, required_datasets: Iterable[str], run_id: str | None = None) -> dict:
    rid = run_id or current_run_id()
    required = tuple(dict.fromkeys(required_datasets))
    if not required:
        raise RuntimeError("CONTROL_PLANE_PUBLICATION_EMPTY")
    if mode == "production":
        from .warehouse_gate import validate_publication_freshness
        validate_publication_freshness(rid)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM pipeline_run WHERE pipeline_run_id=%s FOR UPDATE", (rid,))
        row = cur.fetchone()
        if row is None or row[0] not in {"STARTED", "VALIDATING"}:
            raise RuntimeError(f"CONTROL_PLANE_RUN_NOT_PUBLISHABLE: {rid}")
        cur.execute(
            """SELECT dataset_name,dataset_version_id,content_hash,row_count
               FROM dataset_version WHERE pipeline_run_id=%s AND status='AVAILABLE'
                 AND dataset_name=ANY(%s)""",
            (rid, list(required)),
        )
        versions = {row[0]: row[1:] for row in cur.fetchall()}
        missing = sorted(set(required) - set(versions))
        if missing:
            raise RuntimeError("CONTROL_PLANE_PUBLICATION_BLOCKED: missing=" + ",".join(missing))
        failed_stage_sql = """SELECT stage_name FROM pipeline_stage
                              WHERE pipeline_run_id=%s AND status<>'PASS'"""
        cur.execute(failed_stage_sql, (rid,))
        failed = [row[0] for row in cur.fetchall()]
        if failed:
            raise RuntimeError("CONTROL_PLANE_PUBLICATION_BLOCKED: stages=" + ",".join(failed))
        cur.execute("SELECT count(*) FROM pipeline_stage WHERE pipeline_run_id=%s", (rid,))
        if int(cur.fetchone()[0]) == 0:
            raise RuntimeError("CONTROL_PLANE_PUBLICATION_BLOCKED: no validated stages")
        if "warehouse_snapshot" in versions:
            cur.execute(
                """SELECT payload->>'as_of_utc' FROM dataset_row
                   WHERE dataset_version_id=%s ORDER BY row_ordinal LIMIT 1""",
                (versions["warehouse_snapshot"][0],),
            )
            as_of_row = cur.fetchone()
            if not as_of_row or not as_of_row[0]:
                raise RuntimeError("CONTROL_PLANE_PUBLICATION_BLOCKED: warehouse as-of missing")
            cur.execute(
                """UPDATE pipeline_run SET warehouse_as_of=%s::timestamptz
                   WHERE pipeline_run_id=%s""",
                (as_of_row[0], rid),
            )
        signal_records = {}
        for name, (version_id, expected_hash, expected_rows) in versions.items():
            cur.execute(
                "SELECT payload FROM dataset_row WHERE dataset_version_id=%s ORDER BY row_ordinal",
                (version_id,),
            )
            records = [record[0] for record in cur.fetchall()]
            if name in {"momentum_signals", "rotation_leaders", "sector_rotation"}:
                signal_records[name] = records
            if len(records) != int(expected_rows) or _hash(records) != expected_hash:
                raise RuntimeError(f"CONTROL_PLANE_PUBLICATION_BLOCKED: corrupt={name}")
        manifest = [{"name": name, "version": versions[name][0], "hash": versions[name][1],
                     "rows": versions[name][2]} for name in sorted(required)]
        manifest_hash = _hash(manifest)
        cur.execute(
            """INSERT INTO publication_snapshot(pipeline_run_id,mode,status,manifest_hash,metadata)
               VALUES (%s,%s,'VALIDATING',%s,%s::jsonb)
               RETURNING publication_snapshot_id""",
            (rid, mode, manifest_hash, _canonical({"datasets": manifest})),
        )
        snapshot_id = int(cur.fetchone()[0])
        cur.executemany(
            """INSERT INTO publication_dataset
               (publication_snapshot_id,dataset_name,dataset_version_id) VALUES (%s,%s,%s)""",
            [(snapshot_id, name, versions[name][0]) for name in required],
        )
        if mode == "production":
            from .signal_freshness import signal_expiry_reason
            cur.execute("SELECT clock_timestamp()")
            reason = signal_expiry_reason(signal_records, now_utc=cur.fetchone()[0])
            if reason:
                raise RuntimeError("PUBLICATION_SIGNAL_FRESHNESS_BLOCKED: " + reason)
        cur.execute(
            """UPDATE publication_snapshot SET status='PUBLISHED',published_at=clock_timestamp()
               WHERE publication_snapshot_id=%s""", (snapshot_id,)
        )
        cur.execute(
            """INSERT INTO publication_head(mode,publication_snapshot_id) VALUES (%s,%s)
               ON CONFLICT (mode) DO UPDATE SET publication_snapshot_id=EXCLUDED.publication_snapshot_id,
                 updated_at=now()""", (mode, snapshot_id)
        )
        cur.execute(
            """UPDATE pipeline_run SET status='PUBLISHED',completed_at=now()
               WHERE pipeline_run_id=%s""", (rid,)
        )
    return {"pipeline_run_id": rid, "publication_snapshot_id": snapshot_id,
            "manifest_hash": manifest_hash, "datasets": manifest}


def publication_info(mode: str) -> dict:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ps.publication_snapshot_id,ps.pipeline_run_id,ps.mode,ps.status,
                      ps.published_at,ps.manifest_hash,pr.source_commit,pr.warehouse_as_of
               FROM publication_head ph
               JOIN publication_snapshot ps ON ps.publication_snapshot_id=ph.publication_snapshot_id
               JOIN pipeline_run pr ON pr.pipeline_run_id=ps.pipeline_run_id
               WHERE ph.mode=%s AND ps.status='PUBLISHED'""", (mode,)
        )
        row=cur.fetchone()
    if row is None:
        raise RuntimeError(f"CONTROL_PLANE_PUBLICATION_UNAVAILABLE: {mode}")
    keys=("publication_snapshot_id","production_run_id","mode","status","published_at_utc",
          "manifest_hash","source_commit","warehouse_as_of_utc")
    return {key:_clean(value) for key,value in zip(keys,row)}


def main():
    parser = argparse.ArgumentParser(description="PostgreSQL control plane")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--mode", required=True)
    sub.add_parser("migrate")
    fail = sub.add_parser("fail")
    fail.add_argument("--message", required=True)
    stage_begin = sub.add_parser("stage-begin")
    stage_begin.add_argument("--name", required=True)
    stage_begin.add_argument("--order", required=True, type=int)
    stage_pass = sub.add_parser("stage-pass")
    stage_pass.add_argument("--name", required=True)
    stage_pass.add_argument("--rows", type=int)
    publication = sub.add_parser("publish")
    publication.add_argument("--mode", default="production")
    publication.add_argument("--required", nargs="+", required=True)
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()
        print("CONTROL_PLANE_MIGRATION_PASS")
    elif args.command == "start":
        print(start_run(args.mode))
    elif args.command == "fail":
        fail_run(args.message)
    elif args.command == "stage-begin":
        stage_started(args.name, args.order)
    elif args.command == "stage-pass":
        stage_finished(args.name, row_count=args.rows)
    elif args.command == "publish":
        print(json.dumps(publish(args.mode, args.required), sort_keys=True))


if __name__ == "__main__":
    main()
