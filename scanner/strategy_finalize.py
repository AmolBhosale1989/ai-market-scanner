"""Single production recommendation writer after the strategy completion barrier."""
import argparse

from . import control_plane as cp
from .consumer_snapshot import consumer_anchor, consumer_pg_snapshot
from .intraday import _write_recommendations


def require_producers(*, full=False):
    anchor, snapshot = consumer_anchor(), consumer_pg_snapshot()
    if anchor is None or snapshot is None:
        raise RuntimeError("STRATEGY_FINALIZE_SNAPSHOT_REQUIRED")
    required = {"v3_live", "sector_rotation", "momentum", "order_flow", "theme_live"}
    if full:
        required.add("daily_scan")
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT stage_name,status FROM pipeline_stage WHERE pipeline_run_id=%s",
                    (cp.current_run_id(),))
        states = dict(cur.fetchall())
        pending = sorted(name for name in required if states.get(name) != "PASS")
        if pending:
            raise RuntimeError("STRATEGY_FINALIZE_PRODUCERS_NOT_READY: " + ",".join(pending))
        # Empty datasets have lineage in metadata, so validate it as well as rows.
        cur.execute("""SELECT metadata FROM dataset_version
                       WHERE pipeline_run_id=%s AND dataset_name='intraday_live'
                         AND status='AVAILABLE'""", (cp.current_run_id(),))
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("STRATEGY_FINALIZE_V3_OUTPUT_REQUIRED")
        metadata = row[0]
        if (metadata.get("production_run_id") != cp.current_run_id()
                or metadata.get("as_of_utc") != anchor.tz_convert("UTC").isoformat()
                or metadata.get("pg_snapshot") != snapshot):
            raise RuntimeError("STRATEGY_FINALIZE_V3_PROVENANCE_MISMATCH")


def run(*, full=False):
    require_producers(full=full)
    # Hash-verified current-run read. Daily/legendary products remain separate;
    # the existing V3 eligibility rules continue to own recommendations.
    return _write_recommendations(cp.read_dataset("intraday_live"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    run(full=parser.parse_args().full)
