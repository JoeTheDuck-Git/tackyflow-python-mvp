from __future__ import annotations

import hmac
from typing import Any

from fastapi import Header, HTTPException, Request

from app.auth.repository import (
    AuthenticationError,
    SESSION_COOKIE_DEV,
    SESSION_COOKIE_SECURE,
    SQLiteAuthRepository,
    SessionContext,
)
from app.config import settings


_repository: Any | None = None


def get_auth_repository() -> Any:
    global _repository
    if _repository is None:
        if settings.database_url:
            from app.auth.postgres_repository import PostgresSupabaseAuthRepository

            _repository = PostgresSupabaseAuthRepository(
                settings.database_url,
                supabase_url=settings.supabase_url,
                anon_key=settings.supabase_anon_key,
                service_role_key=settings.supabase_service_role_key,
                session_ttl_hours=settings.session_ttl_hours,
                session_idle_minutes=settings.session_idle_minutes,
            )
        else:
            _repository = SQLiteAuthRepository(
                settings.database_path,
                session_ttl_hours=settings.session_ttl_hours,
                session_idle_minutes=settings.session_idle_minutes,
            )
    return _repository


def session_cookie_name() -> str:
    return (
        SESSION_COOKIE_SECURE
        if settings.app_env == "production"
        else SESSION_COOKIE_DEV
    )


async def resolve_session_context(
    request: Request,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> SessionContext:
    cached = getattr(request.state, "session_context", None)
    if cached is not None:
        context = cached
    else:
        raw_token = request.cookies.get(session_cookie_name(), "")
        if not raw_token:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            context = await get_auth_repository().resolve_session(raw_token)
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="authentication required") from exc
        request.state.session_context = context
        request.state.session_token = raw_token
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not x_csrf_token or not hmac.compare_digest(
            x_csrf_token, context.csrf_token
        ):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
    return context
