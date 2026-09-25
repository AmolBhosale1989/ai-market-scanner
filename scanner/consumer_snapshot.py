"""Opt-in validated current-run read boundary; never used as publication clock."""
import os
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
    return anchor


def consumer_anchor():
    if os.getenv('WAREHOUSE_CONSUMER_SNAPSHOT') != '1':
        return None
    run_id = os.getenv('PRODUCTION_RUN_ID', '').strip()
    if not run_id:
        raise RuntimeError('CONSUMER_SNAPSHOT_RUN_REQUIRED')
    anchor = _validated_anchor(run_id)
    if anchor > pd.Timestamp.now(tz='UTC'):
        raise RuntimeError('CONSUMER_SNAPSHOT_FUTURE')
    return anchor
