"""Historical instrument versions and immutable copies of run catalogues."""
import pandas as pd

CATALOGUE_DATASETS = frozenset({'master_universe', 'live_universe', 'tradable_universe'})


def instrument_cte(as_of, visibility):
    if visibility is None:
        return 'visible_instrument AS (SELECT * FROM instrument)', ()
    return '''visible_instrument AS (
        SELECT * FROM (
            SELECT DISTINCT ON (instrument_id) * FROM instrument_catalogue_revision
            WHERE known_at<=%s AND pg_visible_in_snapshot(writer_xid,%s::pg_snapshot)
            ORDER BY instrument_id,revision_id DESC
        ) versions WHERE NOT deleted
    )''', (as_of, visibility)


def freeze_catalogues(cur, run_id, at, visibility):
    from .control_plane import _read_version_records, _hash, _canonical
    cur.execute('''SELECT dataset_name,dataset_version_id,content_hash,row_count
                   FROM dataset_version WHERE pipeline_run_id=%s AND status='AVAILABLE'
                   AND dataset_name=ANY(%s)''', (run_id, sorted(CATALOGUE_DATASETS)))
    versions = cur.fetchall()
    payloads = _read_version_records(cur, [v[1] for v in versions])
    for name, version, digest, count in versions:
        records = payloads[version]
        if len(records) != count or _hash(records) != digest:
            raise RuntimeError(f'CATALOGUE_SNAPSHOT_CORRUPT: {name}')
        cur.execute('''INSERT INTO frozen_catalogue
            (pipeline_run_id,as_of_utc,pg_snapshot,dataset_name,records,content_hash)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s)''',
            (run_id, at, visibility, name, _canonical(records), digest))


def read_frozen_catalogue(name, run_id, at, visibility, *, required=True):
    from .database import connection
    from .control_plane import _hash
    with connection() as conn, conn.cursor() as cur:
        cur.execute('''SELECT records,content_hash FROM frozen_catalogue
            WHERE pipeline_run_id=%s AND as_of_utc=%s AND pg_snapshot=%s AND dataset_name=%s''',
            (run_id, at, visibility, name))
        row = cur.fetchone()
    if row is None:
        if required:
            raise RuntimeError(f'CATALOGUE_SNAPSHOT_MISSING: {name}')
        return pd.DataFrame()
    if _hash(row[0]) != row[1]:
        raise RuntimeError(f'CATALOGUE_SNAPSHOT_CORRUPT: {name}')
    return pd.DataFrame(row[0])
