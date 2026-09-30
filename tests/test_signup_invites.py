from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import app.api.auth as auth_api
import app.api.workspace as workspace_api
import app.auth.dependencies as auth_dependencies
import app.auth.postgres_repository as postgres_auth
from app.api.auth import build_auth_router
from app.api.workspace import resolve_workspace_id
from app.auth.postgres_repository import PostgresSupabaseAuthRepository
from app.auth.repository import (
    EmailNotVerifiedError,
    InvalidInviteCodeError,
    SQLiteAuthRepository,
    SignupConflictError,
    SignupRateLimitError,
    normalize_invite_code,
)


OWNER = {"email": "owner@example.com", "display_name": "Owner", "password": "owner-password-123"}


def _session_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, SQLiteAuthRepository]:
    repository = SQLiteAuthRepository(tmp_path / "signup.db")
    session_settings = SimpleNamespace(
        auth_mode="session",
        app_env="development",
        session_ttl_hours=12,
        session_idle_minutes=120,
        auth_proxy_secret="",
        signup_verify_redirect_url="",
    )
    monkeypatch.setattr(auth_api, "settings", session_settings)
    monkeypatch.setattr(auth_dependencies, "settings", session_settings)
    monkeypatch.setattr(workspace_api, "settings", session_settings)
    monkeypatch.setattr(auth_dependencies, "_repository", repository)
    app = FastAPI()
    app.include_router(build_auth_router(repository, platform_owner_emails=("OWNER@example.com",)))

    @app.get("/protected")
    async def protected(workspace_id: str = Depends(resolve_workspace_id)) -> dict[str, str]:
        return {"workspace_id": workspace_id}

    return app, repository


def _signup(client: TestClient, code: str, email: str = "new@example.com") -> httpx.Response:
    return client.post(
        "/api/v1/auth/signup",
        json={"invite_code": code, "display_name": "New User", "email": email, "password": "new-user-password-1"},
    )


def test_invite_signup_creates_isolated_workspace_without_platform_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _session_app(tmp_path, monkeypatch)
    owner = TestClient(app)
    assert owner.get("/api/v1/auth/status").json()["signup_available"] is False
    owner_csrf = owner.post("/api/v1/auth/bootstrap", json=OWNER).json()["csrf_token"]
    assert owner.get("/api/v1/auth/status").json()["signup_available"] is True
    assert owner.get("/api/v1/auth/me").json()["is_platform_owner"] is True

    created = owner.post(
        "/api/v1/invite-codes",
        headers={"X-CSRF-Token": owner_csrf},
        json={"label": "Beta A", "max_uses": 1, "expires_in_days": 7},
    )
    assert created.status_code == 201
    code = created.json()["code"]
    listed = owner.get("/api/v1/invite-codes").json()
    assert listed[0]["code_hint"] == code[-4:]
    assert "code" not in listed[0] and "code_hash" not in listed[0]

    newcomer = TestClient(app)
    assert _signup(newcomer, "WRONG-CODE-0000-0000").status_code == 400
    # Codes are accepted regardless of case and separators.
    signed_up = _signup(newcomer, code.lower().replace("-", " "))
    assert signed_up.status_code == 201
    body = signed_up.json()
    assert body["verification_required"] is False
    assert body["is_platform_owner"] is False
    assert body["workspace"]["id"] != "default"
    assert body["workspace"]["role"] == "owner"
    assert [workspace["id"] for workspace in body["workspaces"]] == [body["workspace"]["id"]]

    # The newcomer cannot reach the platform owner's workspace or owner-only tools.
    assert newcomer.get("/protected", headers={"X-Workspace-ID": "default"}).status_code == 403
    assert newcomer.get("/api/v1/invite-codes").status_code == 403
    assert newcomer.post(
        "/api/v1/invite-codes", headers={"X-CSRF-Token": body["csrf_token"]}, json={}
    ).status_code == 403
    assert newcomer.post(
        "/api/v1/auth/switch-workspace",
        headers={"X-CSRF-Token": body["csrf_token"]},
        json={"workspace_id": "default"},
    ).status_code == 403

    # A single-use code is exhausted, and owner addresses are reserved.
    assert _signup(TestClient(app), code, email="third@example.com").status_code == 400
    second_code = owner.post(
        "/api/v1/invite-codes", headers={"X-CSRF-Token": owner_csrf}, json={"max_uses": 5}
    ).json()["code"]
    assert _signup(TestClient(app), second_code, email="Owner@Example.com").status_code == 409
    assert _signup(TestClient(app), second_code, email="new@example.com").status_code == 409


def test_signup_requires_bootstrap_and_session_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _session_app(tmp_path, monkeypatch)
    assert _signup(TestClient(app), "AAAA-BBBB-CCCC-DDDD").status_code == 409


@pytest.mark.asyncio
async def test_revoked_and_expired_invite_codes_are_rejected(tmp_path: Path) -> None:
    repository = SQLiteAuthRepository(tmp_path / "invites.db")
    _, owner = await repository.bootstrap_owner(**OWNER)
    revoked = await repository.create_invite_code(created_by=owner.user_id, max_uses=3)
    await repository.revoke_invite_code(revoked["id"])
    expired = await repository.create_invite_code(
        created_by=owner.user_id, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    for code in (revoked["code"], expired["code"]):
        with pytest.raises(InvalidInviteCodeError):
            await repository.signup(
                email="late@example.com", display_name="Late", password="late-password-123", invite_code=code
            )
    with pytest.raises(InvalidInviteCodeError):
        await repository.revoke_invite_code(revoked["id"])
    assert normalize_invite_code("ab12-cd34") == "AB12CD34"


def _supabase_repository(monkeypatch: pytest.MonkeyPatch, handler) -> PostgresSupabaseAuthRepository:
    repository = object.__new__(PostgresSupabaseAuthRepository)
    repository.supabase_url = "https://example.supabase.co"
    repository.anon_key = "anon"
    repository.service_role_key = "service"
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        postgres_auth.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **{k: v for k, v in kwargs.items() if k != "trust_env"}),
    )
    return repository


@pytest.mark.asyncio
async def test_supabase_signup_detects_existing_email_and_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([
        httpx.Response(200, json={"id": "obfuscated", "identities": []}),
        httpx.Response(429, json={"error_code": "over_email_send_rate_limit"}),
    ])
    repository = _supabase_repository(monkeypatch, lambda request: next(responses))
    with pytest.raises(SignupConflictError):
        await repository._supabase_signup("a@example.com", "password-1234567", "A", "")
    with pytest.raises(SignupRateLimitError):
        await repository._supabase_signup("a@example.com", "password-1234567", "A", "")


@pytest.mark.asyncio
async def test_supabase_signup_releases_invite_when_auth_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    released: list[str] = []
    repository = _supabase_repository(monkeypatch, lambda request: httpx.Response(429, json={}))
    monkeypatch.setattr(repository, "_redeem_invite_code", lambda code: "invite-1", raising=False)
    monkeypatch.setattr(repository, "_release_invite_code", released.append, raising=False)
    with pytest.raises(SignupRateLimitError):
        await repository.signup(
            email="a@example.com", display_name="A", password="password-1234567", invite_code="X"
        )
    assert released == ["invite-1"]


@pytest.mark.asyncio
async def test_supabase_login_reports_unverified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _supabase_repository(
        monkeypatch, lambda request: httpx.Response(400, json={"error_code": "email_not_confirmed"})
    )
    with pytest.raises(EmailNotVerifiedError):
        await repository._supabase_login("a@example.com", "password-1234567")


def test_email_normalization_strips_invisible_and_full_width_characters() -> None:
    from app.auth.repository import normalize_email

    assert normalize_email(" Owner@\u200bExample.com\u00a0") == "owner@example.com"
    assert normalize_email("owner\ufeff@gmail\u2060.com") == "owner@gmail.com"
    assert normalize_email("ｏｗｎｅｒ＠ｅｘａｍｐｌｅ．ｃｏｍ") == "owner@example.com"


def test_login_accepts_email_pasted_with_zero_width_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _session_app(tmp_path, monkeypatch)
    client = TestClient(app)
    assert client.post("/api/v1/auth/bootstrap", json=OWNER).status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example\u200b.com", "password": OWNER["password"]},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "owner@example.com"
