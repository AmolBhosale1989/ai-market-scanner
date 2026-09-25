"""Read-only, same-publication comparison; diagnostic branch only."""
from contextlib import contextmanager
import importlib.util
import json
import os
import statistics
import sys
import time

import psycopg

from . import control_plane as new
from .product_feed import FEED_DATASETS


spec = importlib.util.spec_from_file_location("scanner._baseline_control_plane", sys.argv[1])
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
counters = {"connections": 0, "queries": 0}


class Cursor:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, *args, **kwargs):
        counters["queries"] += 1
        return self.raw.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def __enter__(self):
        self.raw.__enter__()
        return self

    def __exit__(self, *args):
        return self.raw.__exit__(*args)


class Connection:
    def __init__(self, raw):
        self.raw = raw

    def cursor(self):
        return Cursor(self.raw.cursor())


@contextmanager
def connect():
    counters["connections"] += 1
    with psycopg.connect(
        os.environ["DATABASE_URL"], sslmode="require", connect_timeout=15,
        options="-c default_transaction_read_only=on -c statement_timeout=60000",
    ) as raw:
        yield Connection(raw)


old._connect = new._connect = connect
with connect() as conn, conn.cursor() as cur:
    cur.execute("SHOW transaction_read_only")
    assert cur.fetchone()[0] == "on"
    cur.execute("""SELECT ps.publication_snapshot_id,ps.pipeline_run_id::text,
                          dv.dataset_name,dv.dataset_version_id,dv.content_hash,dv.row_count
                   FROM publication_head ph JOIN publication_snapshot ps USING(publication_snapshot_id)
                   JOIN publication_dataset pd USING(publication_snapshot_id)
                   JOIN dataset_version dv USING(dataset_version_id)
                   WHERE ph.mode='production' AND ps.status='PUBLISHED'
                   ORDER BY dv.dataset_name""")
    versions = cur.fetchall()
assert versions
snapshot, run_id = versions[0][:2]
assert all(row[:2] == (snapshot, run_id) for row in versions)
print(json.dumps({"snapshot": snapshot, "run_id": run_id, "datasets": len(versions),
                  "rows": sum(row[5] for row in versions), "database_read_only": True}), flush=True)


def consume(module):
    return {name: new._hash(module.read_dataset(name, run_id=run_id).to_dict("records"))
            for name in FEED_DATASETS}


def publication_payloads(batch):
    with connect() as conn, conn.cursor() as cur:
        if batch:
            grouped = new._read_version_records(cur, (row[3] for row in versions))
        else:
            grouped = {}
            for _, _, _, vid, _, _ in versions:
                cur.execute("SELECT payload FROM dataset_row WHERE dataset_version_id=%s ORDER BY row_ordinal", (vid,))
                grouped[vid] = [row[0] for row in cur.fetchall()]
    result = {}
    for _, _, name, vid, digest, count in versions:
        records = grouped[vid]
        assert len(records) == count and new._hash(records) == digest, name
        result[name] = digest
    return result


measurements = []
for trial in range(2):
    pairs = [("consumer_reads", lambda: consume(old), lambda: consume(new)),
             ("publication_payload_checks", lambda: publication_payloads(False), lambda: publication_payloads(True))]
    for operation, before, after in pairs:
        values = {}
        for version, fn in ([("before", before), ("after", after)] if trial == 0
                            else [("after", after), ("before", before)]):
            counters.update(connections=0, queries=0)
            started = time.perf_counter()
            values[version] = fn()
            item = {"trial": trial + 1, "operation": operation, "version": version,
                    "seconds": round(time.perf_counter() - started, 3), **counters}
            measurements.append(item)
            print("BENCHMARK " + json.dumps(item), flush=True)
        assert values["before"] == values["after"], operation
        print(f"EQUIVALENCE_PASS {operation} trial={trial + 1}", flush=True)
for operation in sorted({row["operation"] for row in measurements}):
    summary = {version: statistics.median(row["seconds"] for row in measurements
                                         if row["operation"] == operation and row["version"] == version)
               for version in ("before", "after")}
    print("SUMMARY " + json.dumps({"operation": operation, **summary,
                                    "same_results": True, "database_read_only": True}), flush=True)
