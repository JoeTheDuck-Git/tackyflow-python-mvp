from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
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
    EmailNotVerifiedError,
    InvalidInviteCodeError,
    PasswordResetRateLimitError,
    SQLiteAuthRepository,
    SessionContext,
    SignupConflictError,
    SignupRateLimitError,
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


class SignupRequest(BootstrapRequest):
    invite_code: str = Field(min_length=4, max_length=64)


class InviteCodeCreateRequest(BaseModel):
    label: str = Field(default="", max_length=100)
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_in_days: int | None = Field(default=14, ge=1, le=365)


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


def build_auth_router(
    repository: Any,
    usage_repository: Any | None = None,
    platform_owner_emails: tuple[str, ...] | set[str] = (),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["authentication"])
    # Platform owners are defined only by server configuration, never by signup.
    platform_owners = {normalize_email(email) for email in platform_owner_emails if email.strip()}

    def require_platform_owner(context: SessionContext) -> None:
        if normalize_email(context.email) not in platform_owners:
            raise HTTPException(status_code=403, detail="platform owner access required")

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
        bootstrap_available = await repository.bootstrap_available()
        return {
            "auth_mode": settings.auth_mode,
            "authentication_required": settings.auth_mode == "session",
            "bootstrap_available": bootstrap_available,
            "signup_available": settings.auth_mode == "session" and not bootstrap_available,
        }

    @router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
    async def signup(payload: SignupRequest, response: Response) -> dict[str, Any]:
        if settings.auth_mode != "session":
            raise HTTPException(status_code=409, detail="session authentication is disabled")
        if await repository.bootstrap_available():
            raise HTTPException(status_code=409, detail="請先建立第一位管理員。")
        if payload.email in platform_owners:
            # Owner addresses are reserved so a signup can never collide with them.
            raise HTTPException(status_code=409, detail="這個電子郵件無法用於註冊。")
        try:
            result = await repository.signup(
                email=payload.email,
                display_name=payload.display_name,
                password=payload.password,
                invite_code=payload.invite_code,
                redirect_to=getattr(settings, "signup_verify_redirect_url", ""),
            )
        except InvalidInviteCodeError as exc:
            raise HTTPException(status_code=400, detail="邀請碼無效、已用完或已過期。") from exc
        except SignupConflictError as exc:
            raise HTTPException(status_code=409, detail="這個電子郵件已經註冊過，請直接登入或使用忘記密碼。") from exc
        except SignupRateLimitError as exc:
            raise HTTPException(status_code=429, detail="驗證信寄送太頻繁，請稍後再試。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result.context is None:
            return {"verification_required": True, "email": result.email}
        _set_session_cookie(response, result.raw_token)
        await record_auth_event(result.context, "auth.signup")
        return {
            "verification_required": False,
            **_context_payload(result.context, await repository.list_workspaces(result.context.user_id)),
        }

    @router.post("/auth/signup/resend", status_code=status.HTTP_202_ACCEPTED)
    async def resend_verification(payload: PasswordResetRequest) -> dict[str, str]:
        # Identical response for every address so it cannot be used to probe accounts.
        resend_method = getattr(repository, "resend_verification", None)
        if resend_method is not None:
            try:
                await resend_method(
                    payload.email,
                    redirect_to=getattr(settings, "signup_verify_redirect_url", ""),
                )
            except SignupRateLimitError as exc:
                raise HTTPException(status_code=429, detail="驗證信剛剛已寄出，請先檢查收件匣或稍後再試。") from exc
        return {"message": "若帳號尚未驗證，系統已重新寄出驗證信。"}

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
        except EmailNotVerifiedError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "email_not_verified", "message": "請先到信箱點擊驗證連結，完成後再登入。"},
            ) from exc
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

    @router.get("/invite-codes")
    async def list_invite_codes(
        context: SessionContext = Depends(resolve_session_context),
    ) -> list[dict[str, Any]]:
        require_platform_owner(context)
        return await repository.list_invite_codes()

    @router.post("/invite-codes", status_code=status.HTTP_201_CREATED)
    async def create_invite_code(
        payload: InviteCodeCreateRequest,
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, Any]:
        require_platform_owner(context)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        )
        invite = await repository.create_invite_code(
            created_by=context.user_id,
            label=payload.label,
            max_uses=payload.max_uses,
            expires_at=expires_at,
        )
        await record_auth_event(context, "auth.invite_created", {"max_uses": str(payload.max_uses)})
        return invite

    @router.delete("/invite-codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_invite_code(
        code_id: str,
        context: SessionContext = Depends(resolve_session_context),
    ) -> Response:
        require_platform_owner(context)
        try:
            await repository.revoke_invite_code(code_id)
        except InvalidInviteCodeError as exc:
            raise HTTPException(status_code=404, detail="invite code not found") from exc
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
