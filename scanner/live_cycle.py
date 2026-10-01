"""One isolated feeder or engine cycle; shared runner, fresh run ID and caches."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys

from . import control_plane as cp
from .catalogue_snapshot import CATALOGUE_DATASETS
from .feeder_handoff import complete_feeder
from .production_telemetry import seed
from .run_policy import select_mode
from .workflow_continuation import should_continue_live_session


def stage(name, order, action, run_id):
    cp.stage_started(name, order, run_id=run_id)
    action()
    cp.stage_finished(name, run_id=run_id)


def run_engine():
    # Keep parallel strategy children in one group so failure/cancellation cannot
    # leave a background writer running after the next cycle has started.
    process = subprocess.Popen(['bash', 'scripts/live_engine.sh'], start_new_session=True)
    try:
        code = process.wait()
        if code:
            raise subprocess.CalledProcessError(code, process.args)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise


def run_cycle(role):
    if role not in {'feeder', 'engine'}:
        raise ValueError('unknown live role')
    active, reason = should_continue_live_session(minimum_remaining_minutes=10)
    if not active:
        raise RuntimeError('LIVE_SESSION_CLOSED: ' + reason)
    # Never turn the Engine into an unexpected full/network rebuild.
    if select_mode('live', cp.publication_info('production')) != 'live':
        raise RuntimeError('LIVE_DAILY_BASELINE_REQUIRED: run the full production lane first')
    os.environ.pop('WAREHOUSE_CONSUMER_SNAPSHOT', None)
    rid = cp.start_run('feeder' if role == 'feeder' else 'production')
    os.environ['PRODUCTION_RUN_ID'] = rid
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE pipeline_run SET metadata=metadata||%s::jsonb "
                    "WHERE pipeline_run_id=%s", (cp._canonical({'lane': role}), rid))
    print(f'LIVE_CYCLE_BEGIN role={role} run_id={rid}', flush=True)
    try:
        if role == 'feeder':
            stage('seed_plan', 10, lambda: cp.seed_from_publication(
                'production', sorted(CATALOGUE_DATASETS), run_id=rid), rid)
            stage('intraday_warehouse', 60, lambda: subprocess.run(
                [sys.executable, '-m', 'scanner.live_ingestion'], check=True), rid)
            stage('catalyst_ingest', 65, lambda: subprocess.run(
                ['timeout', '--kill-after=5s', '150s', sys.executable, '-m',
                 'scanner.catalyst_pipeline', '--ingest-only', '--mandatory-only'],
                check=True, env={**os.environ, 'CATALYST_MODE': 'optional'}), rid)
            complete_feeder(rid)
        else:
            stage('engine_seed', 10, seed, rid)
            run_engine()
    except BaseException as exc:
        # An acknowledged publication is immutable even if a later process dies.
        cp.fail_run(f'{role}: {type(exc).__name__}: {exc}', rid)
        raise
    print(f'LIVE_CYCLE_PASS role={role} run_id={rid}', flush=True)
    return rid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['feeder', 'engine'])
    args = parser.parse_args()
    def terminate(signum, frame):
        raise RuntimeError(f'LIVE_CYCLE_TERMINATED: signal={signum}')
    signal.signal(signal.SIGTERM, terminate)
    run_cycle(args.role)


if __name__ == '__main__':
    main()
