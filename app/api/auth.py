from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import (
    resolve_session_context,
    session_cookie_name,
)
from app.auth.repository import (
    AuthenticationError,
    AuthorizationError,
    BootstrapUnavailableError,
    PasswordResetRateLimitError,
    SQLiteAuthRepository,
    SessionContext,
    normalize_email,
)
from app.config import settings


class CredentialsRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("invalid email address")
        return normalized


class BootstrapRequest(CredentialsRequest):
    display_name: str = Field(min_length=1, max_length=100)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("display name is required")
        return normalized


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WorkspaceSwitchRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=100)


class MemberCreateRequest(BootstrapRequest):
    role: str = Field(default="member", pattern="^(admin|member)$")


class MemberRoleRequest(BaseModel):
    role: str = Field(pattern="^(admin|member)$")


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=12, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("invalid email address")
        return normalized


class PasswordResetConfirmRequest(BaseModel):
    access_token: str = Field(min_length=20, max_length=4096)
    new_password: str = Field(min_length=12, max_length=128)


def _context_payload(
    context: SessionContext, workspaces: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "authenticated": True,
        "user": {
            "id": context.user_id,
            "email": context.email,
            "display_name": context.display_name,
        },
        "workspace": {
            "id": context.workspace_id,
            "name": context.workspace_name,
            "role": context.role,
        },
        "workspaces": workspaces,
        "csrf_token": context.csrf_token,
        "expires_at": context.expires_at,
    }


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=session_cookie_name(),
        value=raw_token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/",
    )


def build_auth_router(repository: Any, usage_repository: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["authentication"])

    async def record_auth_event(
        context: SessionContext,
        event_name: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if usage_repository is None:
            return
        try:
            await usage_repository.record_event(
                context.workspace_id,
                event_name,
                actor_id=context.user_id,
                metadata=metadata or {},
            )
        except Exception:
            # Product analytics must never prevent authentication or workspace access.
            return

    @router.get("/auth/status")
    async def auth_status() -> dict[str, Any]:
        return {
            "auth_mode": settings.auth_mode,
            "authentication_required": settings.auth_mode == "session",
            "bootstrap_available": await repository.bootstrap_available(),
        }

    @router.post("/auth/bootstrap", status_code=status.HTTP_201_CREATED)
    async def bootstrap(
        payload: BootstrapRequest, response: Response
    ) -> dict[str, Any]:
        if settings.auth_mode != "session":
            raise HTTPException(status_code=409, detail="session authentication is disabled")
        try:
            raw_token, context = await repository.bootstrap_owner(
                email=payload.email,
                display_name=payload.display_name,
                password=payload.password,
            )
        except BootstrapUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _set_session_cookie(response, raw_token)
        await record_auth_event(context, "auth.bootstrap")
        return _context_payload(
            context, await repository.list_workspaces(context.user_id)
        )

    @router.post("/auth/login")
    async def login(
        payload: CredentialsRequest, response: Response
    ) -> dict[str, Any]:
        if settings.auth_mode != "session":
            raise HTTPException(status_code=409, detail="session authentication is disabled")
        try:
            raw_token, context = await repository.login(
                payload.email, payload.password
            )
        except (AuthenticationError, AuthorizationError) as exc:
            raise HTTPException(
                status_code=401, detail="電子郵件或密碼不正確"
            ) from exc
        _set_session_cookie(response, raw_token)
        await record_auth_event(context, "auth.login")
        return _context_payload(
            context, await repository.list_workspaces(context.user_id)
        )

    @router.post("/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
    async def request_password_reset(payload: PasswordResetRequest) -> dict[str, str]:
        # The response is deliberately identical for existing and unknown emails.
        reset_method = getattr(repository, "request_password_reset", None)
        if reset_method is not None:
            try:
                await reset_method(
                    payload.email,
                    redirect_to=settings.password_reset_redirect_url,
                )
            except PasswordResetRateLimitError as exc:
                raise HTTPException(
                    status_code=429,
                    detail="重設信剛剛已寄出，請先檢查收件匣或稍後再申請。",
                ) from exc
            except AuthenticationError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="目前無法寄送重設連結，請稍後再試。",
                ) from exc
        return {"message": "若帳號存在，系統已寄出密碼重設連結。"}

    @router.post("/auth/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
    async def confirm_password_reset(payload: PasswordResetConfirmRequest) -> Response:
        reset_method = getattr(repository, "confirm_password_reset", None)
        if reset_method is None:
            raise HTTPException(status_code=409, detail="目前環境未啟用電子郵件密碼重設。")
        try:
            await reset_method(payload.access_token, new_password=payload.new_password)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=400,
                detail="重設連結無效或已過期，請重新申請。",
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/auth/me")
    async def me(
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, Any]:
        return _context_payload(
            context, await repository.list_workspaces(context.user_id)
        )

    @router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(
        request: Request,
        response: Response,
        context: SessionContext = Depends(resolve_session_context),
    ) -> Response:
        await record_auth_event(context, "auth.logout")
        raw_token = getattr(request.state, "session_token", "")
        if raw_token:
            await repository.logout(raw_token)
        response.delete_cookie(
            session_cookie_name(),
            path="/",
            secure=settings.app_env == "production",
            httponly=True,
            samesite="strict",
        )
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
    async def change_password(
        payload: PasswordChangeRequest,
        context: SessionContext = Depends(resolve_session_context),
    ) -> Response:
        try:
            await repository.change_password(
                context,
                current_password=payload.current_password,
                new_password=payload.new_password,
            )
        except AuthenticationError as exc:
            raise HTTPException(status_code=400, detail="目前密碼不正確") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/workspaces")
    async def list_workspaces(
        context: SessionContext = Depends(resolve_session_context),
    ) -> list[dict[str, Any]]:
        return await repository.list_workspaces(context.user_id)

    @router.post("/workspaces", status_code=status.HTTP_201_CREATED)
    async def create_workspace(
        payload: WorkspaceCreateRequest,
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, Any]:
        try:
            workspace = await repository.create_workspace(
                context.user_id, name=payload.name
            )
            await record_auth_event(
                context,
                "workspace.created",
                {"created_workspace_id": str(workspace["id"])},
            )
            return workspace
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/auth/switch-workspace")
    async def switch_workspace(
        payload: WorkspaceSwitchRequest,
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, Any]:
        try:
            updated = await repository.switch_workspace(
                context, payload.workspace_id
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        await record_auth_event(updated, "workspace.switched")
        return _context_payload(
            updated, await repository.list_workspaces(updated.user_id)
        )

    @router.get("/workspaces/{workspace_id}/members")
    async def list_members(
        workspace_id: str,
        context: SessionContext = Depends(resolve_session_context),
    ) -> list[dict[str, Any]]:
        if workspace_id != context.workspace_id:
            raise HTTPException(status_code=404, detail="resource not found")
        try:
            return await repository.list_members(context, workspace_id)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.post(
        "/workspaces/{workspace_id}/members",
        status_code=status.HTTP_201_CREATED,
    )
    async def add_member(
        workspace_id: str,
        payload: MemberCreateRequest,
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, Any]:
        if workspace_id != context.workspace_id:
            raise HTTPException(status_code=404, detail="resource not found")
        try:
            member = await repository.add_member(
                context,
                workspace_id,
                email=payload.email,
                display_name=payload.display_name,
                password=payload.password,
                role=payload.role,
            )
            await record_auth_event(
                context,
                "workspace.member_added",
                {"role": payload.role},
            )
            return member
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.patch("/workspaces/{workspace_id}/members/{user_id}")
    async def update_member_role(
        workspace_id: str,
        user_id: str,
        payload: MemberRoleRequest,
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, str]:
        if workspace_id != context.workspace_id:
            raise HTTPException(status_code=404, detail="resource not found")
        try:
            await repository.update_member_role(
                context, workspace_id, user_id, payload.role
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "updated"}

    @router.delete(
        "/workspaces/{workspace_id}/members/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def remove_member(
        workspace_id: str,
        user_id: str,
        context: SessionContext = Depends(resolve_session_context),
    ) -> Response:
        if workspace_id != context.workspace_id:
            raise HTTPException(status_code=404, detail="resource not found")
        try:
            await repository.remove_member(context, workspace_id, user_id)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
