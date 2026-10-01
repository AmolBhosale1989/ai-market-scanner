"""Supervise an isolated child group; lease loss stops all child processes.

This primitive is not a replacement for fencing database writes. The caller must
acquire ownership before spawning and use the same token at publication.
"""
import os
import signal
import subprocess


def supervise(command, renew, *, env=None, heartbeat_seconds=30):
    if heartbeat_seconds <= 0:
        raise ValueError('heartbeat interval must be positive')
    # Validate ownership before starting any worker work.
    renew()
    child = subprocess.Popen(command, env=env, start_new_session=True)
    try:
        while True:
            try:
                return child.wait(timeout=heartbeat_seconds)
            except subprocess.TimeoutExpired:
                renew()  # Any DB error or fenced-out result fails closed.
    finally:
        # Also clean up descendants when the direct child exits unexpectedly.
        # Only the child's dedicated group is targeted, never the CI agent.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
