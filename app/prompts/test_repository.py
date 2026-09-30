from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg

from app.db.postgres import begin_schema_setup, get_pool
from app.prompts.repository import PLATFORM_PROMPT_SCOPE


class PromptTestNotFoundError(LookupError):
    pass


class SQLitePromptTestRepository:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS prompt_test_cases (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, prompt_key TEXT NOT NULL,
                    name TEXT NOT NULL, input_text TEXT NOT NULL, rubric TEXT NOT NULL,
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_test_cases_scope
                    ON prompt_test_cases(workspace_id, prompt_key, created_at DESC);
                CREATE TABLE IF NOT EXISTS prompt_test_runs (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, prompt_key TEXT NOT NULL,
                    case_id TEXT NOT NULL, version_a INTEGER NOT NULL, version_b INTEGER NOT NULL,
                    result_json TEXT NOT NULL, preference TEXT, preference_note TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_test_runs_scope
                    ON prompt_test_runs(workspace_id, prompt_key, created_at DESC);
                """
            )
            if connection.execute(
                "SELECT 1 FROM prompt_test_cases WHERE workspace_id=? LIMIT 1",
                (PLATFORM_PROMPT_SCOPE,),
            ).fetchone() is None:
                connection.execute(
                    "UPDATE prompt_test_cases SET workspace_id=? WHERE workspace_id='default'",
                    (PLATFORM_PROMPT_SCOPE,),
                )
                connection.execute(
                    "UPDATE prompt_test_runs SET workspace_id=? WHERE workspace_id='default'",
                    (PLATFORM_PROMPT_SCOPE,),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    async def create_case(self, workspace_id: str, prompt_key: str, name: str, input_text: str, rubric: str, created_by: str) -> dict[str, Any]:
        item = {"id": str(uuid4()), "workspace_id": workspace_id, "prompt_key": prompt_key, "name": name.strip(), "input_text": input_text.strip(), "rubric": rubric.strip(), "created_by": created_by, "created_at": datetime.now(timezone.utc).isoformat()}
        with self._connect() as connection:
            connection.execute("INSERT INTO prompt_test_cases VALUES (:id,:workspace_id,:prompt_key,:name,:input_text,:rubric,:created_by,:created_at)", item)
        return {key: value for key, value in item.items() if key != "workspace_id"}

    async def list_cases(self, workspace_id: str, prompt_key: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id,prompt_key,name,input_text,rubric,created_by,created_at FROM prompt_test_cases WHERE workspace_id=? AND prompt_key=? ORDER BY created_at DESC", (workspace_id, prompt_key)).fetchall()
        return [dict(row) for row in rows]

    async def get_case(self, workspace_id: str, prompt_key: str, case_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM prompt_test_cases WHERE workspace_id=? AND prompt_key=? AND id=?", (workspace_id, prompt_key, case_id)).fetchone()
        if row is None:
            raise PromptTestNotFoundError(case_id)
        return dict(row)

    async def create_run(self, workspace_id: str, prompt_key: str, case_id: str, version_a: int, version_b: int, result: dict[str, Any], created_by: str) -> dict[str, Any]:
        item = {"id": str(uuid4()), "workspace_id": workspace_id, "prompt_key": prompt_key, "case_id": case_id, "version_a": version_a, "version_b": version_b, "result_json": json.dumps(result, ensure_ascii=False), "preference": None, "preference_note": "", "created_by": created_by, "created_at": datetime.now(timezone.utc).isoformat()}
        with self._connect() as connection:
            connection.execute("INSERT INTO prompt_test_runs VALUES (:id,:workspace_id,:prompt_key,:case_id,:version_a,:version_b,:result_json,:preference,:preference_note,:created_by,:created_at)", item)
        return self._decode_run(item)

    def _decode_run(self, row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        item.pop("workspace_id", None)
        return item

    async def list_runs(self, workspace_id: str, prompt_key: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM prompt_test_runs WHERE workspace_id=? AND prompt_key=? ORDER BY created_at DESC LIMIT 30", (workspace_id, prompt_key)).fetchall()
        return [self._decode_run(dict(row)) for row in rows]

    async def set_preference(self, workspace_id: str, run_id: str, preference: str, note: str) -> dict[str, Any]:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE prompt_test_runs SET preference=?, preference_note=? WHERE workspace_id=? AND id=?", (preference, note.strip(), workspace_id, run_id))
            if cursor.rowcount != 1:
                raise PromptTestNotFoundError(run_id)
            row = connection.execute("SELECT * FROM prompt_test_runs WHERE workspace_id=? AND id=?", (workspace_id, run_id)).fetchone()
        return self._decode_run(dict(row))


class PostgresPromptTestRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        with self._connect() as connection:
            if not begin_schema_setup(connection, "prompt_tests"):
                return
            connection.execute("""CREATE TABLE IF NOT EXISTS py_prompt_test_cases (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, prompt_key TEXT NOT NULL,
                name TEXT NOT NULL, input_text TEXT NOT NULL, rubric TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_prompt_test_cases_scope ON py_prompt_test_cases(workspace_id,prompt_key,created_at DESC)")
            connection.execute("""CREATE TABLE IF NOT EXISTS py_prompt_test_runs (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, prompt_key TEXT NOT NULL,
                case_id TEXT NOT NULL, version_a INTEGER NOT NULL, version_b INTEGER NOT NULL,
                result_json JSONB NOT NULL, preference TEXT, preference_note TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_prompt_test_runs_scope ON py_prompt_test_runs(workspace_id,prompt_key,created_at DESC)")
            if connection.execute(
                "SELECT 1 FROM py_prompt_test_cases WHERE workspace_id=%s LIMIT 1",
                (PLATFORM_PROMPT_SCOPE,),
            ).fetchone() is None:
                connection.execute(
                    "UPDATE py_prompt_test_cases SET workspace_id=%s WHERE workspace_id='default'",
                    (PLATFORM_PROMPT_SCOPE,),
                )
                connection.execute(
                    "UPDATE py_prompt_test_runs SET workspace_id=%s WHERE workspace_id='default'",
                    (PLATFORM_PROMPT_SCOPE,),
                )

    def _connect(self) -> psycopg.Connection:
        return get_pool(self.database_url).connection()

    async def create_case(self, workspace_id: str, prompt_key: str, name: str, input_text: str, rubric: str, created_by: str) -> dict[str, Any]:
        item = {"id": str(uuid4()), "workspace_id": workspace_id, "prompt_key": prompt_key, "name": name.strip(), "input_text": input_text.strip(), "rubric": rubric.strip(), "created_by": created_by, "created_at": datetime.now(timezone.utc)}
        with self._connect() as connection:
            connection.execute("INSERT INTO py_prompt_test_cases (id,workspace_id,prompt_key,name,input_text,rubric,created_by,created_at) VALUES (%(id)s,%(workspace_id)s,%(prompt_key)s,%(name)s,%(input_text)s,%(rubric)s,%(created_by)s,%(created_at)s)", item)
        return {key: value for key, value in item.items() if key != "workspace_id"}

    async def list_cases(self, workspace_id: str, prompt_key: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return list(connection.execute("SELECT id,prompt_key,name,input_text,rubric,created_by,created_at FROM py_prompt_test_cases WHERE workspace_id=%s AND prompt_key=%s ORDER BY created_at DESC", (workspace_id, prompt_key)).fetchall())

    async def get_case(self, workspace_id: str, prompt_key: str, case_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM py_prompt_test_cases WHERE workspace_id=%s AND prompt_key=%s AND id=%s", (workspace_id, prompt_key, case_id)).fetchone()
        if row is None:
            raise PromptTestNotFoundError(case_id)
        return dict(row)

    async def create_run(self, workspace_id: str, prompt_key: str, case_id: str, version_a: int, version_b: int, result: dict[str, Any], created_by: str) -> dict[str, Any]:
        item = {"id": str(uuid4()), "workspace_id": workspace_id, "prompt_key": prompt_key, "case_id": case_id, "version_a": version_a, "version_b": version_b, "result_json": json.dumps(result, ensure_ascii=False), "preference": None, "preference_note": "", "created_by": created_by, "created_at": datetime.now(timezone.utc)}
        with self._connect() as connection:
            row = connection.execute("INSERT INTO py_prompt_test_runs (id,workspace_id,prompt_key,case_id,version_a,version_b,result_json,preference,preference_note,created_by,created_at) VALUES (%(id)s,%(workspace_id)s,%(prompt_key)s,%(case_id)s,%(version_a)s,%(version_b)s,%(result_json)s::jsonb,%(preference)s,%(preference_note)s,%(created_by)s,%(created_at)s) RETURNING *", item).fetchone()
        return self._decode_run(dict(row))

    def _decode_run(self, row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        raw = item.pop("result_json")
        item["result"] = json.loads(raw) if isinstance(raw, str) else raw
        item.pop("workspace_id", None)
        return item

    async def list_runs(self, workspace_id: str, prompt_key: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM py_prompt_test_runs WHERE workspace_id=%s AND prompt_key=%s ORDER BY created_at DESC LIMIT 30", (workspace_id, prompt_key)).fetchall()
        return [self._decode_run(dict(row)) for row in rows]

    async def set_preference(self, workspace_id: str, run_id: str, preference: str, note: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("UPDATE py_prompt_test_runs SET preference=%s, preference_note=%s WHERE workspace_id=%s AND id=%s RETURNING *", (preference, note.strip(), workspace_id, run_id)).fetchone()
        if row is None:
            raise PromptTestNotFoundError(run_id)
        return self._decode_run(dict(row))


def build_prompt_test_repository(*, database_url: str, database_path: Path | str) -> SQLitePromptTestRepository | PostgresPromptTestRepository:
    return PostgresPromptTestRepository(database_url) if database_url else SQLitePromptTestRepository(database_path)
