"""Opt-in validated current-run read boundary; never used as publication clock."""
import os
import re
from functools import lru_cache
import pandas as pd


@lru_cache(maxsize=8)
def _validated_anchor(run_id):
    from .control_plane import read_dataset
    frame = read_dataset('warehouse_snapshot', run_id=run_id)
    if len(frame) != 1 or frame.iloc[0].get('status') != 'PASS':
        raise RuntimeError('CONSUMER_SNAPSHOT_INVALID')
    row = frame.iloc[0]
    if str(row.get('production_run_id')) != run_id:
        raise RuntimeError('CONSUMER_SNAPSHOT_WRONG_RUN')
    anchor = pd.Timestamp(row.get('as_of_utc'))
    if pd.isna(anchor) or anchor.tzinfo is None:
        raise RuntimeError('CONSUMER_SNAPSHOT_INVALID_TIME')
    visibility = validate_pg_snapshot(row.get("pg_snapshot"))
    return anchor, visibility


def consumer_anchor():
    if os.getenv('WAREHOUSE_CONSUMER_SNAPSHOT') != '1':
        return None
    run_id = os.getenv('PRODUCTION_RUN_ID', '').strip()
    if not run_id:
        raise RuntimeError('CONSUMER_SNAPSHOT_RUN_REQUIRED')
    anchor, _ = _validated_anchor(run_id)
    if anchor > pd.Timestamp.now(tz='UTC'):
        raise RuntimeError('CONSUMER_SNAPSHOT_FUTURE')
    return anchor


def validate_pg_snapshot(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+:[0-9]+:(?:[0-9]+(?:,[0-9]+)*)?", value):
        raise RuntimeError("CONSUMER_PG_SNAPSHOT_REQUIRED_OR_INVALID")
    low, high, active = value.split(":")
    low, high = int(low), int(high)
    ids = [int(x) for x in active.split(",")] if active else []
    if not 0 < low <= high < 2**64 or ids != sorted(set(ids)) or any(x < low or x >= high for x in ids):
        raise RuntimeError("CONSUMER_PG_SNAPSHOT_INVALID")
    return value


def consumer_pg_snapshot():
    if os.getenv("WAREHOUSE_CONSUMER_SNAPSHOT") != "1":
        return None
    consumer_anchor()  # Validate run, timestamp and future guard as one cached pair.
    return _validated_anchor(os.environ["PRODUCTION_RUN_ID"].strip())[1]


def resolve_pg_snapshot(explicit=None):
    current = consumer_pg_snapshot()
    if explicit is not None:
        explicit = validate_pg_snapshot(explicit)
        if current is not None and explicit != current:
            raise RuntimeError("CONSUMER_PG_SNAPSHOT_MISMATCH")
    return current if explicit is None else explicit


def capture_boundary(*, run_id=None, require_feeder=False):
    """Capture the server clock and transaction visibility in one SQL statement."""
    from .database import connection
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("SELECT statement_timestamp(), pg_current_snapshot()::text")
        at, visibility = cur.fetchone()
        if run_id is not None:
            from .catalogue_snapshot import freeze_catalogues
            freeze_catalogues(cur, run_id, at, visibility)
        if require_feeder:
            from .feeder_handoff import bind_at_snapshot
            bind_at_snapshot(cur, run_id, at, visibility)
    return pd.Timestamp(at), validate_pg_snapshot(visibility)
