"""Temporary diagnostic: read-only connection reuse on one immutable publication."""
from contextlib import contextmanager
import json
import os
import statistics
import time

import psycopg

from . import control_plane as cp
from . import database
from .product_feed import FEED_DATASETS


READ_ONLY = "-c default_transaction_read_only=on -c statement_timeout=60000"
os.environ["PGOPTIONS"] = READ_ONLY
database.close_pools()
counters = {"connections": 0, "checkouts": 0}


@contextmanager
def direct():
    counters["connections"] += 1
    counters["checkouts"] += 1
    with psycopg.connect(os.environ["DATABASE_URL"], sslmode="require", connect_timeout=15,
                        options=READ_ONLY) as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone() == ("on",)
        yield conn


@contextmanager
def pooled():
    counters["checkouts"] += 1
    with database.connection() as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone() == ("on",)
        yield conn


with direct() as conn:
    snapshot, run_id = conn.execute("""SELECT ps.publication_snapshot_id,ps.pipeline_run_id::text
        FROM publication_head ph JOIN publication_snapshot ps USING(publication_snapshot_id)
        WHERE ph.mode='production' AND ps.status='PUBLISHED'""").fetchone()
print(json.dumps({"snapshot": snapshot, "run_id": run_id, "database_read_only": True}), flush=True)


def consume():
    return {name: cp._hash(cp.read_dataset(name, run_id=run_id).to_dict("records"))
            for name in FEED_DATASETS}


measurements = []
for trial in range(2):
    results = {}
    for version, factory in ([("direct", direct), ("pooled", pooled)] if trial == 0
                             else [("pooled", pooled), ("direct", direct)]):
        database.close_pools()  # Include cold first connection in every pooled trial.
        counters.update(connections=0, checkouts=0)
        cp._connect = factory
        started = time.perf_counter()
        results[version] = consume()
        elapsed = time.perf_counter() - started
        if version == "pooled":
            counters["connections"] = sum(pool.get_stats()["connections_num"]
                                           for pool in database._pools.values())
        item = {"trial": trial + 1, "version": version, "seconds": round(elapsed, 3),
                "datasets": len(results[version]), **counters}
        measurements.append(item)
        print("BENCHMARK " + json.dumps(item), flush=True)
    assert results["direct"] == results["pooled"]
    print(f"EQUIVALENCE_PASS trial={trial + 1}", flush=True)
database.close_pools()
summary = {version: statistics.median(row["seconds"] for row in measurements
                                      if row["version"] == version)
           for version in ("direct", "pooled")}
print("SUMMARY " + json.dumps({**summary, "same_results": True, "database_read_only": True}), flush=True)
