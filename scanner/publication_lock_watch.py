"""Best-effort live lock evidence. Never records query text or credentials."""
from contextlib import contextmanager
import json
import threading
from .database import connection


@contextmanager
def watch(pid):
    stop=threading.Event()
    def sample():
        try:
            with connection(timeout=2) as conn:
                try:
                    conn.autocommit=True
                    conn.execute("SET statement_timeout='1s'")
                    while not stop.is_set():
                        row=conn.execute("""SELECT clock_timestamp()::text,wait_event_type,wait_event,
                            pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=%s""",(pid,)).fetchone()
                        if row:
                            print('PUBLICATION_LOCK_SAMPLE '+json.dumps(dict(at=row[0],pid=pid,
                                wait_type=row[1],wait_event=row[2],blockers=row[3])),flush=True)
                        stop.wait(1)
                    conn.execute('RESET statement_timeout')
                    conn.autocommit=False
                except BaseException:
                    conn.close()  # Never return modified session settings to the pool.
                    raise
        except Exception as exc:
            print('PUBLICATION_LOCK_MONITOR_UNAVAILABLE error='+type(exc).__name__,flush=True)
    thread=threading.Thread(target=sample,daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(4)
