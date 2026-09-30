from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.postgres import begin_schema_setup, get_pool

from app.domain.models import (
    OpportunityGenerationEvent,
    OpportunityGenerationStatus,
    OpportunityItemStatus,
    OpportunityResponse,
)


class OpportunityNotFoundError(LookupError):
    pass


class OpportunityRepository(Protocol):
    async def save(self, generation: OpportunityResponse) -> OpportunityResponse: ...

    async def get(self, generation_id: str) -> OpportunityResponse: ...

    async def list(
        self, limit: int = 50, workspace_id: str | None = None
    ) -> list[OpportunityResponse]: ...

    async def find_by_request_hash(
        self, request_hash: str, workspace_id: str
    ) -> OpportunityResponse | None: ...

    async def release_workflow_adoptions(
        self, workflow_id: str, workspace_id: str
    ) -> list[OpportunityResponse]: ...

    async def ping(self) -> None: ...


def _release_workflow_adoptions(
    generation: OpportunityResponse,
    workflow_id: str,
) -> OpportunityResponse | None:
    """Return an unlinked snapshot, or ``None`` when this generation is unaffected.

    A deleted workflow turns an adopted candidate back into a saved candidate.  The
    full generation snapshot remains immutable to callers; repositories persist the
    returned copy and return the previous snapshot for best-effort compensation.
    """

    released_item_ids = [
        item.id
        for item in generation.opportunities
        if item.adopted_workflow_id == workflow_id
    ]
    if not released_item_ids:
        return None

    now = datetime.now(timezone.utc)
    released_items = [
        item.model_copy(
            update={
                "status": OpportunityItemStatus.SAVED,
                "adopted_workflow_id": None,
            },
            deep=True,
        )
        if item.id in released_item_ids
        else item.model_copy(deep=True)
        for item in generation.opportunities
    ]
    events = [
        OpportunityGenerationEvent(
            action="workflow_adoption_released",
            item_id=item_id,
            note=(
                f"workflow={workflow_id} 已刪除；內容機會已回復為 saved，"
                "並清除 adopted_workflow_id。"
            ),
        )
        for item_id in released_item_ids
    ]
    return generation.model_copy(
        update={
            "opportunities": released_items,
            "updated_at": now,
            "events": generation.events + events,
        },
        deep=True,
    )


class InMemoryOpportunityRepository:
    def __init__(self) -> None:
        self._items: dict[str, OpportunityResponse] = {}

    async def save(self, generation: OpportunityResponse) -> OpportunityResponse:
        self._items[generation.id] = generation.model_copy(deep=True)
        return generation

    async def get(self, generation_id: str) -> OpportunityResponse:
        generation = self._items.get(generation_id)
        if generation is None:
            raise OpportunityNotFoundError(generation_id)
        return generation.model_copy(deep=True)

    async def list(
        self, limit: int = 50, workspace_id: str | None = None
    ) -> list[OpportunityResponse]:
        items = (
            item
            for item in self._items.values()
            if workspace_id is None or item.request.workspace_id == workspace_id
        )
        return [
            item.model_copy(deep=True)
            for item in sorted(items, key=lambda entry: entry.updated_at, reverse=True)[:limit]
        ]

    async def find_by_request_hash(
        self, request_hash: str, workspace_id: str
    ) -> OpportunityResponse | None:
        matches = [
            item
            for item in self._items.values()
            if item.request_hash == request_hash
            and item.request.workspace_id == workspace_id
            and item.status == OpportunityGenerationStatus.COMPLETED
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: item.updated_at).model_copy(deep=True)

    async def release_workflow_adoptions(
        self, workflow_id: str, workspace_id: str
    ) -> list[OpportunityResponse]:
        previous_snapshots: list[OpportunityResponse] = []
        for generation_id, generation in list(self._items.items()):
            if generation.request.workspace_id != workspace_id:
                continue
            released = _release_workflow_adoptions(generation, workflow_id)
            if released is None:
                continue
            previous_snapshots.append(generation.model_copy(deep=True))
            self._items[generation_id] = released
        return previous_snapshots


class PostgresOpportunityRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        with self._connect() as connection:
            if not begin_schema_setup(connection, "opportunities"):
                return
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_opportunity_generations (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    parent_generation_id TEXT, revision INTEGER NOT NULL DEFAULT 0,
                    payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_opportunity_workspace_hash ON py_opportunity_generations(workspace_id,request_hash,status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_opportunity_updated ON py_opportunity_generations(updated_at DESC)")

    def _connect(self) -> psycopg.Connection:
        return get_pool(self.database_url).connection()

    async def ping(self) -> None:
        with self._connect() as connection: connection.execute("SELECT 1").fetchone()

    async def save(self, generation: OpportunityResponse) -> OpportunityResponse:
        snapshot = generation.model_copy(deep=True)
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO py_opportunity_generations
                    (id,workspace_id,request_hash,status,parent_generation_id,revision,payload,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET workspace_id=EXCLUDED.workspace_id,
                    request_hash=EXCLUDED.request_hash,status=EXCLUDED.status,
                    parent_generation_id=EXCLUDED.parent_generation_id,revision=EXCLUDED.revision,
                    payload=EXCLUDED.payload,updated_at=EXCLUDED.updated_at
            """, (snapshot.id,snapshot.request.workspace_id,snapshot.request_hash,snapshot.status.value,
                    snapshot.parent_generation_id,snapshot.revision,Jsonb(snapshot.model_dump(mode="json")),
                    snapshot.created_at,snapshot.updated_at))
        return generation

    async def get(self, generation_id: str) -> OpportunityResponse:
        with self._connect() as connection:
            row=connection.execute("SELECT payload FROM py_opportunity_generations WHERE id=%s",(generation_id,)).fetchone()
        if row is None: raise OpportunityNotFoundError(generation_id)
        return OpportunityResponse.model_validate(row["payload"])

    async def list(self, limit: int = 50, workspace_id: str | None = None) -> list[OpportunityResponse]:
        query="SELECT payload FROM py_opportunity_generations"
        params: tuple[Any,...]=()
        if workspace_id is not None:
            query += " WHERE workspace_id=%s"; params=(workspace_id,)
        query += " ORDER BY updated_at DESC LIMIT %s"; params += (limit,)
        with self._connect() as connection: rows=connection.execute(query,params).fetchall()
        return [OpportunityResponse.model_validate(row["payload"]) for row in rows]

    async def find_by_request_hash(self, request_hash: str, workspace_id: str) -> OpportunityResponse | None:
        with self._connect() as connection:
            row=connection.execute("SELECT payload FROM py_opportunity_generations WHERE workspace_id=%s AND request_hash=%s AND status=%s ORDER BY updated_at DESC LIMIT 1",(workspace_id,request_hash,OpportunityGenerationStatus.COMPLETED.value)).fetchone()
        return OpportunityResponse.model_validate(row["payload"]) if row else None

    async def release_workflow_adoptions(self, workflow_id: str, workspace_id: str) -> list[OpportunityResponse]:
        previous=[]
        with self._connect() as connection:
            rows=connection.execute("SELECT id,payload FROM py_opportunity_generations WHERE workspace_id=%s FOR UPDATE",(workspace_id,)).fetchall()
            for row in rows:
                generation=OpportunityResponse.model_validate(row["payload"])
                released=_release_workflow_adoptions(generation,workflow_id)
                if released is None: continue
                previous.append(generation.model_copy(deep=True))
                connection.execute("UPDATE py_opportunity_generations SET payload=%s,updated_at=%s WHERE id=%s AND workspace_id=%s",(Jsonb(released.model_dump(mode="json")),released.updated_at,row["id"],workspace_id))
        return previous


def build_opportunity_repository(*, database_url: str, database_path: Path | str) -> OpportunityRepository:
    return PostgresOpportunityRepository(database_url) if database_url else SQLiteOpportunityRepository(database_path)

    async def ping(self) -> None:
        return None


class SQLiteOpportunityRepository:
    """Stores complete generation JSON snapshots in the workflow SQLite database."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunity_generations (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    parent_generation_id TEXT,
                    revision INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_opportunity_updated_at "
                "ON opportunity_generations(updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_opportunity_workspace_hash "
                "ON opportunity_generations(workspace_id, request_hash, status)"
            )
        self.database_path.chmod(0o600)

    async def ping(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    async def save(self, generation: OpportunityResponse) -> OpportunityResponse:
        snapshot = generation.model_copy(deep=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO opportunity_generations (
                    id, workspace_id, request_hash, status, parent_generation_id,
                    revision, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    request_hash = excluded.request_hash,
                    status = excluded.status,
                    parent_generation_id = excluded.parent_generation_id,
                    revision = excluded.revision,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot.id,
                    snapshot.request.workspace_id,
                    snapshot.request_hash,
                    snapshot.status.value,
                    snapshot.parent_generation_id,
                    snapshot.revision,
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                    snapshot.updated_at.isoformat(),
                ),
            )
        return generation

    async def get(self, generation_id: str) -> OpportunityResponse:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM opportunity_generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
        if row is None:
            raise OpportunityNotFoundError(generation_id)
        return OpportunityResponse.model_validate_json(row["payload"])

    async def list(
        self, limit: int = 50, workspace_id: str | None = None
    ) -> list[OpportunityResponse]:
        with self._connect() as connection:
            if workspace_id is None:
                rows = connection.execute(
                    "SELECT payload FROM opportunity_generations "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM opportunity_generations "
                    "WHERE workspace_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (workspace_id, limit),
                ).fetchall()
        return [OpportunityResponse.model_validate_json(row["payload"]) for row in rows]

    async def find_by_request_hash(
        self, request_hash: str, workspace_id: str
    ) -> OpportunityResponse | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM opportunity_generations
                WHERE workspace_id = ? AND request_hash = ? AND status = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (
                    workspace_id,
                    request_hash,
                    OpportunityGenerationStatus.COMPLETED.value,
                ),
            ).fetchone()
        if row is None:
            return None
        return OpportunityResponse.model_validate_json(row["payload"])

    async def release_workflow_adoptions(
        self, workflow_id: str, workspace_id: str
    ) -> list[OpportunityResponse]:
        """Unlink every snapshot that references a workflow in one SQLite transaction."""

        previous_snapshots: list[OpportunityResponse] = []
        with self._connect() as connection:
            # Keep the scan and all JSON snapshot rewrites together. This also prevents
            # another SQLite writer from committing between individual generations.
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, payload FROM opportunity_generations WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
            for row in rows:
                generation = OpportunityResponse.model_validate_json(row["payload"])
                released = _release_workflow_adoptions(generation, workflow_id)
                if released is None:
                    continue
                previous_snapshots.append(generation.model_copy(deep=True))
                connection.execute(
                    """
                    UPDATE opportunity_generations
                    SET payload = ?, updated_at = ?
                    WHERE id = ? AND workspace_id = ?
                    """,
                    (
                        released.model_dump_json(),
                        released.updated_at.isoformat(),
                        row["id"],
                        workspace_id,
                    ),
                )
        return previous_snapshots
