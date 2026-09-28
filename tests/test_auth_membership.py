from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import app.api.auth as auth_api
import app.api.workspace as workspace_api
import app.auth.dependencies as auth_dependencies
from app.api.auth import build_auth_router
from app.api.workspace import resolve_actor_id, resolve_workspace_id
from app.auth.repository import (
    AuthenticationError,
    AuthorizationError,
    BootstrapUnavailableError,
    PasswordResetRateLimitError,
    SQLiteAuthRepository,
    hash_password,
    verify_password,
)


def test_scrypt_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)


@pytest.mark.asyncio
async def test_workspace_membership_roles_and_sessions(tmp_path: Path) -> None:
    repository = SQLiteAuthRepository(tmp_path / "auth.db")
    raw_token, owner = await repository.bootstrap_owner(
        email="OWNER@example.com",
        display_name="Owner",
        password="owner-password-123",
    )
    assert owner.email == "owner@example.com"
    assert owner.role == "owner"
    assert (await repository.resolve_session(raw_token)).user_id == owner.user_id

    with pytest.raises(BootstrapUnavailableError):
        await repository.bootstrap_owner(
            email="second@example.com",
            display_name="Second",
            password="second-password-123",
        )

    member = await repository.add_member(
        owner,
        "default",
        email="member@example.com",
        display_name="Member",
        password="member-password-123",
        role="member",
    )
    member_token, member_context = await repository.login(
        "member@example.com", "member-password-123"
    )
    with pytest.raises(AuthorizationError):
        await repository.list_members(member_context, "default")

    workspace = await repository.create_workspace(owner.user_id, name="第二工作區")
    switched = await repository.switch_workspace(owner, workspace["id"])
    assert switched.workspace_name == "第二工作區"
    assert switched.role == "owner"

    await repository.remove_member(owner, "default", member["id"])
    with pytest.raises(AuthenticationError):
        await repository.resolve_session(member_token)

    await repository.change_password(
        owner,
        current_password="owner-password-123",
        new_password="owner-password-456",
    )
    with pytest.raises(AuthenticationError):
        await repository.login("owner@example.com", "owner-password-123")
    _, updated_owner = await repository.login(
        "owner@example.com", "owner-password-456"
    )
    assert updated_owner.user_id == owner.user_id


def test_session_cookie_csrf_and_workspace_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SQLiteAuthRepository(tmp_path / "api-auth.db")
    session_settings = SimpleNamespace(
        auth_mode="session",
        app_env="development",
        session_ttl_hours=12,
        session_idle_minutes=120,
        auth_proxy_secret="",
    )
    monkeypatch.setattr(auth_api, "settings", session_settings)
    monkeypatch.setattr(auth_dependencies, "settings", session_settings)
    monkeypatch.setattr(workspace_api, "settings", session_settings)
    monkeypatch.setattr(auth_dependencies, "_repository", repository)

    app = FastAPI()
    app.include_router(build_auth_router(repository))

    @app.get("/protected")
    async def protected(
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> dict[str, str]:
        return {"workspace_id": workspace_id, "actor_id": actor_id}

    client = TestClient(app)
    bootstrap = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "display_name": "Owner",
            "password": "owner-password-123",
        },
    )
    assert bootstrap.status_code == 201
    assert "HttpOnly" in bootstrap.headers["set-cookie"]
    assert "SameSite=strict" in bootstrap.headers["set-cookie"]
    csrf_token = bootstrap.json()["csrf_token"]

    protected = client.get("/protected", headers={"X-Workspace-ID": "default"})
    assert protected.status_code == 200
    assert protected.json()["workspace_id"] == "default"

    without_csrf = client.post(
        "/api/v1/workspaces", json={"name": "不應建立"}
    )
    assert without_csrf.status_code == 403

    created = client.post(
        "/api/v1/workspaces",
        headers={"X-CSRF-Token": csrf_token},
        json={"name": "新工作區"},
    )
    assert created.status_code == 201
    workspace_id = created.json()["id"]

    mismatched = client.get(
        "/protected", headers={"X-Workspace-ID": workspace_id}
    )
    assert mismatched.status_code == 403

    switched = client.post(
        "/api/v1/auth/switch-workspace",
        headers={"X-CSRF-Token": csrf_token},
        json={"workspace_id": workspace_id},
    )
    assert switched.status_code == 200
    assert switched.json()["workspace"]["id"] == workspace_id

    logout = client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token}
    )
    assert logout.status_code == 204
    assert client.get("/protected").status_code == 401


def test_password_reset_request_is_generic_and_confirmation_uses_recovery_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResetRepository:
        requested: tuple[str, str] | None = None
        confirmed: tuple[str, str] | None = None

        async def bootstrap_available(self) -> bool:
            return False

        async def request_password_reset(self, email: str, *, redirect_to: str) -> None:
            self.requested = (email, redirect_to)

        async def confirm_password_reset(self, access_token: str, *, new_password: str) -> None:
            self.confirmed = (access_token, new_password)

    repository = ResetRepository()
    monkeypatch.setattr(
        auth_api,
        "settings",
        SimpleNamespace(
            auth_mode="session",
            app_env="development",
            session_ttl_hours=12,
            password_reset_redirect_url="http://127.0.0.1:8000/?auth=recovery",
        ),
    )
    app = FastAPI()
    app.include_router(build_auth_router(repository))
    client = TestClient(app)

    requested = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "OWNER@example.com"},
    )
    assert requested.status_code == 202
    assert requested.json() == {"message": "若帳號存在，系統已寄出密碼重設連結。"}
    assert repository.requested == (
        "owner@example.com",
        "http://127.0.0.1:8000/?auth=recovery",
    )

    confirmed = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "access_token": "recovery-access-token-1234567890",
            "new_password": "new-owner-password-456",
        },
    )
    assert confirmed.status_code == 204
    assert repository.confirmed == (
        "recovery-access-token-1234567890",
        "new-owner-password-456",
    )


def test_password_reset_rate_limit_has_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RateLimitedRepository:
        async def bootstrap_available(self) -> bool:
            return False

        async def request_password_reset(self, email: str, *, redirect_to: str) -> None:
            raise PasswordResetRateLimitError("rate limited")

    monkeypatch.setattr(
        auth_api,
        "settings",
        SimpleNamespace(
            auth_mode="session",
            app_env="development",
            session_ttl_hours=12,
            password_reset_redirect_url="http://127.0.0.1:8000/?auth=recovery",
        ),
    )
    app = FastAPI()
    app.include_router(build_auth_router(RateLimitedRepository()))
    response = TestClient(app).post(
        "/api/v1/auth/password-reset/request",
        json={"email": "owner@example.com"},
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "重設信剛剛已寄出，請先檢查收件匣或稍後再申請。"
