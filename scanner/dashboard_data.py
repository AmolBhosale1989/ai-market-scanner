"""Read one publication's dashboard datasets with one database connection."""
import pandas as pd

from .control_plane import _connect, _hash


def read_dashboard_datasets(names, *, run_id):
    names = list(dict.fromkeys(names))
    if not run_id:
        raise RuntimeError("CONTROL_PLANE_RUN_REQUIRED")
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT dv.dataset_name,dv.content_hash,dv.row_count,dr.payload
               FROM dataset_version dv LEFT JOIN dataset_row dr
                 ON dr.dataset_version_id=dv.dataset_version_id
               WHERE dv.pipeline_run_id=%s AND dv.dataset_name=ANY(%s)
                 AND dv.status='AVAILABLE'
               ORDER BY dv.dataset_name,dr.row_ordinal""",
            (run_id, names),
        )
        rows = cur.fetchall()
    grouped = {}
    for name, digest, count, payload in rows:
        entry = grouped.setdefault(name, [digest, int(count), []])
        if payload is not None:
            entry[2].append(payload)
    result = {}
    for name in names:
        if name not in grouped:
            result[name] = (pd.DataFrame(), "blocked")
            continue
        digest, count, records = grouped[name]
        if len(records) != count or _hash(records) != digest:
            raise RuntimeError(f"CONTROL_PLANE_DATASET_CORRUPT: {name}")
        result[name] = (pd.DataFrame(records), "postgresql")
    return result
