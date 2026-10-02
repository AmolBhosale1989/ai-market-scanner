"""Bounded, serial session loops; cron is a watchdog, not a minute scheduler."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import signal
import subprocess
import sys
import time

from .workflow_continuation import should_continue_live_session


INTERVALS = {'feeder': 60, 'engine': 120}
CYCLE_LIMITS = {'feeder': 240, 'engine': 600}


def execute_cycle(command, *, check):
    """Forward cancellation to the whole timeout/cycle group, then reap it."""
    process = subprocess.Popen(command, start_new_session=True)
    try:
        code = process.wait()
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            process.wait()
        raise
    if check and code:
        raise subprocess.CalledProcessError(code, command)


def next_slot(origin, now, interval):
    """An overrun starts the next cycle immediately, never concurrent catch-up."""
    return max(origin + interval, now)


def run_session(role, *, max_cycles=1, minutes=45, clock=time.monotonic,
                sleep=time.sleep, execute=execute_cycle,
                session_check=should_continue_live_session):
    if role not in INTERVALS or not 1 <= max_cycles <= 60 or not 1 <= minutes <= 60:
        raise ValueError('invalid bounded session settings')
    origin = clock()
    deadline = origin + minutes * 60
    cycles = 0
    while cycles < max_cycles:
        if clock() + CYCLE_LIMITS[role] > deadline:
            break
        active, reason = session_check(minimum_remaining_minutes=10)
        if not active:
            print(f'LIVE_SESSION_STOP role={role} reason={reason}', flush=True)
            break
        started = clock()
        print('LIVE_SESSION_TICK ' + json.dumps(dict(role=role, cycle=cycles + 1,
            at=datetime.now(timezone.utc).isoformat(),
            target_interval_seconds=INTERVALS[role])), flush=True)
        # GNU timeout bounds and terminates the entire child process group,
        # including the Engine's parallel strategy processes. Any failure exits
        # this session; a later watchdog run is a new, separately logged attempt.
        execute(['timeout', '--kill-after=5s', f'{CYCLE_LIMITS[role]}s',
                 sys.executable, '-m', 'scanner.live_cycle', role], check=True)
        cycles += 1
        ended = clock()
        print(f'LIVE_SESSION_CYCLE_PASS role={role} cycle={cycles} '
              f'elapsed_seconds={ended-started:.6f}', flush=True)
        if cycles >= max_cycles:
            break
        due = next_slot(started, ended, INTERVALS[role])
        if due + CYCLE_LIMITS[role] > deadline:
            break
        # Short sleeps make shutdown responsive and avoid a long blocking wait.
        while clock() < due:
            sleep(min(1.0, due - clock()))
    print(f'LIVE_SESSION_FINISHED role={role} cycles={cycles}', flush=True)
    return cycles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=sorted(INTERVALS))
    parser.add_argument('--max-cycles', type=int, default=1)
    parser.add_argument('--minutes', type=int, default=45)
    args = parser.parse_args()
    def terminate(signum, frame):
        raise RuntimeError(f'LIVE_SESSION_TERMINATED: signal={signum}')
    signal.signal(signal.SIGTERM, terminate)
    run_session(args.role, max_cycles=args.max_cycles, minutes=args.minutes)


if __name__ == '__main__':
    main()
