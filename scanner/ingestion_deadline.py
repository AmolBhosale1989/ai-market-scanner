"""A process-group deadline for Stage 60, including workers and DB waits."""
import multiprocessing
import os
import signal
import time


def _child(target, ready):
    os.setsid()
    ready.set()
    target()


def run_bounded(target, seconds=120):
    if seconds <= 0:
        raise ValueError('ingestion deadline must be positive')
    ctx=multiprocessing.get_context('spawn')
    ready=ctx.Event()
    process=ctx.Process(target=_child,args=(target,ready))
    started=time.monotonic()
    process.start()
    process.join(seconds)
    if process.is_alive():
        # All spawned provider workers inherit this group. No orphan requests
        # may continue committing after the ingestion stage has failed.
        if ready.is_set():
            try:
                os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        process.join(2)
        # The leader may exit before its children; kill the group regardless.
        if ready.is_set():
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.is_alive():
            process.kill()
        process.join(2)
        print(f'INGESTION_DEADLINE_EXCEEDED elapsed_seconds={time.monotonic()-started:.3f}',flush=True)
        raise RuntimeError('INGESTION_OVERALL_DEADLINE: snapshot forbidden')
    if process.exitcode != 0:
        raise RuntimeError(f'INGESTION_CHILD_FAILED exitcode={process.exitcode}')
