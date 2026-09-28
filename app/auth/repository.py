from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SESSION_COOKIE_DEV = "tackyflow_session"
SESSION_COOKIE_SECURE = "__Host-tackyflow_session"
ROLE_ORDER = {"member": 1, "admin": 2, "owner": 3}


class AuthenticationError(RuntimeError):
    pass


class PasswordResetRateLimitError(AuthenticationError):
    pass


class AuthorizationError(RuntimeError):
    pass


class BootstrapUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionContext:
    session_id: str
    user_id: str
    email: str
    display_name: str
    workspace_id: str
    workspace_name: str
    role: str
    csrf_token: str
    expires_at: datetime


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("password must contain 12 to 128 characters")
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32
    )
    return "$".join(
        [
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt_text.encode("ascii")),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class SQLiteAuthRepository:
    def __init__(
        self,
        database_path: Path | str,
        *,
        session_ttl_hours: int = 12,
        session_idle_minutes: int = 120,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_ttl = timedelta(hours=max(1, session_ttl_hours))
        self.session_idle = timedelta(minutes=max(5, session_idle_minutes))
        self._dummy_hash = hash_password("invalid-login-password")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_memberships (
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner','admin','member')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, user_id),
                    FOREIGN KEY (workspace_id) REFERENCES auth_workspaces(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    active_workspace_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                    FOREIGN KEY (active_workspace_id) REFERENCES auth_workspaces(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS auth_login_attempts (
                    id TEXT PRIMARY KEY,
                    identifier_hash TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_memberships_user
                    ON auth_memberships(user_id, workspace_id);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_token
                    ON auth_sessions(token_hash);
                CREATE INDEX IF NOT EXISTS idx_auth_login_attempts
                    ON auth_login_attempts(identifier_hash, occurred_at DESC);
                """
            )
        self.database_path.chmod(0o600)

    async def bootstrap_available(self) -> bool:
        with self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM auth_users"
            ).fetchone()["count"]
        return int(count) == 0

    async def bootstrap_owner(
        self, *, email: str, display_name: str, password: str
    ) -> tuple[str, SessionContext]:
        now = datetime.now(timezone.utc)
        user_id = str(uuid4())
        password_digest = hash_password(password)
        normalized_email = normalize_email(email)
        normalized_name = " ".join(display_name.split())
        if not normalized_name:
            raise ValueError("display name is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM auth_users"
            ).fetchone()["count"]
            if int(count) != 0:
                connection.rollback()
                raise BootstrapUnavailableError("administrator has already been created")
            connection.execute(
                "INSERT OR IGNORE INTO auth_workspaces (id, name, created_at) VALUES ('default', '預設工作區', ?)",
                (now.isoformat(),),
            )
            connection.execute(
                """
                INSERT INTO auth_users
                    (id, email, display_name, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized_email,
                    normalized_name,
                    password_digest,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO auth_memberships (workspace_id, user_id, role, created_at)
                VALUES ('default', ?, 'owner', ?)
                """,
                (user_id, now.isoformat()),
            )
        return await self._create_session(user_id, "default")

    async def login(self, email: str, password: str) -> tuple[str, SessionContext]:
        normalized_email = normalize_email(email)
        identifier_hash = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        attempt_window = now - timedelta(minutes=15)
        with self._connect() as connection:
            failed_attempts = connection.execute(
                """
                SELECT COUNT(*) AS count FROM auth_login_attempts
                WHERE identifier_hash = ? AND occurred_at >= ?
                """,
                (identifier_hash, attempt_window.isoformat()),
            ).fetchone()["count"]
            row = connection.execute(
                "SELECT id, password_hash, is_active FROM auth_users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
        password_hash = row["password_hash"] if row is not None else self._dummy_hash
        password_valid = verify_password(password, password_hash)
        if (
            int(failed_attempts) >= 5
            or row is None
            or not password_valid
            or not bool(row["is_active"])
        ):
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO auth_login_attempts (id, identifier_hash, occurred_at) VALUES (?, ?, ?)",
                    (str(uuid4()), identifier_hash, now.isoformat()),
                )
            raise AuthenticationError("invalid email or password")
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM auth_login_attempts WHERE identifier_hash = ?",
                (identifier_hash,),
            )
        with self._connect() as connection:
            membership = connection.execute(
                """
                SELECT workspace_id FROM auth_memberships
                WHERE user_id = ? ORDER BY created_at ASC LIMIT 1
                """,
                (row["id"],),
            ).fetchone()
        if membership is None:
            raise AuthorizationError("user has no workspace membership")
        return await self._create_session(row["id"], membership["workspace_id"])

    async def _create_session(
        self, user_id: str, workspace_id: str
    ) -> tuple[str, SessionContext]:
        now = datetime.now(timezone.utc)
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        session_id = str(uuid4())
        csrf_token = secrets.token_urlsafe(32)
        expires_at = now + self.session_ttl
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions
                    (id, token_hash, user_id, active_workspace_id, csrf_token,
                     created_at, last_seen_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    token_hash,
                    user_id,
                    workspace_id,
                    csrf_token,
                    now.isoformat(),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        context = await self.resolve_session(raw_token)
        return raw_token, context

    async def resolve_session(self, raw_token: str) -> SessionContext:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.id AS session_id, s.user_id, s.active_workspace_id,
                       s.csrf_token, s.last_seen_at, s.expires_at,
                       u.email, u.display_name, u.is_active,
                       w.name AS workspace_name, m.role
                FROM auth_sessions s
                JOIN auth_users u ON u.id = s.user_id
                JOIN auth_workspaces w ON w.id = s.active_workspace_id
                JOIN auth_memberships m
                  ON m.workspace_id = s.active_workspace_id AND m.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                raise AuthenticationError("session is invalid")
            expires_at = datetime.fromisoformat(row["expires_at"])
            last_seen_at = datetime.fromisoformat(row["last_seen_at"])
            if (
                expires_at <= now
                or last_seen_at + self.session_idle <= now
                or not bool(row["is_active"])
            ):
                connection.execute(
                    "DELETE FROM auth_sessions WHERE id = ?", (row["session_id"],)
                )
                raise AuthenticationError("session has expired")
            connection.execute(
                "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?",
                (now.isoformat(), row["session_id"]),
            )
        return SessionContext(
            session_id=row["session_id"],
            user_id=row["user_id"],
            email=row["email"],
            display_name=row["display_name"],
            workspace_id=row["active_workspace_id"],
            workspace_name=row["workspace_name"],
            role=row["role"],
            csrf_token=row["csrf_token"],
            expires_at=expires_at,
        )

    async def logout(self, raw_token: str) -> None:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)
            )

    async def change_password(
        self,
        context: SessionContext,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM auth_users WHERE id = ? AND is_active = 1",
                (context.user_id,),
            ).fetchone()
        if row is None or not verify_password(current_password, row["password_hash"]):
            raise AuthenticationError("current password is incorrect")
        new_hash = hash_password(new_password)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE auth_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_hash, now, context.user_id),
            )
            connection.execute(
                "DELETE FROM auth_sessions WHERE user_id = ? AND id != ?",
                (context.user_id, context.session_id),
            )

    async def list_workspaces(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT w.id, w.name, m.role
                FROM auth_memberships m
                JOIN auth_workspaces w ON w.id = m.workspace_id
                WHERE m.user_id = ? ORDER BY w.created_at ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def switch_workspace(
        self, context: SessionContext, workspace_id: str
    ) -> SessionContext:
        with self._connect() as connection:
            membership = connection.execute(
                "SELECT role FROM auth_memberships WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, context.user_id),
            ).fetchone()
            if membership is None:
                raise AuthorizationError("workspace membership is required")
            connection.execute(
                "UPDATE auth_sessions SET active_workspace_id = ? WHERE id = ?",
                (workspace_id, context.session_id),
            )
        # Re-read by session id without exposing/requiring the raw token.
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT w.name, m.role FROM auth_workspaces w
                JOIN auth_memberships m ON m.workspace_id = w.id
                WHERE w.id = ? AND m.user_id = ?
                """,
                (workspace_id, context.user_id),
            ).fetchone()
        return SessionContext(
            session_id=context.session_id,
            user_id=context.user_id,
            email=context.email,
            display_name=context.display_name,
            workspace_id=workspace_id,
            workspace_name=row["name"],
            role=row["role"],
            csrf_token=context.csrf_token,
            expires_at=context.expires_at,
        )

    async def create_workspace(
        self, user_id: str, *, name: str
    ) -> dict[str, Any]:
        workspace_id = str(uuid4())
        normalized_name = " ".join(name.split())
        if not normalized_name:
            raise ValueError("workspace name is required")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO auth_workspaces (id, name, created_at) VALUES (?, ?, ?)",
                (workspace_id, normalized_name, now),
            )
            connection.execute(
                """
                INSERT INTO auth_memberships (workspace_id, user_id, role, created_at)
                VALUES (?, ?, 'owner', ?)
                """,
                (workspace_id, user_id, now),
            )
        return {"id": workspace_id, "name": normalized_name, "role": "owner"}

    async def list_members(
        self, context: SessionContext, workspace_id: str
    ) -> list[dict[str, Any]]:
        await self.require_role(context.user_id, workspace_id, "admin")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT u.id, u.email, u.display_name, u.is_active, m.role, m.created_at
                FROM auth_memberships m
                JOIN auth_users u ON u.id = m.user_id
                WHERE m.workspace_id = ? ORDER BY m.created_at ASC
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def add_member(
        self,
        context: SessionContext,
        workspace_id: str,
        *,
        email: str,
        display_name: str,
        password: str,
        role: str,
    ) -> dict[str, Any]:
        await self.require_role(context.user_id, workspace_id, "admin")
        if role not in {"admin", "member"}:
            raise ValueError("new members may be admin or member")
        normalized_email = normalize_email(email)
        normalized_name = " ".join(display_name.split())
        if not normalized_name:
            raise ValueError("display name is required")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute(
                "SELECT id, display_name FROM auth_users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if user is None:
                user_id = str(uuid4())
                password_digest = hash_password(password)
                connection.execute(
                    """
                    INSERT INTO auth_users
                        (id, email, display_name, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        normalized_email,
                        normalized_name,
                        password_digest,
                        now,
                        now,
                    ),
                )
            else:
                user_id = user["id"]
            try:
                connection.execute(
                    """
                    INSERT INTO auth_memberships (workspace_id, user_id, role, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (workspace_id, user_id, role, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("user is already a workspace member") from exc
        return {
            "id": user_id,
            "email": normalized_email,
            "display_name": normalized_name or user["display_name"],
            "role": role,
            "is_active": True,
            "created_at": now,
        }

    async def update_member_role(
        self,
        context: SessionContext,
        workspace_id: str,
        user_id: str,
        role: str,
    ) -> None:
        await self.require_role(context.user_id, workspace_id, "owner")
        if role not in {"admin", "member"}:
            raise ValueError("role must be admin or member")
        if user_id == context.user_id:
            raise ValueError("owner cannot change their own role")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE auth_memberships SET role = ? WHERE workspace_id = ? AND user_id = ? AND role != 'owner'",
                (role, workspace_id, user_id),
            )
        if cursor.rowcount != 1:
            raise AuthorizationError("membership cannot be changed")

    async def remove_member(
        self, context: SessionContext, workspace_id: str, user_id: str
    ) -> None:
        await self.require_role(context.user_id, workspace_id, "admin")
        if user_id == context.user_id:
            raise ValueError("cannot remove your own membership")
        with self._connect() as connection:
            target = connection.execute(
                "SELECT role FROM auth_memberships WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
            if target is None:
                raise AuthorizationError("membership not found")
            if target["role"] == "owner":
                raise AuthorizationError("owner membership cannot be removed")
            cursor = connection.execute(
                "DELETE FROM auth_memberships WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            )
            connection.execute(
                "DELETE FROM auth_sessions WHERE user_id = ? AND active_workspace_id = ?",
                (user_id, workspace_id),
            )
        if cursor.rowcount != 1:
            raise AuthorizationError("membership not found")

    async def require_role(
        self, user_id: str, workspace_id: str, minimum_role: str
    ) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM auth_memberships WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
        if row is None or ROLE_ORDER[row["role"]] < ROLE_ORDER[minimum_role]:
            raise AuthorizationError("insufficient workspace role")
        return row["role"]

    async def ping(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()
