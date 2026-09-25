from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid

import pytest

from scanner import database as db


@pytest.fixture
def isolated_pools():
    db.close_pools()
    yield
    db.close_pools()


def test_no_database_url_fails_without_creating_pool(monkeypatch, isolated_pools):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not configured"):
        db.connection()
    assert not db._pools


def test_pool_is_created_once_and_scoped_to_process_credentials_and_ssl(monkeypatch, isolated_pools):
    created = []
    pid = [100]

    class Pool:
        def connection(self):
            return self

        def close(self):
            pass

    def create(url, ssl):
        pool = Pool()
        created.append((url, ssl, pool))
        return pool

    monkeypatch.setattr(db, "_new_pool", create)
    monkeypatch.setattr(db.os, "getpid", lambda: pid[0])
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-one")
    monkeypatch.setenv("PGSSLMODE", "require")
    with ThreadPoolExecutor(max_workers=8) as executor:
        connections = list(executor.map(lambda _: db.connection(), range(24)))
    assert len(created) == 1
    assert all(conn is connections[0] for conn in connections)
    pid[0] = 101
    assert db.connection() is not connections[0]
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-two")
    db.connection()
    monkeypatch.setenv("PGSSLMODE", "disable")
    db.connection()
    assert len(created) == 4
    db.close_pools()
    pid[0] = 100
    db.close_pools()


@pytest.fixture
def postgres(isolated_pools):
    if not os.getenv("DATABASE_URL", "").strip():
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    from psycopg import sql

    table = "pool_test_" + uuid.uuid4().hex
    identifier = sql.Identifier(table)
    with db.connection() as conn:
        conn.execute(sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY)").format(identifier))
    yield identifier
    with db.connection() as conn:
        conn.execute(sql.SQL("DROP TABLE {}").format(identifier))


@pytest.mark.postgres_integration
def test_reuses_socket_but_commits_each_transaction_and_reads_new_rows(postgres):
    import psycopg
    from psycopg import sql

    with db.connection() as conn:
        pid = conn.info.backend_pid
        conn.execute(sql.SQL("INSERT INTO {} VALUES (1)").format(postgres))
    # A separate connection must observe the committed row, and its new data must
    # become visible on the next checkout of the same pooled socket.
    with psycopg.connect(os.environ["DATABASE_URL"], sslmode=os.getenv("PGSSLMODE", "require")) as other:
        assert other.execute(sql.SQL("SELECT id FROM {}").format(postgres)).fetchall() == [(1,)]
        other.execute(sql.SQL("INSERT INTO {} VALUES (2)").format(postgres))
    with db.connection() as conn:
        assert conn.info.backend_pid == pid
        assert conn.execute(sql.SQL("SELECT id FROM {} ORDER BY id").format(postgres)).fetchall() == [(1,), (2,)]


@pytest.mark.postgres_integration
def test_exception_and_sql_error_rollback_without_poisoning_next_transaction(postgres):
    import psycopg
    from psycopg import sql

    insert = sql.SQL("INSERT INTO {} VALUES (%s)").format(postgres)
    with pytest.raises(RuntimeError, match="reject publication"):
        with db.connection() as conn:
            conn.execute(insert, (1,))
            raise RuntimeError("reject publication")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.connection() as conn:
            conn.execute(insert, (2,))
            conn.execute(insert, (2,))
    with db.connection() as conn:
        assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(postgres)).fetchone()[0] == 0
        conn.execute(insert, (3,))
    with db.connection() as conn:
        assert conn.execute(sql.SQL("SELECT id FROM {}").format(postgres)).fetchall() == [(3,)]


@pytest.mark.postgres_integration
def test_parallel_checkouts_are_exclusive_and_bounded(postgres):
    from psycopg_pool import PoolTimeout

    first_ready, second_ready, release = threading.Event(), threading.Event(), threading.Event()
    pids = []

    def hold(ready):
        with db.connection() as conn:
            pids.append(conn.info.backend_pid)
            ready.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(hold, first_ready)
        second = executor.submit(hold, second_ready)
        try:
            assert first_ready.wait(5) and second_ready.wait(5)
            assert len(set(pids)) == 2
            pool = next(pool for key, pool in db._pools.items() if key[0] == os.getpid())
            with pytest.raises(PoolTimeout):
                with pool.connection(timeout=0.1):
                    pytest.fail("A third checkout exceeded the two-connection bound")
        finally:
            release.set()
        first.result()
        second.result()
    with db.connection() as conn:
        assert conn.info.backend_pid in pids
        assert conn.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.postgres_integration
def test_closed_connection_is_replaced_without_retrying_failed_work(postgres):
    with db.connection() as conn:
        original_pid = conn.info.backend_pid
        conn.close()
    with db.connection() as conn:
        assert conn.info.backend_pid != original_pid
        assert conn.execute("SELECT 1").fetchone() == (1,)
