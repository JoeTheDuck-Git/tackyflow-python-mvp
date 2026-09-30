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
