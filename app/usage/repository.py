from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.postgres import begin_schema_setup, get_pool


class GenerationQuotaExceededError(RuntimeError):
    def __init__(self, limit: int, resets_at: datetime) -> None:
        super().__init__("daily generation quota exceeded")
        self.limit = limit
        self.resets_at = resets_at


def _platform_usage_summary(
    *,
    days: int,
    events: list[dict[str, Any]],
    workspaces: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
    workflows: list[dict[str, Any]],
    feedback: list[dict[str, Any]],
) -> dict[str, Any]:
    workspace_stats: dict[str, dict[str, Any]] = {}
    for item in workspaces:
        workspace_id = str(item["id"])
        workspace_stats[workspace_id] = {
            "workspace_id": workspace_id,
            "workspace_name": str(item.get("name") or workspace_id),
            "member_count": 0,
            "active_members": set(),
            "event_count": 0,
            "workflow_count": 0,
            "generation_count": 0,
            "failure_count": 0,
            "feedback_count": 0,
            "token_units": 0,
            "last_active_at": None,
        }

    member_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    user_stats: dict[tuple[str, str], dict[str, Any]] = {}
    for item in memberships:
        workspace_id = str(item["workspace_id"])
        user_id = str(item["user_id"])
        workspace = workspace_stats.setdefault(
            workspace_id,
            {
                "workspace_id": workspace_id,
                "workspace_name": workspace_id,
                "member_count": 0,
                "active_members": set(),
                "event_count": 0,
                "workflow_count": 0,
                "generation_count": 0,
                "failure_count": 0,
                "feedback_count": 0,
                "token_units": 0,
                "last_active_at": None,
            },
        )
        workspace["member_count"] += 1
        member_lookup[(workspace_id, user_id)] = item
        user_stats[(workspace_id, user_id)] = {
            "workspace_id": workspace_id,
            "workspace_name": workspace["workspace_name"],
            "user_id": user_id,
            "email": str(item.get("email") or ""),
            "display_name": str(item.get("display_name") or item.get("email") or user_id),
            "role": str(item.get("role") or "member"),
            "event_count": 0,
            "page_views": 0,
            "workflow_opens": 0,
            "asset_actions": 0,
            "last_active_at": None,
        }

    event_counts: dict[str, int] = {}
    for event in events:
        workspace_id = str(event["workspace_id"])
        actor_id = str(event.get("actor_id") or "")
        event_name = str(event["event_name"])
        occurred_at = event.get("occurred_at")
        workspace = workspace_stats.setdefault(
            workspace_id,
            {
                "workspace_id": workspace_id,
                "workspace_name": workspace_id,
                "member_count": 0,
                "active_members": set(),
                "event_count": 0,
                "workflow_count": 0,
                "generation_count": 0,
                "failure_count": 0,
                "feedback_count": 0,
                "token_units": 0,
                "last_active_at": None,
            },
        )
        workspace["event_count"] += 1
        if event_name == "generation.started":
            workspace["generation_count"] += 1
        elif event_name == "generation.failed":
            workspace["failure_count"] += 1
        elif event_name == "generation.completed":
            workspace["token_units"] += int(event.get("units") or 0)
        if workspace["last_active_at"] is None or occurred_at > workspace["last_active_at"]:
            workspace["last_active_at"] = occurred_at
        event_counts[event_name] = event_counts.get(event_name, 0) + 1
        user = user_stats.get((workspace_id, actor_id))
        if user is not None:
            workspace["active_members"].add(actor_id)
            user["event_count"] += 1
            user["page_views"] += int(event_name == "page.viewed")
            user["workflow_opens"] += int(event_name == "workflow.opened")
            user["asset_actions"] += int(
                event_name.startswith("artifact.") or event_name.startswith("publication.")
            )
            if user["last_active_at"] is None or occurred_at > user["last_active_at"]:
                user["last_active_at"] = occurred_at

    for item in workflows:
        workspace = workspace_stats.get(str(item["workspace_id"]))
        if workspace is not None:
            workspace["workflow_count"] += 1
    for item in feedback:
        workspace = workspace_stats.get(str(item["workspace_id"]))
        if workspace is not None:
            workspace["feedback_count"] += 1

    recent_events = []
    for event in events[:50]:
        workspace_id = str(event["workspace_id"])
        actor_id = str(event.get("actor_id") or "")
        member = member_lookup.get((workspace_id, actor_id), {})
        recent_events.append(
            {
                "workspace_id": workspace_id,
                "workspace_name": workspace_stats.get(workspace_id, {}).get("workspace_name", workspace_id),
                "actor_id": actor_id,
                "actor_name": member.get("display_name") or member.get("email") or actor_id,
                "event_name": event["event_name"],
                "workflow_id": event.get("workflow_id"),
                "metadata": event.get("metadata") or {},
                "occurred_at": event.get("occurred_at"),
            }
        )

    workspace_rows = []
    for item in workspace_stats.values():
        row = dict(item)
        row["active_member_count"] = len(row.pop("active_members"))
        workspace_rows.append(row)
    workspace_rows.sort(key=lambda item: str(item.get("last_active_at") or ""), reverse=True)
    user_rows = sorted(
        user_stats.values(),
        key=lambda item: str(item.get("last_active_at") or ""),
        reverse=True,
    )
    active_user_ids = {
        item["user_id"] for item in user_rows if item.get("last_active_at") is not None
    }
    workflow_status_counts: dict[str, int] = {}
    for item in workflows:
        status = str(item.get("status") or "unknown")
        workflow_status_counts[status] = workflow_status_counts.get(status, 0) + 1
    return {
        "window_days": days,
        "generated_at": datetime.now(timezone.utc),
        "totals": {
            "workspaces": len(workspace_rows),
            "active_workspaces": sum(1 for item in workspace_rows if item["event_count"] > 0),
            "members": len({item["user_id"] for item in user_rows}),
            "active_members": len(active_user_ids),
            "events": len(events),
            "workflows": len(workflows),
            "generation_started": event_counts.get("generation.started", 0),
            "generation_completed": event_counts.get("generation.completed", 0),
            "generation_failed": event_counts.get("generation.failed", 0),
            "feedback": len(feedback),
            "token_units": sum(
                int(item.get("units") or 0)
                for item in events
                if item["event_name"] == "generation.completed"
            ),
        },
        "workflow_statuses": workflow_status_counts,
        "events_by_name": [
            {"event_name": name, "count": count}
            for name, count in sorted(event_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        "workspaces": workspace_rows,
        "users": user_rows,
        "recent_events": recent_events,
    }


class SQLiteUsageRepository:
    """Small persistent event store suitable for a single-instance MVP."""

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
                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    units INTEGER NOT NULL DEFAULT 1,
                    workflow_id TEXT,
                    provider TEXT,
                    model TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    occurred_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_entries (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    rating TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_bug_reports (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    workflow_id TEXT,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    steps_to_reproduce TEXT NOT NULL DEFAULT '',
                    expected_behavior TEXT NOT NULL DEFAULT '',
                    actual_behavior TEXT NOT NULL DEFAULT '',
                    page TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '{}',
                    screenshot_key TEXT,
                    screenshot_name TEXT,
                    screenshot_mime TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    owner_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_workspace_event_time "
                "ON usage_events(workspace_id, event_name, occurred_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_workspace_workflow "
                "ON feedback_entries(workspace_id, workflow_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_bug_reports_status_time "
                "ON beta_bug_reports(status, created_at DESC)"
            )
        self.database_path.chmod(0o600)

    async def claim_generation(
        self,
        workspace_id: str,
        *,
        actor_id: str,
        workflow_id: str,
        provider: str,
        model: str,
        daily_limit: int,
    ) -> str:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        resets_at = day_start + timedelta(days=1)
        event_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT id FROM usage_events
                WHERE workspace_id = ? AND workflow_id = ?
                  AND event_name = 'generation.started'
                LIMIT 1
                """,
                (workspace_id, workflow_id),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return str(existing["id"])
            used = connection.execute(
                """
                SELECT COALESCE(SUM(units), 0) AS used
                FROM usage_events
                WHERE workspace_id = ? AND event_name = 'generation.started'
                  AND occurred_at >= ?
                """,
                (workspace_id, day_start.isoformat()),
            ).fetchone()["used"]
            if int(used) >= daily_limit:
                connection.rollback()
                raise GenerationQuotaExceededError(daily_limit, resets_at)
            connection.execute(
                """
                INSERT INTO usage_events
                    (id, workspace_id, actor_id, event_name, units, workflow_id,
                     provider, model, metadata, occurred_at)
                VALUES (?, ?, ?, 'generation.started', 1, ?, ?, ?, '{}', ?)
                """,
                (
                    event_id,
                    workspace_id,
                    actor_id,
                    workflow_id,
                    provider,
                    model,
                    now.isoformat(),
                ),
            )
        return event_id

    async def record_event(
        self,
        workspace_id: str,
        event_name: str,
        *,
        actor_id: str = "local-user",
        workflow_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        units: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid4())
        safe_metadata = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO usage_events
                    (id, workspace_id, actor_id, event_name, units, workflow_id,
                     provider, model, metadata, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    workspace_id,
                    actor_id,
                    event_name,
                    units,
                    workflow_id,
                    provider,
                    model,
                    safe_metadata,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return event_id

    async def add_feedback(
        self,
        workspace_id: str,
        workflow_id: str,
        *,
        artifact_type: str,
        rating: str,
        note: str,
        actor_id: str,
    ) -> dict[str, Any]:
        feedback_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO feedback_entries
                    (id, workspace_id, workflow_id, artifact_type, rating, note,
                     actor_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    workspace_id,
                    workflow_id,
                    artifact_type,
                    rating,
                    note,
                    actor_id,
                    created_at.isoformat(),
                ),
            )
        await self.record_event(
            workspace_id,
            "feedback.submitted",
            actor_id=actor_id,
            workflow_id=workflow_id,
            metadata={"artifact_type": artifact_type, "rating": rating},
        )
        return {
            "id": feedback_id,
            "workflow_id": workflow_id,
            "artifact_type": artifact_type,
            "rating": rating,
            "note": note,
            "created_at": created_at,
        }

    async def create_bug_report(self, workspace_id: str, *, actor_id: str, data: dict[str, Any]) -> dict[str, Any]:
        report_id = str(data.get("id") or uuid4())
        created_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO beta_bug_reports
                (id,workspace_id,actor_id,workflow_id,category,severity,description,
                 steps_to_reproduce,expected_behavior,actual_behavior,page,context,
                 screenshot_key,screenshot_name,screenshot_mime,status,owner_note,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report_id, workspace_id, actor_id, data.get("workflow_id"), data["category"],
                 data["severity"], data.get("description", ""), data.get("steps_to_reproduce", ""),
                 data.get("expected_behavior", ""), data.get("actual_behavior", ""), data.get("page", ""),
                 json.dumps(data.get("context") or {}, ensure_ascii=False, separators=(",", ":")),
                 data.get("screenshot_key"), data.get("screenshot_name"), data.get("screenshot_mime"),
                 "new", "", created_at.isoformat(), created_at.isoformat()),
            )
        await self.record_event(workspace_id, "bug_report.submitted", actor_id=actor_id,
                                workflow_id=data.get("workflow_id"), metadata={"category": data["category"], "severity": data["severity"]})
        return {"id": report_id, "status": "new", "created_at": created_at}

    async def list_bug_reports(self, *, limit: int = 100, report_status: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE r.status = ?" if report_status else ""
        params: tuple[Any, ...] = (report_status, limit) if report_status else (limit,)
        with self._connect() as connection:
            tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            joins = ""
            select_identity = "r.workspace_id AS workspace_name, r.actor_id AS actor_name, '' AS actor_email"
            if {"auth_workspaces", "auth_profiles"}.issubset(tables):
                joins = "LEFT JOIN auth_workspaces w ON w.id=r.workspace_id LEFT JOIN auth_profiles p ON p.id=r.actor_id"
                select_identity = "COALESCE(w.name,r.workspace_id) AS workspace_name, COALESCE(p.display_name,p.email,r.actor_id) AS actor_name, COALESCE(p.email,'') AS actor_email"
            rows = connection.execute(
                f"SELECT r.*, {select_identity} FROM beta_bug_reports r {joins} {where} ORDER BY r.created_at DESC LIMIT ?",
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["context"] = json.loads(item.get("context") or "{}")
            item["has_screenshot"] = bool(item.pop("screenshot_key", None)); result.append(item)
        return result

    async def get_bug_report_screenshot(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT screenshot_key,screenshot_name,screenshot_mime FROM beta_bug_reports WHERE id=?", (report_id,)).fetchone()
        return dict(row) if row and row["screenshot_key"] else None

    async def update_bug_report(self, report_id: str, *, status: str, owner_note: str) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute("UPDATE beta_bug_reports SET status=?,owner_note=?,updated_at=? WHERE id=?", (status, owner_note, now, report_id))
            if cursor.rowcount == 0: return None
        return {"id": report_id, "status": status, "owner_note": owner_note, "updated_at": now}

    async def usage_summary(self, workspace_id: str, daily_limit: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as connection:
            used = connection.execute(
                """
                SELECT COALESCE(SUM(units), 0) AS used
                FROM usage_events
                WHERE workspace_id = ? AND event_name = 'generation.started'
                  AND occurred_at >= ?
                """,
                (workspace_id, day_start.isoformat()),
            ).fetchone()["used"]
            feedback_count = connection.execute(
                "SELECT COUNT(*) AS count FROM feedback_entries WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()["count"]
        used_int = int(used)
        return {
            "workspace_id": workspace_id,
            "generation": {
                "used": used_int,
                "limit": daily_limit,
                "remaining": max(0, daily_limit - used_int),
                "resets_at": (day_start + timedelta(days=1)).isoformat(),
            },
            "feedback_count": int(feedback_count),
        }

    async def platform_usage_summary(self, days: int) -> dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._connect() as connection:
            events = [dict(row) for row in connection.execute(
                "SELECT workspace_id,actor_id,event_name,units,workflow_id,metadata,occurred_at FROM usage_events WHERE occurred_at >= ? ORDER BY occurred_at DESC",
                (cutoff.isoformat(),),
            ).fetchall()]
            for event in events:
                event["metadata"] = json.loads(event.get("metadata") or "{}")
            tables = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            workspaces = (
                [dict(row) for row in connection.execute("SELECT id,name FROM auth_workspaces").fetchall()]
                if "auth_workspaces" in tables else []
            )
            memberships = (
                [dict(row) for row in connection.execute(
                    "SELECT m.workspace_id,m.user_id,m.role,p.email,p.display_name FROM auth_memberships m JOIN auth_profiles p ON p.id=m.user_id"
                ).fetchall()]
                if {"auth_memberships", "auth_profiles"}.issubset(tables) else []
            )
            workflows = (
                [dict(row) for row in connection.execute(
                    "SELECT workspace_id,json_extract(payload,'$.status') status FROM workflows WHERE created_at >= ?",
                    (cutoff.isoformat(),),
                ).fetchall()]
                if "workflows" in tables else []
            )
            feedback = (
                [dict(row) for row in connection.execute(
                    "SELECT workspace_id,actor_id,rating,created_at FROM feedback_entries WHERE created_at >= ?",
                    (cutoff.isoformat(),),
                ).fetchall()]
                if "feedback_entries" in tables else []
            )
        return _platform_usage_summary(
            days=days,
            events=events,
            workspaces=workspaces,
            memberships=memberships,
            workflows=workflows,
            feedback=feedback,
        )

    async def ping(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()


class PostgresUsageRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        with self._connect() as connection:
            if not begin_schema_setup(connection, "usage"):
                return
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_usage_events (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                    event_name TEXT NOT NULL, units INTEGER NOT NULL DEFAULT 1,
                    workflow_id TEXT, provider TEXT, model TEXT, metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    occurred_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_feedback_entries (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL, rating TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                    actor_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_beta_bug_reports (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                    workflow_id TEXT, category TEXT NOT NULL, severity TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', steps_to_reproduce TEXT NOT NULL DEFAULT '',
                    expected_behavior TEXT NOT NULL DEFAULT '', actual_behavior TEXT NOT NULL DEFAULT '',
                    page TEXT NOT NULL DEFAULT '', context JSONB NOT NULL DEFAULT '{}'::jsonb,
                    screenshot_key TEXT, screenshot_name TEXT, screenshot_mime TEXT,
                    status TEXT NOT NULL DEFAULT 'new', owner_note TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_usage_workspace_event_time ON py_usage_events(workspace_id,event_name,occurred_at DESC)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_py_usage_generation_workflow ON py_usage_events(workspace_id,workflow_id,event_name) WHERE event_name='generation.started'")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_feedback_workspace_workflow ON py_feedback_entries(workspace_id,workflow_id,created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_bug_reports_status_time ON py_beta_bug_reports(status,created_at DESC)")

    def _connect(self) -> psycopg.Connection:
        return get_pool(self.database_url).connection()

    async def claim_generation(self, workspace_id: str, *, actor_id: str, workflow_id: str, provider: str, model: str, daily_limit: int) -> str:
        now=datetime.now(timezone.utc); day_start=now.replace(hour=0,minute=0,second=0,microsecond=0); resets_at=day_start+timedelta(days=1)
        with self._connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(f"quota:{workspace_id}:{day_start.date()}",))
            existing=connection.execute("SELECT id FROM py_usage_events WHERE workspace_id=%s AND workflow_id=%s AND event_name='generation.started'",(workspace_id,workflow_id)).fetchone()
            if existing: return str(existing["id"])
            used=connection.execute("SELECT COALESCE(SUM(units),0) used FROM py_usage_events WHERE workspace_id=%s AND event_name='generation.started' AND occurred_at >= %s",(workspace_id,day_start)).fetchone()["used"]
            if int(used)>=daily_limit: raise GenerationQuotaExceededError(daily_limit,resets_at)
            event_id=str(uuid4())
            connection.execute("INSERT INTO py_usage_events (id,workspace_id,actor_id,event_name,units,workflow_id,provider,model,metadata,occurred_at) VALUES (%s,%s,%s,'generation.started',1,%s,%s,%s,%s,%s)",(event_id,workspace_id,actor_id,workflow_id,provider,model,Jsonb({}),now))
        return event_id

    async def record_event(self, workspace_id: str, event_name: str, *, actor_id: str="local-user", workflow_id: str|None=None, provider: str|None=None, model: str|None=None, units: int=1, metadata: dict[str,Any]|None=None) -> str:
        event_id=str(uuid4())
        with self._connect() as connection:
            connection.execute("INSERT INTO py_usage_events (id,workspace_id,actor_id,event_name,units,workflow_id,provider,model,metadata,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(event_id,workspace_id,actor_id,event_name,units,workflow_id,provider,model,Jsonb(metadata or {}),datetime.now(timezone.utc)))
        return event_id

    async def add_feedback(self, workspace_id: str, workflow_id: str, *, artifact_type: str, rating: str, note: str, actor_id: str) -> dict[str,Any]:
        feedback_id=str(uuid4()); created_at=datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("INSERT INTO py_feedback_entries (id,workspace_id,workflow_id,artifact_type,rating,note,actor_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",(feedback_id,workspace_id,workflow_id,artifact_type,rating,note,actor_id,created_at))
            connection.execute("INSERT INTO py_usage_events (id,workspace_id,actor_id,event_name,units,workflow_id,metadata,occurred_at) VALUES (%s,%s,%s,'feedback.submitted',1,%s,%s,%s)",(str(uuid4()),workspace_id,actor_id,workflow_id,Jsonb({"artifact_type":artifact_type,"rating":rating}),created_at))
        return {"id":feedback_id,"workflow_id":workflow_id,"artifact_type":artifact_type,"rating":rating,"note":note,"created_at":created_at}

    async def create_bug_report(self, workspace_id: str, *, actor_id: str, data: dict[str, Any]) -> dict[str, Any]:
        report_id=str(data.get("id") or uuid4()); created_at=datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("""INSERT INTO py_beta_bug_reports
                (id,workspace_id,actor_id,workflow_id,category,severity,description,steps_to_reproduce,
                 expected_behavior,actual_behavior,page,context,screenshot_key,screenshot_name,screenshot_mime,
                 status,owner_note,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new','',%s,%s)""",
                (report_id,workspace_id,actor_id,data.get("workflow_id"),data["category"],data["severity"],
                 data.get("description",""),data.get("steps_to_reproduce",""),data.get("expected_behavior",""),
                 data.get("actual_behavior",""),data.get("page",""),Jsonb(data.get("context") or {}),
                 data.get("screenshot_key"),data.get("screenshot_name"),data.get("screenshot_mime"),created_at,created_at))
            connection.execute("INSERT INTO py_usage_events (id,workspace_id,actor_id,event_name,units,workflow_id,metadata,occurred_at) VALUES (%s,%s,%s,'bug_report.submitted',1,%s,%s,%s)",
                               (str(uuid4()),workspace_id,actor_id,data.get("workflow_id"),Jsonb({"category":data["category"],"severity":data["severity"]}),created_at))
        return {"id":report_id,"status":"new","created_at":created_at}

    async def list_bug_reports(self, *, limit: int = 100, report_status: str | None = None) -> list[dict[str, Any]]:
        where="WHERE r.status=%s" if report_status else ""; params=(report_status,limit) if report_status else (limit,)
        with self._connect() as connection:
            rows=connection.execute(f"""SELECT r.*,COALESCE(w.name,r.workspace_id) workspace_name,
                COALESCE(p.display_name,p.email,r.actor_id) actor_name,COALESCE(p.email,'') actor_email
                FROM py_beta_bug_reports r LEFT JOIN py_auth_workspaces w ON w.id=r.workspace_id
                LEFT JOIN py_auth_profiles p ON p.id=r.actor_id {where} ORDER BY r.created_at DESC LIMIT %s""",params).fetchall()
        result=[]
        for row in rows:
            item=dict(row); item["has_screenshot"]=bool(item.pop("screenshot_key",None)); result.append(item)
        return result

    async def get_bug_report_screenshot(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row=connection.execute("SELECT screenshot_key,screenshot_name,screenshot_mime FROM py_beta_bug_reports WHERE id=%s",(report_id,)).fetchone()
        return dict(row) if row and row["screenshot_key"] else None

    async def update_bug_report(self, report_id: str, *, status: str, owner_note: str) -> dict[str, Any] | None:
        now=datetime.now(timezone.utc)
        with self._connect() as connection:
            row=connection.execute("UPDATE py_beta_bug_reports SET status=%s,owner_note=%s,updated_at=%s WHERE id=%s RETURNING id",(status,owner_note,now,report_id)).fetchone()
        return {"id":report_id,"status":status,"owner_note":owner_note,"updated_at":now} if row else None

    async def usage_summary(self, workspace_id: str, daily_limit: int) -> dict[str,Any]:
        now=datetime.now(timezone.utc); day_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
        with self._connect() as connection:
            used=connection.execute("SELECT COALESCE(SUM(units),0) used FROM py_usage_events WHERE workspace_id=%s AND event_name='generation.started' AND occurred_at >= %s",(workspace_id,day_start)).fetchone()["used"]
            count=connection.execute("SELECT COUNT(*) count FROM py_feedback_entries WHERE workspace_id=%s",(workspace_id,)).fetchone()["count"]
        return {"workspace_id":workspace_id,"generation":{"used":int(used),"limit":daily_limit,"remaining":max(0,daily_limit-int(used)),"resets_at":(day_start+timedelta(days=1)).isoformat()},"feedback_count":int(count)}

    async def platform_usage_summary(self, days: int) -> dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with self._connect() as connection:
            events = [dict(row) for row in connection.execute(
                "SELECT workspace_id,actor_id,event_name,units,workflow_id,metadata,occurred_at FROM py_usage_events WHERE occurred_at >= %s ORDER BY occurred_at DESC",
                (cutoff,),
            ).fetchall()]
            workspaces = [dict(row) for row in connection.execute(
                "SELECT id,name FROM py_auth_workspaces"
            ).fetchall()]
            memberships = [dict(row) for row in connection.execute(
                "SELECT m.workspace_id,m.user_id,m.role,p.email,p.display_name FROM py_auth_memberships m JOIN py_auth_profiles p ON p.id=m.user_id"
            ).fetchall()]
            workflows = [dict(row) for row in connection.execute(
                "SELECT workspace_id,payload->>'status' status FROM py_workflows WHERE created_at >= %s",
                (cutoff,),
            ).fetchall()]
            feedback = [dict(row) for row in connection.execute(
                "SELECT workspace_id,actor_id,rating,created_at FROM py_feedback_entries WHERE created_at >= %s",
                (cutoff,),
            ).fetchall()]
        return _platform_usage_summary(
            days=days,
            events=events,
            workspaces=workspaces,
            memberships=memberships,
            workflows=workflows,
            feedback=feedback,
        )

    async def ping(self) -> None:
        with self._connect() as connection: connection.execute("SELECT 1").fetchone()


def build_usage_repository(*, database_url: str, database_path: Path | str):
    return PostgresUsageRepository(database_url) if database_url else SQLiteUsageRepository(database_path)
