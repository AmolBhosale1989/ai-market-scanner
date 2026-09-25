"""Bounded, process-local PostgreSQL connections with transaction isolation.

Only connections are reused. Market data and freshness decisions are never
cached here. Pool contexts commit on success and roll back on exceptions.
"""
from __future__ import annotations

import atexit
import os
import threading


_pools = {}
_pool_lock = threading.Lock()


def _new_pool(url: str, sslmode: str):
    try:
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise RuntimeError("BITEMPORAL_WAREHOUSE_UNAVAILABLE: psycopg pool is not installed") from exc
    pool = ConnectionPool(
        conninfo=url,
        kwargs={"sslmode": sslmode, "connect_timeout": 15},
        min_size=0,
        max_size=2,
        max_waiting=8,
        timeout=15,
        max_idle=60,
        max_lifetime=300,
        num_workers=1,
        open=True,
    )
    return pool


def connection():
    """Get an exclusive connection for one transaction; never share it concurrently."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("BITEMPORAL_WAREHOUSE_UNAVAILABLE: DATABASE_URL is not configured")
    # Never reuse a socket created by another process or for different credentials.
    key = (os.getpid(), url, os.getenv("PGSSLMODE", "require"))
    with _pool_lock:
        pool = _pools.get(key)
        if pool is None:
            pool = _new_pool(url, key[2])
            _pools[key] = pool
    return pool.connection()


def close_pools():
    """Release this process's idle connections at shutdown (also useful in tests)."""
    pid = os.getpid()
    with _pool_lock:
        pools = [_pools.pop(key) for key in list(_pools) if key[0] == pid]
    for pool in pools:
        pool.close()


def _after_fork():
    # A lock held by another thread at fork cannot be acquired in the child.
    # PID-scoped keys leave inherited pools unused; new children open fresh sockets.
    global _pool_lock
    _pool_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
atexit.register(close_pools)
