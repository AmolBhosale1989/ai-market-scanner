"""Committed feeder evidence, selected inside the Engine's exact T0 snapshot."""
from __future__ import annotations

from datetime import timedelta
import json

from . import control_plane as cp
from .catalogue_snapshot import CATALOGUE_DATASETS


FEEDER_STAGES = ('seed_plan', 'intraday_warehouse', 'catalyst_ingest')
MAX_RECEIPT_AGE = timedelta(minutes=10)


def _catalogue_hashes(cur, run_id):
    cur.execute("""SELECT dataset_name,dataset_version_id,content_hash,row_count
        FROM dataset_version WHERE pipeline_run_id=%s AND status='AVAILABLE'
        AND dataset_name=ANY(%s)""", (run_id, sorted(CATALOGUE_DATASETS)))
    versions = cur.fetchall()
    if {r[0] for r in versions} != CATALOGUE_DATASETS:
        raise RuntimeError('FEEDER_CATALOGUE_MISSING')
    payloads = cp._read_version_records(cur, [r[1] for r in versions])
    for name, version, digest, count in versions:
        if not count or len(payloads[version]) != count or cp._hash(payloads[version]) != digest:
            raise RuntimeError(f'FEEDER_CATALOGUE_CORRUPT: {name}')
    return {name: digest for name, _, digest, _ in versions}


def complete_feeder(run_id):
    """Atomically record successful stage joins; never create a publication."""
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='3s'")
        cur.execute("SET LOCAL statement_timeout='15s'")
        cur.execute("SELECT mode,status,source_commit,started_at FROM pipeline_run "
                    "WHERE pipeline_run_id=%s FOR UPDATE", (run_id,))
        run = cur.fetchone()
        if run is None or run[:2] != ('feeder', 'STARTED'):
            raise RuntimeError('FEEDER_RUN_NOT_COMPLETABLE')
        cur.execute("SELECT clock_timestamp()")
        now = cur.fetchone()[0]
        cur.execute("""SELECT stage_name,status,started_at,completed_at FROM pipeline_stage
            WHERE pipeline_run_id=%s""", (run_id,))
        rows = {row[0]: row[1:] for row in cur.fetchall()}
        if set(rows) != set(FEEDER_STAGES):
            raise RuntimeError('FEEDER_STAGE_SET')
        previous = run[3]
        for name in FEEDER_STAGES:
            status, start, end = rows[name]
            if status != 'PASS' or end is None or not previous <= start <= end <= now:
                raise RuntimeError(f'FEEDER_STAGE_FAILED: {name}')
            previous = end
        hashes = _catalogue_hashes(cur, run_id)
        payload = cp._clean(dict(feeder_run_id=run_id, source_commit=run[2],
            completed_at=now, catalogue_hashes=hashes,
            stages={name: dict(status=rows[name][0], started_at=rows[name][1],
                               completed_at=rows[name][2]) for name in FEEDER_STAGES}))
        digest = cp._hash(payload)
        cur.execute("""INSERT INTO feeder_receipt
            (pipeline_run_id,completed_at,source_commit,catalogue_hashes,content_hash,payload)
            VALUES (%s,%s,%s,%s::jsonb,%s,%s::jsonb)""",
            (run_id, now, run[2], cp._canonical(hashes), digest, cp._canonical(payload)))
        cur.execute("UPDATE pipeline_run SET status='INGESTED',completed_at=%s "
                    "WHERE pipeline_run_id=%s", (now, run_id))
    print('FEEDER_COMMITS_JOINED ' + json.dumps(payload, sort_keys=True), flush=True)
    return payload


def bind_at_snapshot(cur, run_id, at, visibility):
    """Called in capture_boundary's transaction; post-T0 commits cannot qualify."""
    cur.execute("SELECT dataset_name,content_hash FROM frozen_catalogue "
                "WHERE pipeline_run_id=%s AND as_of_utc=%s AND pg_snapshot=%s",
                (run_id, at, visibility))
    hashes = dict(cur.fetchall())
    if set(hashes) != CATALOGUE_DATASETS:
        raise RuntimeError('FEEDER_CATALOGUE_MISSING')
    cur.execute("SELECT source_commit FROM pipeline_run WHERE pipeline_run_id=%s "
                "AND mode='production' AND metadata->>'lane'='engine'", (run_id,))
    engine = cur.fetchone()
    if engine is None:
        raise RuntimeError('ENGINE_RUN_REQUIRED')
    cur.execute("""SELECT r.pipeline_run_id::text,r.content_hash,r.payload
        FROM feeder_receipt r JOIN pipeline_run p USING(pipeline_run_id)
        WHERE p.status='INGESTED' AND r.source_commit=%s AND r.catalogue_hashes=%s::jsonb
          AND r.completed_at BETWEEN %s AND %s
          AND pg_visible_in_snapshot(r.writer_xid,%s::pg_snapshot)
        ORDER BY r.completed_at DESC,r.pipeline_run_id LIMIT 1""",
        (engine[0], cp._canonical(hashes), at - MAX_RECEIPT_AGE, at, visibility))
    row = cur.fetchone()
    if row is None:
        raise RuntimeError('ENGINE_FEEDER_NOT_READY: committed matching feeder receipt required')
    feeder_id, digest, payload = row
    if cp._hash(payload) != digest:
        raise RuntimeError('ENGINE_FEEDER_RECEIPT_CORRUPT')
    cur.execute("""INSERT INTO engine_feeder_binding
        (pipeline_run_id,feeder_run_id,as_of_utc,pg_snapshot,receipt_hash)
        VALUES (%s,%s,%s,%s,%s)""", (run_id, feeder_id, at, visibility, digest))
    print('ENGINE_FEEDER_BOUND ' + json.dumps(dict(engine_run_id=run_id,
        feeder_run_id=feeder_id, as_of_utc=str(at), pg_snapshot=visibility,
        receipt_hash=digest), sort_keys=True), flush=True)


def validate_binding(cur, run_id):
    """Acceptance and pointer publication require the same immutable binding."""
    cur.execute("""SELECT b.as_of_utc,b.pg_snapshot,b.receipt_hash,r.content_hash,r.payload,
          r.completed_at,r.catalogue_hashes,r.source_commit,p.source_commit,
          pg_visible_in_snapshot(r.writer_xid,b.pg_snapshot::pg_snapshot),s.payload
        FROM engine_feeder_binding b
        JOIN feeder_receipt r ON r.pipeline_run_id=b.feeder_run_id
        JOIN pipeline_run p ON p.pipeline_run_id=b.pipeline_run_id
        JOIN dataset_version d ON d.pipeline_run_id=p.pipeline_run_id
          AND d.dataset_name='warehouse_snapshot' AND d.status='AVAILABLE'
        JOIN dataset_row s ON s.dataset_version_id=d.dataset_version_id
        WHERE b.pipeline_run_id=%s""", (run_id,))
    rows = cur.fetchall()
    if len(rows) != 1:
        raise RuntimeError('ENGINE_FEEDER_BINDING_MISSING')
    at, visibility, bound_hash, digest, payload, completed, hashes, source, engine_source, visible, snapshot = rows[0]
    import pandas as pd
    if (bound_hash != digest or cp._hash(payload) != digest or source != engine_source
            or not visible or not at - MAX_RECEIPT_AGE <= completed <= at
            or pd.Timestamp(snapshot.get('as_of_utc')) != at
            or snapshot.get('pg_snapshot') != visibility
            or snapshot.get('production_run_id') != run_id):
        raise RuntimeError('ENGINE_FEEDER_BINDING_INVALID')
    cur.execute("SELECT dataset_name,content_hash FROM frozen_catalogue "
                "WHERE pipeline_run_id=%s AND as_of_utc=%s AND pg_snapshot=%s",
                (run_id, at, visibility))
    if dict(cur.fetchall()) != hashes:
        raise RuntimeError('ENGINE_FEEDER_CATALOGUE_MISMATCH')
