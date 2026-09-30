from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.postgres import begin_schema_setup, get_pool

from app.domain.models import WorkflowRun


class WorkflowNotFoundError(LookupError):
    pass


class WorkflowConflictError(RuntimeError):
    pass


class WorkflowRepository(Protocol):
    async def save(self, workflow: WorkflowRun) -> WorkflowRun: ...

    async def get(
        self, workflow_id: str, workspace_id: str | None = None
    ) -> WorkflowRun: ...

    async def list(
        self,
        workspace_id: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowRun]: ...

    async def ping(self) -> None: ...

    async def delete(self, workflow_id: str, workspace_id: str | None = None) -> None: ...


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self._items: dict[str, WorkflowRun] = {}

    async def save(self, workflow: WorkflowRun) -> WorkflowRun:
        current = self._items.get(workflow.id)
        if current is not None:
            if current.revision_count != workflow.revision_count:
                raise WorkflowConflictError(workflow.id)
            workflow.revision_count += 1
        self._items[workflow.id] = workflow.model_copy(deep=True)
        return workflow

    async def get(
        self, workflow_id: str, workspace_id: str | None = None
    ) -> WorkflowRun:
        workflow = self._items.get(workflow_id)
        if workflow is None or (
            workspace_id is not None and workflow.input.workspace_id != workspace_id
        ):
            raise WorkflowNotFoundError(workflow_id)
        return workflow.model_copy(deep=True)

    async def list(
        self,
        workspace_id: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        return sorted(
            (
                item.model_copy(deep=True)
                for item in self._items.values()
                if workspace_id is None or item.input.workspace_id == workspace_id
            ),
            key=lambda item: item.updated_at,
            reverse=True,
        )[offset : offset + limit]

    async def ping(self) -> None:
        return None

    async def delete(self, workflow_id: str, workspace_id: str | None = None) -> None:
        workflow = self._items.get(workflow_id)
        if workflow is None or (
            workspace_id is not None and workflow.input.workspace_id != workspace_id
        ):
            raise WorkflowNotFoundError(workflow_id)
        del self._items[workflow_id]


class SQLiteWorkflowRepository:
    """以單一 JSON snapshot 保存完整工作流，適合本機版與早期整合。"""

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
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    payload TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(workflows)").fetchall()
            }
            if "workspace_id" not in columns:
                connection.execute(
                    "ALTER TABLE workflows ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'default'"
                )
            if "revision" not in columns:
                connection.execute(
                    "ALTER TABLE workflows ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflows_updated_at ON workflows(updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflows_workspace_updated "
                "ON workflows(workspace_id, updated_at DESC)"
            )
        self.database_path.chmod(0o600)

    async def save(self, workflow: WorkflowRun) -> WorkflowRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision FROM workflows WHERE id = ?", (workflow.id,)
            ).fetchone()
            if row is None:
                snapshot = workflow.model_copy(deep=True)
                connection.execute(
                    """
                    INSERT INTO workflows
                        (id, workspace_id, payload, revision, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.id,
                        snapshot.input.workspace_id,
                        snapshot.model_dump_json(),
                        snapshot.revision_count,
                        snapshot.created_at.isoformat(),
                        snapshot.updated_at.isoformat(),
                    ),
                )
            else:
                stored_revision = int(row["revision"])
                if stored_revision != workflow.revision_count:
                    raise WorkflowConflictError(workflow.id)
                workflow.revision_count += 1
                snapshot = workflow.model_copy(deep=True)
                cursor = connection.execute(
                    """
                    UPDATE workflows
                    SET workspace_id = ?, payload = ?, revision = ?, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        snapshot.input.workspace_id,
                        snapshot.model_dump_json(),
                        snapshot.revision_count,
                        snapshot.updated_at.isoformat(),
                        snapshot.id,
                        stored_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    workflow.revision_count -= 1
                    raise WorkflowConflictError(workflow.id)
        return workflow

    async def get(
        self, workflow_id: str, workspace_id: str | None = None
    ) -> WorkflowRun:
        with self._connect() as connection:
            if workspace_id is None:
                row = connection.execute(
                    "SELECT payload FROM workflows WHERE id = ?", (workflow_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT payload FROM workflows WHERE id = ? AND workspace_id = ?",
                    (workflow_id, workspace_id),
                ).fetchone()
        if row is None:
            raise WorkflowNotFoundError(workflow_id)
        return WorkflowRun.model_validate_json(row["payload"])

    async def list(
        self,
        workspace_id: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        with self._connect() as connection:
            if workspace_id is None:
                rows = connection.execute(
                    "SELECT payload FROM workflows ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM workflows WHERE workspace_id = ? "
                    "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (workspace_id, limit, offset),
                ).fetchall()
        return [WorkflowRun.model_validate_json(row["payload"]) for row in rows]

    async def ping(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    async def delete(self, workflow_id: str, workspace_id: str | None = None) -> None:
        with self._connect() as connection:
            if workspace_id is None:
                cursor = connection.execute(
                    "DELETE FROM workflows WHERE id = ?", (workflow_id,)
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM workflows WHERE id = ? AND workspace_id = ?",
                    (workflow_id, workspace_id),
                )
        if cursor.rowcount == 0:
            raise WorkflowNotFoundError(workflow_id)


class PostgresWorkflowRepository:
    """PostgreSQL persistence plus queryable material-center projections."""

    def __init__(self, database_url: str) -> None:
        if not database_url.strip():
            raise ValueError("database_url is required")
        self.database_url = database_url
        self._initialize()

    def _connect(self) -> psycopg.Connection:
        return get_pool(self.database_url).connection()

    def _initialize(self) -> None:
        with self._connect() as connection:
            if not begin_schema_setup(connection, "workflow"):
                return
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS py_workflows (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_py_workflows_workspace_updated "
                "ON py_workflows(workspace_id, updated_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS py_material_projects (
                    workflow_id TEXT PRIMARY KEY REFERENCES py_workflows(id) ON DELETE CASCADE,
                    workspace_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    workflow_status TEXT NOT NULL,
                    script JSONB,
                    production_package JSONB,
                    reference_analysis JSONB,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_py_material_projects_workspace_updated "
                "ON py_material_projects(workspace_id, updated_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS py_material_script_sections (
                    workflow_id TEXT NOT NULL REFERENCES py_workflows(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    payload JSONB NOT NULL,
                    PRIMARY KEY (workflow_id, position)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS py_material_storyboard_shots (
                    workflow_id TEXT NOT NULL REFERENCES py_workflows(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    shot_number INTEGER NOT NULL,
                    section_title TEXT NOT NULL DEFAULT '',
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    payload JSONB NOT NULL,
                    PRIMARY KEY (workflow_id, position)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS py_material_visual_cues (
                    workflow_id TEXT NOT NULL REFERENCES py_workflows(id) ON DELETE CASCADE,
                    cue_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    shot_number INTEGER NOT NULL,
                    cue_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    start_seconds INTEGER NOT NULL,
                    duration_seconds INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    PRIMARY KEY (workflow_id, cue_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_py_material_visual_cues_timeline "
                "ON py_material_visual_cues(workflow_id, start_seconds, position)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS py_material_generated_assets (
                    workflow_id TEXT NOT NULL REFERENCES py_workflows(id) ON DELETE CASCADE,
                    asset_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    shot_number INTEGER,
                    payload JSONB NOT NULL,
                    PRIMARY KEY (workflow_id, asset_id)
                )
                """
            )

    def _sync_material_center(
        self, connection: psycopg.Connection, workflow: WorkflowRun
    ) -> None:
        artifacts = workflow.artifacts or {}
        script = artifacts.get("script") or None
        production = artifacts.get("production_package") or None
        references = artifacts.get("reference_analysis") or (
            script.get("reference_analysis") if isinstance(script, dict) else None
        )
        connection.execute(
            """
            INSERT INTO py_material_projects
                (workflow_id, workspace_id, topic, workflow_status, script,
                 production_package, reference_analysis, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workflow_id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                topic = EXCLUDED.topic,
                workflow_status = EXCLUDED.workflow_status,
                script = EXCLUDED.script,
                production_package = EXCLUDED.production_package,
                reference_analysis = EXCLUDED.reference_analysis,
                updated_at = EXCLUDED.updated_at
            """,
            (
                workflow.id,
                workflow.input.workspace_id,
                workflow.input.topic,
                workflow.status.value,
                Jsonb(script) if script is not None else None,
                Jsonb(production) if production is not None else None,
                Jsonb(references) if references is not None else None,
                workflow.updated_at,
            ),
        )
        for table in (
            "py_material_script_sections",
            "py_material_storyboard_shots",
            "py_material_visual_cues",
            "py_material_generated_assets",
        ):
            connection.execute(f"DELETE FROM {table} WHERE workflow_id = %s", (workflow.id,))

        for position, section in enumerate((script or {}).get("sections", [])):
            connection.execute(
                """
                INSERT INTO py_material_script_sections
                    (workflow_id, position, title, content, payload)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    workflow.id,
                    position,
                    str(section.get("title") or section.get("section") or ""),
                    str(section.get("content") or section.get("script") or section.get("text") or ""),
                    Jsonb(section),
                ),
            )
        for position, shot in enumerate((production or {}).get("storyboard", [])):
            connection.execute(
                """
                INSERT INTO py_material_storyboard_shots
                    (workflow_id, position, shot_number, section_title,
                     duration_seconds, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    workflow.id,
                    position,
                    int(shot.get("shot") or position + 1),
                    str(shot.get("section") or ""),
                    int(shot.get("duration_seconds") or 0),
                    Jsonb(shot),
                ),
            )
        for position, cue in enumerate((production or {}).get("visual_cues", [])):
            connection.execute(
                """
                INSERT INTO py_material_visual_cues
                    (workflow_id, cue_id, position, shot_number, cue_type, label,
                     start_seconds, duration_seconds, source, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    workflow.id,
                    str(cue.get("id") or f"cue-{position + 1}"),
                    position,
                    int(cue.get("shot") or 1),
                    str(cue.get("cue_type") or "broll"),
                    str(cue.get("label") or ""),
                    int(cue.get("start_seconds") or 0),
                    int(cue.get("duration_seconds") or 0),
                    str(cue.get("source") or "ai"),
                    Jsonb(cue),
                ),
            )
        for position, asset in enumerate((production or {}).get("generated_assets", [])):
            connection.execute(
                """
                INSERT INTO py_material_generated_assets
                    (workflow_id, asset_id, position, kind, shot_number, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    workflow.id,
                    str(asset.get("id") or f"asset-{position + 1}"),
                    position,
                    str(asset.get("kind") or ""),
                    int(asset["shot"]) if asset.get("shot") is not None else None,
                    Jsonb(asset),
                ),
            )

    async def save(self, workflow: WorkflowRun) -> WorkflowRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision FROM py_workflows WHERE id = %s FOR UPDATE",
                (workflow.id,),
            ).fetchone()
            snapshot = workflow.model_copy(deep=True)
            if row is None:
                connection.execute(
                    """
                    INSERT INTO py_workflows
                        (id, workspace_id, payload, revision, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        snapshot.id,
                        snapshot.input.workspace_id,
                        Jsonb(snapshot.model_dump(mode="json")),
                        snapshot.revision_count,
                        snapshot.created_at,
                        snapshot.updated_at,
                    ),
                )
            else:
                stored_revision = int(row["revision"])
                if stored_revision != workflow.revision_count:
                    raise WorkflowConflictError(workflow.id)
                workflow.revision_count += 1
                snapshot = workflow.model_copy(deep=True)
                cursor = connection.execute(
                    """
                    UPDATE py_workflows
                    SET workspace_id = %s, payload = %s, revision = %s, updated_at = %s
                    WHERE id = %s AND revision = %s
                    """,
                    (
                        snapshot.input.workspace_id,
                        Jsonb(snapshot.model_dump(mode="json")),
                        snapshot.revision_count,
                        snapshot.updated_at,
                        snapshot.id,
                        stored_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    workflow.revision_count -= 1
                    raise WorkflowConflictError(workflow.id)
            self._sync_material_center(connection, snapshot)
        return workflow

    async def get(
        self, workflow_id: str, workspace_id: str | None = None
    ) -> WorkflowRun:
        query = "SELECT payload FROM py_workflows WHERE id = %s"
        params: tuple[object, ...] = (workflow_id,)
        if workspace_id is not None:
            query += " AND workspace_id = %s"
            params += (workspace_id,)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise WorkflowNotFoundError(workflow_id)
        return WorkflowRun.model_validate(row["payload"])

    async def list(
        self,
        workspace_id: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        query = "SELECT payload FROM py_workflows"
        params: tuple[object, ...] = ()
        if workspace_id is not None:
            query += " WHERE workspace_id = %s"
            params = (workspace_id,)
        query += " ORDER BY updated_at DESC LIMIT %s OFFSET %s"
        params += (limit, offset)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [WorkflowRun.model_validate(row["payload"]) for row in rows]

    async def ping(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    async def delete(self, workflow_id: str, workspace_id: str | None = None) -> None:
        query = "DELETE FROM py_workflows WHERE id = %s"
        params: tuple[object, ...] = (workflow_id,)
        if workspace_id is not None:
            query += " AND workspace_id = %s"
            params += (workspace_id,)
        with self._connect() as connection:
            cursor = connection.execute(query, params)
        if cursor.rowcount == 0:
            raise WorkflowNotFoundError(workflow_id)


def build_workflow_repository(
    *, database_url: str, database_path: Path | str
) -> WorkflowRepository:
    if database_url.strip():
        return PostgresWorkflowRepository(database_url)
    return SQLiteWorkflowRepository(database_path)
