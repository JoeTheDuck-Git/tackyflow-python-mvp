from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from app.db.postgres import get_pool

from app.auth.repository import (
    AuthenticationError,
    AuthorizationError,
    BootstrapUnavailableError,
    EmailNotVerifiedError,
    InvalidInviteCodeError,
    PasswordResetRateLimitError,
    ROLE_ORDER,
    SessionContext,
    SignupConflictError,
    SignupRateLimitError,
    SignupResult,
    generate_invite_code,
    hash_invite_code,
    normalize_email,
    signup_workspace_name,
)


def _supabase_error_code(response: httpx.Response) -> str:
    try:
        payload = response.json() or {}
    except ValueError:
        payload = {}
    return str(payload.get("error_code") or payload.get("code") or payload.get("error") or "")


class PostgresSupabaseAuthRepository:
    """Supabase credential verification with PostgreSQL workspace/session state."""

    def __init__(
        self,
        database_url: str,
        *,
        supabase_url: str,
        anon_key: str,
        service_role_key: str,
        session_ttl_hours: int = 12,
        session_idle_minutes: int = 120,
    ) -> None:
        if not all((database_url, supabase_url, anon_key, service_role_key)):
            raise ValueError("Supabase Auth and PostgreSQL settings are required")
        self.database_url = database_url
        self.supabase_url = supabase_url.rstrip("/")
        self.anon_key = anon_key
        self.service_role_key = service_role_key
        self.session_ttl = timedelta(hours=max(1, session_ttl_hours))
        self.session_idle = timedelta(minutes=max(5, session_idle_minutes))
        self._initialize()

    def _connect(self) -> psycopg.Connection:
        return get_pool(self.database_url).connection()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_auth_profiles (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_auth_workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_auth_memberships (
                    workspace_id TEXT NOT NULL REFERENCES py_auth_workspaces(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES py_auth_profiles(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('owner','admin','member')),
                    created_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (workspace_id, user_id)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_auth_sessions (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL REFERENCES py_auth_profiles(id) ON DELETE CASCADE,
                    active_workspace_id TEXT NOT NULL REFERENCES py_auth_workspaces(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS py_auth_invite_codes (
                    id TEXT PRIMARY KEY,
                    code_hash TEXT NOT NULL UNIQUE,
                    code_hint TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    max_uses INTEGER NOT NULL CHECK(max_uses >= 1),
                    used_count INTEGER NOT NULL DEFAULT 0,
                    expires_at TIMESTAMPTZ,
                    revoked_at TIMESTAMPTZ,
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_auth_memberships_user ON py_auth_memberships(user_id, workspace_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_py_auth_sessions_token ON py_auth_sessions(token_hash)")

    def _admin_headers(self) -> dict[str, str]:
        return {"apikey": self.service_role_key, "Authorization": f"Bearer {self.service_role_key}"}

    async def _supabase_login(self, email: str, password: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                f"{self.supabase_url}/auth/v1/token?grant_type=password",
                headers={"apikey": self.anon_key, "Authorization": f"Bearer {self.anon_key}"},
                json={"email": normalize_email(email), "password": password},
            )
        if response.status_code >= 400:
            # Supabase only reports this after the password matched, so it does not
            # reveal whether an address is registered.
            if _supabase_error_code(response) == "email_not_confirmed":
                raise EmailNotVerifiedError("email address has not been verified")
            raise AuthenticationError("invalid email or password")
        return response.json()

    async def _admin_create_user(self, email: str, password: str, display_name: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                f"{self.supabase_url}/auth/v1/admin/users",
                headers=self._admin_headers(),
                json={
                    "email": normalize_email(email),
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"display_name": display_name},
                },
            )
        if response.status_code >= 400:
            raise ValueError("Supabase Auth 無法建立帳號；電子郵件可能已存在")
        return response.json()

    async def bootstrap_available(self) -> bool:
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM py_auth_profiles").fetchone()["count"]
        return int(count) == 0

    async def bootstrap_owner(self, *, email: str, display_name: str, password: str) -> tuple[str, SessionContext]:
        if not await self.bootstrap_available():
            raise BootstrapUnavailableError("administrator has already been created")
        user = await self._admin_create_user(email, password, display_name)
        now = datetime.now(timezone.utc)
        user_id = str(user["id"])
        with self._connect() as connection:
            connection.execute("INSERT INTO py_auth_profiles (id,email,display_name,created_at,updated_at) VALUES (%s,%s,%s,%s,%s)", (user_id, normalize_email(email), display_name.strip(), now, now))
            connection.execute("INSERT INTO py_auth_workspaces (id,name,created_at) VALUES ('default','預設工作區',%s) ON CONFLICT (id) DO NOTHING", (now,))
            connection.execute("INSERT INTO py_auth_memberships (workspace_id,user_id,role,created_at) VALUES ('default',%s,'owner',%s)", (user_id, now))
        return await self._create_session(user_id, "default")

    async def create_invite_code(self, *, created_by: str, label: str = "", max_uses: int = 1, expires_at: datetime | None = None) -> dict[str, Any]:
        code, now = generate_invite_code(), datetime.now(timezone.utc)
        record = {"id": str(uuid4()), "code_hint": code[-4:], "label": " ".join(label.split()), "max_uses": max_uses, "used_count": 0, "expires_at": expires_at, "revoked_at": None, "created_at": now}
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO py_auth_invite_codes (id,code_hash,code_hint,label,max_uses,expires_at,created_by,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (record["id"], hash_invite_code(code), record["code_hint"], record["label"], max_uses, expires_at, created_by, now),
            )
        # The plaintext code is returned exactly once; only its hash is stored.
        return {**record, "code": code}

    async def list_invite_codes(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return list(connection.execute("SELECT id,code_hint,label,max_uses,used_count,expires_at,revoked_at,created_at FROM py_auth_invite_codes ORDER BY created_at DESC").fetchall())

    async def revoke_invite_code(self, code_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE py_auth_invite_codes SET revoked_at=%s WHERE id=%s AND revoked_at IS NULL", (datetime.now(timezone.utc), code_id))
        if cursor.rowcount != 1:
            raise InvalidInviteCodeError("invite code not found")

    def _redeem_invite_code(self, invite_code: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE py_auth_invite_codes SET used_count=used_count+1
                WHERE code_hash=%s AND revoked_at IS NULL AND used_count<max_uses
                  AND (expires_at IS NULL OR expires_at>%s)
                RETURNING id
                """,
                (hash_invite_code(invite_code), datetime.now(timezone.utc)),
            ).fetchone()
        if row is None:
            raise InvalidInviteCodeError("invite code is invalid or has expired")
        return row["id"]

    def _release_invite_code(self, code_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE py_auth_invite_codes SET used_count=used_count-1 WHERE id=%s AND used_count>0", (code_id,))

    async def _supabase_signup(self, email: str, password: str, display_name: str, redirect_to: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                f"{self.supabase_url}/auth/v1/signup",
                params={"redirect_to": redirect_to} if redirect_to else None,
                headers={"apikey": self.anon_key, "Authorization": f"Bearer {self.anon_key}"},
                json={"email": normalize_email(email), "password": password, "data": {"display_name": display_name}},
            )
        error_code = _supabase_error_code(response) if response.status_code >= 400 else ""
        if response.status_code == 429 or "rate_limit" in error_code:
            raise SignupRateLimitError("verification email rate limit reached")
        if error_code in {"user_already_exists", "email_exists"}:
            raise SignupConflictError("email is already registered")
        if response.status_code >= 400:
            raise ValueError("Supabase Auth 無法建立帳號，請確認電子郵件與密碼格式")
        payload = response.json() or {}
        user = payload.get("user") or payload
        # With email confirmation on, Supabase answers an existing address with an
        # obfuscated user that has no identities instead of an error.
        if not user.get("id") or user.get("identities") == []:
            raise SignupConflictError("email is already registered")
        return user

    async def _admin_delete_user(self, user_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                await client.delete(f"{self.supabase_url}/auth/v1/admin/users/{user_id}", headers=self._admin_headers())
        except httpx.HTTPError:
            pass

    async def signup(self, *, email: str, display_name: str, password: str, invite_code: str, redirect_to: str = "") -> SignupResult:
        normalized_email, normalized_name = normalize_email(email), " ".join(display_name.split())
        if not normalized_name:
            raise ValueError("display name is required")
        code_id = self._redeem_invite_code(invite_code)
        try:
            user = await self._supabase_signup(normalized_email, password, normalized_name, redirect_to)
        except BaseException:
            self._release_invite_code(code_id)
            raise
        user_id, workspace_id, now = str(user["id"]), str(uuid4()), datetime.now(timezone.utc)
        try:
            # One pooled connection block commits all three rows together or none.
            with self._connect() as connection:
                connection.execute("INSERT INTO py_auth_profiles (id,email,display_name,created_at,updated_at) VALUES (%s,%s,%s,%s,%s)", (user_id, normalized_email, normalized_name, now, now))
                connection.execute("INSERT INTO py_auth_workspaces (id,name,created_at) VALUES (%s,%s,%s)", (workspace_id, signup_workspace_name(normalized_name), now))
                connection.execute("INSERT INTO py_auth_memberships (workspace_id,user_id,role,created_at) VALUES (%s,%s,'owner',%s)", (workspace_id, user_id, now))
        except BaseException as exc:
            # Keep Supabase Auth and the workspace tables consistent.
            await self._admin_delete_user(user_id)
            self._release_invite_code(code_id)
            if isinstance(exc, UniqueViolation):
                raise SignupConflictError("email is already registered") from exc
            raise
        # Login stays blocked by Supabase until the verification link is clicked.
        return SignupResult(email=normalized_email, workspace_id=workspace_id, verification_required=True)

    async def resend_verification(self, email: str, *, redirect_to: str = "") -> None:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                f"{self.supabase_url}/auth/v1/resend",
                params={"redirect_to": redirect_to} if redirect_to else None,
                headers={"apikey": self.anon_key, "Authorization": f"Bearer {self.anon_key}"},
                json={"type": "signup", "email": normalize_email(email)},
            )
        if response.status_code == 429 or "rate_limit" in _supabase_error_code(response):
            raise SignupRateLimitError("verification email rate limit reached")

    async def login(self, email: str, password: str) -> tuple[str, SessionContext]:
        auth = await self._supabase_login(email, password)
        user = auth.get("user") or {}
        user_id = str(user.get("id") or "")
        with self._connect() as connection:
            profile = connection.execute("SELECT id FROM py_auth_profiles WHERE id=%s AND is_active", (user_id,)).fetchone()
            membership = connection.execute("SELECT workspace_id FROM py_auth_memberships WHERE user_id=%s ORDER BY created_at LIMIT 1", (user_id,)).fetchone()
        if profile is None or membership is None:
            raise AuthorizationError("user has no workspace membership")
        return await self._create_session(user_id, membership["workspace_id"])

    async def _create_session(self, user_id: str, workspace_id: str) -> tuple[str, SessionContext]:
        now = datetime.now(timezone.utc)
        raw_token = secrets.token_urlsafe(32)
        values = (str(uuid4()), hashlib.sha256(raw_token.encode()).hexdigest(), user_id, workspace_id, secrets.token_urlsafe(32), now, now, now + self.session_ttl)
        with self._connect() as connection:
            connection.execute("INSERT INTO py_auth_sessions (id,token_hash,user_id,active_workspace_id,csrf_token,created_at,last_seen_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", values)
        return raw_token, await self.resolve_session(raw_token)

    async def resolve_session(self, raw_token: str) -> SessionContext:
        now = datetime.now(timezone.utc)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self._connect() as connection:
            row = connection.execute("""
                SELECT s.id session_id,s.user_id,s.active_workspace_id,s.csrf_token,s.last_seen_at,s.expires_at,
                       p.email,p.display_name,p.is_active,w.name workspace_name,m.role
                FROM py_auth_sessions s JOIN py_auth_profiles p ON p.id=s.user_id
                JOIN py_auth_workspaces w ON w.id=s.active_workspace_id
                JOIN py_auth_memberships m ON m.workspace_id=s.active_workspace_id AND m.user_id=s.user_id
                WHERE s.token_hash=%s
            """, (token_hash,)).fetchone()
            if row is None or row["expires_at"] <= now or row["last_seen_at"] + self.session_idle <= now or not row["is_active"]:
                if row is not None:
                    connection.execute("DELETE FROM py_auth_sessions WHERE id=%s", (row["session_id"],))
                raise AuthenticationError("session has expired")
            connection.execute("UPDATE py_auth_sessions SET last_seen_at=%s WHERE id=%s", (now, row["session_id"]))
        return SessionContext(session_id=row["session_id"], user_id=row["user_id"], email=row["email"], display_name=row["display_name"], workspace_id=row["active_workspace_id"], workspace_name=row["workspace_name"], role=row["role"], csrf_token=row["csrf_token"], expires_at=row["expires_at"])

    async def logout(self, raw_token: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM py_auth_sessions WHERE token_hash=%s", (hashlib.sha256(raw_token.encode()).hexdigest(),))

    async def change_password(self, context: SessionContext, *, current_password: str, new_password: str) -> None:
        await self._supabase_login(context.email, current_password)
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.put(f"{self.supabase_url}/auth/v1/admin/users/{context.user_id}", headers=self._admin_headers(), json={"password": new_password})
        if response.status_code >= 400:
            raise AuthenticationError("password update failed")
        with self._connect() as connection:
            connection.execute("DELETE FROM py_auth_sessions WHERE user_id=%s AND id<>%s", (context.user_id, context.session_id))

    async def request_password_reset(self, email: str, *, redirect_to: str = "") -> None:
        payload: dict[str, str] = {"email": normalize_email(email)}
        if redirect_to:
            payload["redirect_to"] = redirect_to
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(
                f"{self.supabase_url}/auth/v1/recover",
                headers={"apikey": self.anon_key, "Authorization": f"Bearer {self.anon_key}"},
                json=payload,
            )
        if response.status_code >= 400:
            try:
                error_payload = response.json() or {}
            except ValueError:
                error_payload = {}
            error_code = str(error_payload.get("error_code") or error_payload.get("code") or "")
            if response.status_code == 429 or "rate_limit" in error_code:
                raise PasswordResetRateLimitError("password recovery email rate limit reached")
            raise AuthenticationError("password recovery request failed")

    async def confirm_password_reset(self, access_token: str, *, new_password: str) -> None:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.put(
                f"{self.supabase_url}/auth/v1/user",
                headers={"apikey": self.anon_key, "Authorization": f"Bearer {access_token}"},
                json={"password": new_password},
            )
        if response.status_code >= 400:
            raise AuthenticationError("password recovery token is invalid or expired")
        user_id = str((response.json() or {}).get("id") or "")
        # Revoke the recovery session's refresh token after the password changes.
        # Access JWTs are short-lived, but this prevents the session from minting
        # any new tokens after the one-time recovery operation completes.
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                await client.post(
                    f"{self.supabase_url}/auth/v1/logout?scope=global",
                    headers={"apikey": self.anon_key, "Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError:
            pass
        if user_id:
            with self._connect() as connection:
                connection.execute("DELETE FROM py_auth_sessions WHERE user_id=%s", (user_id,))

    async def list_workspaces(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT w.id,w.name,m.role FROM py_auth_memberships m JOIN py_auth_workspaces w ON w.id=m.workspace_id WHERE m.user_id=%s ORDER BY w.created_at", (user_id,)).fetchall()
        return list(rows)

    async def switch_workspace(self, context: SessionContext, workspace_id: str) -> SessionContext:
        with self._connect() as connection:
            row = connection.execute("SELECT w.name,m.role FROM py_auth_memberships m JOIN py_auth_workspaces w ON w.id=m.workspace_id WHERE m.user_id=%s AND m.workspace_id=%s", (context.user_id, workspace_id)).fetchone()
            if row is None:
                raise AuthorizationError("workspace membership is required")
            connection.execute("UPDATE py_auth_sessions SET active_workspace_id=%s WHERE id=%s", (workspace_id, context.session_id))
        return SessionContext(session_id=context.session_id,user_id=context.user_id,email=context.email,display_name=context.display_name,workspace_id=workspace_id,workspace_name=row["name"],role=row["role"],csrf_token=context.csrf_token,expires_at=context.expires_at)

    async def create_workspace(self, user_id: str, *, name: str) -> dict[str, Any]:
        normalized = " ".join(name.split())
        if not normalized:
            raise ValueError("workspace name is required")
        workspace_id, now = str(uuid4()), datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("INSERT INTO py_auth_workspaces (id,name,created_at) VALUES (%s,%s,%s)", (workspace_id, normalized, now))
            connection.execute("INSERT INTO py_auth_memberships (workspace_id,user_id,role,created_at) VALUES (%s,%s,'owner',%s)", (workspace_id, user_id, now))
        return {"id": workspace_id, "name": normalized, "role": "owner"}

    async def require_role(self, user_id: str, workspace_id: str, minimum_role: str) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT role FROM py_auth_memberships WHERE workspace_id=%s AND user_id=%s", (workspace_id,user_id)).fetchone()
        if row is None or ROLE_ORDER[row["role"]] < ROLE_ORDER[minimum_role]:
            raise AuthorizationError("insufficient workspace role")
        return row["role"]

    async def list_members(self, context: SessionContext, workspace_id: str) -> list[dict[str, Any]]:
        await self.require_role(context.user_id, workspace_id, "admin")
        with self._connect() as connection:
            return list(connection.execute("SELECT p.id,p.email,p.display_name,p.is_active,m.role,m.created_at FROM py_auth_memberships m JOIN py_auth_profiles p ON p.id=m.user_id WHERE m.workspace_id=%s ORDER BY m.created_at", (workspace_id,)).fetchall())

    async def add_member(self, context: SessionContext, workspace_id: str, *, email: str, display_name: str, password: str, role: str) -> dict[str, Any]:
        await self.require_role(context.user_id, workspace_id, "admin")
        if role not in {"admin", "member"}:
            raise ValueError("new members may be admin or member")
        user = await self._admin_create_user(email, password, display_name)
        user_id, now = str(user["id"]), datetime.now(timezone.utc)
        try:
            with self._connect() as connection:
                connection.execute("INSERT INTO py_auth_profiles (id,email,display_name,created_at,updated_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET display_name=EXCLUDED.display_name,updated_at=EXCLUDED.updated_at", (user_id,normalize_email(email),display_name.strip(),now,now))
                connection.execute("INSERT INTO py_auth_memberships (workspace_id,user_id,role,created_at) VALUES (%s,%s,%s,%s)", (workspace_id,user_id,role,now))
        except UniqueViolation as exc:
            raise ValueError("user is already a workspace member") from exc
        return {"id":user_id,"email":normalize_email(email),"display_name":display_name.strip(),"role":role,"is_active":True,"created_at":now}

    async def update_member_role(self, context: SessionContext, workspace_id: str, user_id: str, role: str) -> None:
        await self.require_role(context.user_id, workspace_id, "owner")
        if role not in {"admin","member"} or user_id == context.user_id:
            raise ValueError("membership role cannot be changed")
        with self._connect() as connection:
            cursor=connection.execute("UPDATE py_auth_memberships SET role=%s WHERE workspace_id=%s AND user_id=%s AND role<>'owner'",(role,workspace_id,user_id))
        if cursor.rowcount != 1: raise AuthorizationError("membership cannot be changed")

    async def remove_member(self, context: SessionContext, workspace_id: str, user_id: str) -> None:
        await self.require_role(context.user_id, workspace_id, "admin")
        if user_id == context.user_id: raise ValueError("cannot remove your own membership")
        with self._connect() as connection:
            row=connection.execute("SELECT role FROM py_auth_memberships WHERE workspace_id=%s AND user_id=%s",(workspace_id,user_id)).fetchone()
            if row is None or row["role"]=="owner": raise AuthorizationError("membership cannot be removed")
            connection.execute("DELETE FROM py_auth_memberships WHERE workspace_id=%s AND user_id=%s",(workspace_id,user_id))
            connection.execute("DELETE FROM py_auth_sessions WHERE user_id=%s AND active_workspace_id=%s",(user_id,workspace_id))

    async def ping(self) -> None:
        with self._connect() as connection: connection.execute("SELECT 1").fetchone()
