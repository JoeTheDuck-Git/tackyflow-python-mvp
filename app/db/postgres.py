from __future__ import annotations

import os
from threading import Lock

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


_pools: dict[str, ConnectionPool] = {}
_lock = Lock()


def get_pool(database_url: str) -> ConnectionPool:
    with _lock:
        pool = _pools.get(database_url)
        if pool is None:
            # Vercel runs several instances at once and each keeps its own pool, while
            # the Supabase pooler caps clients (15 in session mode). Keep pools small,
            # open connections on demand and release idle ones quickly.
            pool = ConnectionPool(
                conninfo=database_url,
                min_size=0,
                max_size=max(1, int(os.getenv("DB_POOL_MAX_SIZE", "3"))),
                max_idle=60,
                timeout=30,
                # prepare_threshold=None keeps statements unprepared, which the
                # Supabase transaction pooler (port 6543) requires.
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
                open=True,
            )
            _pools[database_url] = pool
        return pool


def close_pools() -> None:
    with _lock:
        pools=list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


# Arbitrary constant shared by every schema-setup transaction.
SCHEMA_SETUP_LOCK_KEY = 7_310_432_019


def lock_schema_setup(connection) -> None:
    """Serialize startup DDL across concurrently booting instances.

    Each repository runs CREATE TABLE/INDEX IF NOT EXISTS (and small backfills)
    when a cold start imports app.main. Several instances booting at once took
    those table locks in different orders and PostgreSQL aborted one with
    DeadlockDetected, crashing the import. A transaction-scoped advisory lock
    makes them run one after another; it is released at commit.
    """
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_SETUP_LOCK_KEY,))


def _deployment_version() -> str:
    return (
        os.getenv("VERCEL_GIT_COMMIT_SHA")
        or os.getenv("VERCEL_DEPLOYMENT_ID")
        or os.getenv("VERCEL_URL")
        or ""
    )


def begin_schema_setup(connection, key: str) -> bool:
    """Return True when this instance should run ``key``'s startup DDL.

    The first instance of a deployment runs the DDL under the advisory lock and
    records the deployment in py_schema_setup (in the same transaction, so a
    failed DDL leaves no record). Every later cold start of that deployment sees
    the record with one cheap SELECT and skips DDL entirely, instead of queueing
    behind the lock. Without a deployment id (local runs) DDL always runs.
    """
    version = _deployment_version()
    if version and _schema_recorded(connection, key, version):
        return False
    lock_schema_setup(connection)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS py_schema_setup (
            key TEXT PRIMARY KEY, version TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
    )
    if not version:
        return True
    if _schema_recorded(connection, key, version):
        return False
    connection.execute(
        """INSERT INTO py_schema_setup (key, version) VALUES (%s, %s)
           ON CONFLICT (key) DO UPDATE SET version = EXCLUDED.version, applied_at = now()""",
        (key, version),
    )
    return True


def _schema_recorded(connection, key: str, version: str) -> bool:
    exists = connection.execute("SELECT to_regclass('py_schema_setup') IS NOT NULL AS ok").fetchone()
    if not (exists["ok"] if isinstance(exists, dict) else exists[0]):
        return False
    row = connection.execute("SELECT version FROM py_schema_setup WHERE key = %s", (key,)).fetchone()
    return row is not None and (row["version"] if isinstance(row, dict) else row[0]) == version
