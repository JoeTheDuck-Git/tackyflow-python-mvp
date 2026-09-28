from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from app.db.postgres import get_pool
from app.prompts.templates import DETAILED_PROMPT_TEMPLATES


PLATFORM_PROMPT_SCOPE = "__platform_core__"


PROMPT_CATALOG: tuple[dict[str, str], ...] = (
    {"key": "opportunity_strategy", "name": "內容機會策略", "description": "決定題目角度、受眾價值與機會排序。", "locked": "來源透明、不得捏造事實、固定結構化輸出。", "default": DETAILED_PROMPT_TEMPLATES["opportunity_strategy"]},
    {"key": "opportunity_reviewer", "name": "內容機會審核", "description": "檢查機會是否重複、空泛或偏離主題。", "locked": "不得放寬證據門檻或略過風險標示。", "default": DETAILED_PROMPT_TEMPLATES["opportunity_reviewer"]},
    {"key": "workflow_research", "name": "研究代理", "description": "整理來源、背景與可用證據。", "locked": "不得把推測寫成事實，需保留來源邊界。", "default": DETAILED_PROMPT_TEMPLATES["workflow_research"]},
    {"key": "workflow_verification", "name": "驗證代理", "description": "進行條件式事實核驗與風險辨識。", "locked": "高風險主張必須標示，不能用文案流暢度取代驗證。", "default": DETAILED_PROMPT_TEMPLATES["workflow_verification"]},
    {"key": "workflow_writer", "name": "撰寫代理", "description": "產出完整逐字稿與內容結構。", "locked": "必須遵守字數、語言、格式與參考資料使用界線。", "default": DETAILED_PROMPT_TEMPLATES["workflow_writer"]},
    {"key": "editorial_critic", "name": "品質主編", "description": "檢查語氣、結構、重複與可讀性。", "locked": "不得自行加入未經來源支持的新事實。", "default": DETAILED_PROMPT_TEMPLATES["editorial_critic"]},
    {"key": "production_planner", "name": "製作規劃代理", "description": "把逐字稿轉為分鏡、節奏與 B-roll 規劃。", "locked": "分鏡必須能追溯到逐字稿段落與時間區間。", "default": DETAILED_PROMPT_TEMPLATES["production_planner"]},
    {"key": "visual_director", "name": "視覺導演代理", "description": "設計視覺、B-roll 與生成素材提示詞。", "locked": "提示詞必須對應逐字稿，不能產生無關裝飾畫面。", "default": DETAILED_PROMPT_TEMPLATES["visual_director"]},
    {"key": "production_quality", "name": "製作品質審核", "description": "檢查節奏、覆蓋率與素材可執行性。", "locked": "不得略過缺段、時間衝突或無法拍攝的風險。", "default": DETAILED_PROMPT_TEMPLATES["production_quality"]},
    {"key": "publishing_preflight", "name": "發布前檢查", "description": "發布前確認內容、素材與人工核准狀態。", "locked": "未通過人工核准時不得標示為可發布。", "default": DETAILED_PROMPT_TEMPLATES["publishing_preflight"]},
)

_CATALOG_BY_KEY = {item["key"]: item for item in PROMPT_CATALOG}


class PromptNotFoundError(LookupError):
    pass


class SQLitePromptRepository:
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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS prompt_versions (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    prompt_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    instructions TEXT NOT NULL,
                    change_note TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    UNIQUE(workspace_id, prompt_key, version)
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_versions_scope
                    ON prompt_versions(workspace_id, prompt_key, version DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_versions_active
                    ON prompt_versions(workspace_id, prompt_key)
                    WHERE activated_at IS NOT NULL;
                """
            )
            if connection.execute(
                "SELECT 1 FROM prompt_versions WHERE workspace_id=? LIMIT 1",
                (PLATFORM_PROMPT_SCOPE,),
            ).fetchone() is None:
                connection.execute(
                    "UPDATE prompt_versions SET workspace_id=? WHERE workspace_id='default'",
                    (PLATFORM_PROMPT_SCOPE,),
                )
        self.database_path.chmod(0o600)

    def _validate_key(self, prompt_key: str) -> dict[str, str]:
        item = _CATALOG_BY_KEY.get(prompt_key)
        if item is None:
            raise PromptNotFoundError(prompt_key)
        return item

    async def list_prompts(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM prompt_versions WHERE workspace_id = ?
                   ORDER BY prompt_key, version DESC""", (workspace_id,)
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["prompt_key"], []).append(dict(row))
        result = []
        for item in PROMPT_CATALOG:
            versions = grouped.get(item["key"], [])
            active = next((version for version in versions if version["activated_at"]), None)
            result.append({**item, "active_version": active, "latest_version": versions[0] if versions else None, "version_count": len(versions)})
        return result

    async def list_versions(self, workspace_id: str, prompt_key: str) -> list[dict[str, Any]]:
        self._validate_key(prompt_key)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, prompt_key, version, instructions, change_note, created_by,
                          created_at, activated_at
                   FROM prompt_versions WHERE workspace_id = ? AND prompt_key = ?
                   ORDER BY version DESC""", (workspace_id, prompt_key)
            ).fetchall()
        return [dict(row) for row in rows]

    async def create_version(self, workspace_id: str, prompt_key: str, instructions: str, change_note: str, created_by: str) -> dict[str, Any]:
        self._validate_key(prompt_key)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM prompt_versions WHERE workspace_id = ? AND prompt_key = ?",
                (workspace_id, prompt_key),
            ).fetchone()
            version = int(row["version"]) + 1
            item_id = str(uuid4())
            connection.execute(
                """INSERT INTO prompt_versions
                   (id, workspace_id, prompt_key, version, instructions, change_note, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (item_id, workspace_id, prompt_key, version, instructions.strip(), change_note.strip(), created_by, now),
            )
        return (await self.list_versions(workspace_id, prompt_key))[0]

    async def activate(self, workspace_id: str, prompt_key: str, version: int) -> dict[str, Any]:
        self._validate_key(prompt_key)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute(
                "SELECT id FROM prompt_versions WHERE workspace_id = ? AND prompt_key = ? AND version = ?",
                (workspace_id, prompt_key, version),
            ).fetchone()
            if target is None:
                raise PromptNotFoundError(prompt_key)
            connection.execute(
                "UPDATE prompt_versions SET activated_at = NULL WHERE workspace_id = ? AND prompt_key = ?",
                (workspace_id, prompt_key),
            )
            connection.execute("UPDATE prompt_versions SET activated_at = ? WHERE id = ?", (now, target["id"]))
        return next(item for item in await self.list_versions(workspace_id, prompt_key) if item["version"] == version)

    async def active_instruction(self, workspace_id: str, prompt_key: str) -> str:
        return self.active_instruction_sync(workspace_id, prompt_key)

    def active_instruction_sync(self, workspace_id: str, prompt_key: str) -> str:
        catalog = self._validate_key(prompt_key)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT instructions FROM prompt_versions
                   WHERE workspace_id = ? AND prompt_key = ? AND activated_at IS NOT NULL""",
                (workspace_id, prompt_key),
            ).fetchone()
        return row["instructions"] if row else catalog["default"]

    async def ping(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()


class PostgresPromptRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url=database_url
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS py_prompt_versions (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, prompt_key TEXT NOT NULL,
                version INTEGER NOT NULL, instructions TEXT NOT NULL, change_note TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, activated_at TIMESTAMPTZ,
                UNIQUE(workspace_id,prompt_key,version))""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_prompt_scope ON py_prompt_versions(workspace_id,prompt_key,version DESC)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_py_prompt_active ON py_prompt_versions(workspace_id,prompt_key) WHERE activated_at IS NOT NULL")
            if connection.execute(
                "SELECT 1 FROM py_prompt_versions WHERE workspace_id=%s LIMIT 1",
                (PLATFORM_PROMPT_SCOPE,),
            ).fetchone() is None:
                connection.execute(
                    "UPDATE py_prompt_versions SET workspace_id=%s WHERE workspace_id='default'",
                    (PLATFORM_PROMPT_SCOPE,),
                )

    def _connect(self) -> psycopg.Connection:
        return get_pool(self.database_url).connection()

    def _validate_key(self,prompt_key:str)->dict[str,str]:
        item=_CATALOG_BY_KEY.get(prompt_key)
        if item is None: raise PromptNotFoundError(prompt_key)
        return item

    async def list_prompts(self,workspace_id:str)->list[dict[str,Any]]:
        with self._connect() as connection: rows=connection.execute("SELECT * FROM py_prompt_versions WHERE workspace_id=%s ORDER BY prompt_key,version DESC",(workspace_id,)).fetchall()
        grouped={}
        for row in rows: grouped.setdefault(row["prompt_key"],[]).append(dict(row))
        result=[]
        for item in PROMPT_CATALOG:
            versions=grouped.get(item["key"],[]); active=next((v for v in versions if v["activated_at"]),None)
            result.append({**item,"active_version":active,"latest_version":versions[0] if versions else None,"version_count":len(versions)})
        return result

    async def list_versions(self,workspace_id:str,prompt_key:str)->list[dict[str,Any]]:
        self._validate_key(prompt_key)
        with self._connect() as connection: rows=connection.execute("SELECT id,prompt_key,version,instructions,change_note,created_by,created_at,activated_at FROM py_prompt_versions WHERE workspace_id=%s AND prompt_key=%s ORDER BY version DESC",(workspace_id,prompt_key)).fetchall()
        return list(rows)

    async def create_version(self,workspace_id:str,prompt_key:str,instructions:str,change_note:str,created_by:str)->dict[str,Any]:
        self._validate_key(prompt_key); now=datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(f"prompt:{workspace_id}:{prompt_key}",))
            version=int(connection.execute("SELECT COALESCE(MAX(version),0) version FROM py_prompt_versions WHERE workspace_id=%s AND prompt_key=%s",(workspace_id,prompt_key)).fetchone()["version"])+1
            connection.execute("INSERT INTO py_prompt_versions (id,workspace_id,prompt_key,version,instructions,change_note,created_by,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",(str(uuid4()),workspace_id,prompt_key,version,instructions.strip(),change_note.strip(),created_by,now))
        return (await self.list_versions(workspace_id,prompt_key))[0]

    async def activate(self,workspace_id:str,prompt_key:str,version:int)->dict[str,Any]:
        self._validate_key(prompt_key); now=datetime.now(timezone.utc)
        with self._connect() as connection:
            target=connection.execute("SELECT id FROM py_prompt_versions WHERE workspace_id=%s AND prompt_key=%s AND version=%s",(workspace_id,prompt_key,version)).fetchone()
            if target is None: raise PromptNotFoundError(prompt_key)
            connection.execute("UPDATE py_prompt_versions SET activated_at=NULL WHERE workspace_id=%s AND prompt_key=%s",(workspace_id,prompt_key))
            connection.execute("UPDATE py_prompt_versions SET activated_at=%s WHERE id=%s",(now,target["id"]))
        return next(v for v in await self.list_versions(workspace_id,prompt_key) if v["version"]==version)

    async def active_instruction(self,workspace_id:str,prompt_key:str)->str:
        return self.active_instruction_sync(workspace_id,prompt_key)

    def active_instruction_sync(self,workspace_id:str,prompt_key:str)->str:
        catalog=self._validate_key(prompt_key)
        with self._connect() as connection: row=connection.execute("SELECT instructions FROM py_prompt_versions WHERE workspace_id=%s AND prompt_key=%s AND activated_at IS NOT NULL",(workspace_id,prompt_key)).fetchone()
        return row["instructions"] if row else catalog["default"]

    async def ping(self)->None:
        with self._connect() as connection: connection.execute("SELECT 1").fetchone()


def build_prompt_repository(*,database_url:str,database_path:Path|str):
    return PostgresPromptRepository(database_url) if database_url else SQLitePromptRepository(database_path)
