from __future__ import annotations

from threading import Lock

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


_pools: dict[str, ConnectionPool] = {}
_lock = Lock()


def get_pool(database_url: str) -> ConnectionPool:
    with _lock:
        pool = _pools.get(database_url)
        if pool is None:
            pool = ConnectionPool(
                conninfo=database_url,
                min_size=1,
                max_size=10,
                timeout=30,
                kwargs={"row_factory": dict_row},
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
