from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, Request
from app.auth.dependencies import resolve_session_context
from app.config import settings


WORKSPACE_HEADER = "X-Workspace-ID"


async def resolve_workspace_id(
    x_workspace_id: Annotated[str | None, Header(alias=WORKSPACE_HEADER)] = None,
    x_authenticated_workspace: Annotated[
        str | None, Header(alias="X-Authenticated-Workspace")
    ] = None,
    x_auth_proxy_secret: Annotated[
        str | None, Header(alias="X-Auth-Proxy-Secret")
    ] = None,
    x_csrf_token: Annotated[
        str | None, Header(alias="X-CSRF-Token")
    ] = None,
    request: Request = None,
) -> str:
    """Resolve the local workspace boundary used by every persisted API.

    The standalone build has no identity provider, so it uses an explicit header and
    falls back to the single local workspace.  A host platform can override this
    dependency with an authenticated tenant/workspace resolver without changing the
    repositories or route contracts.
    """

    if settings.auth_mode == "session":
        context = await resolve_session_context(request, x_csrf_token)
        requested_workspace = " ".join((x_workspace_id or context.workspace_id).split())
        if requested_workspace != context.workspace_id:
            raise HTTPException(status_code=403, detail="active workspace does not match")
        return context.workspace_id
    if settings.auth_mode == "local" and getattr(settings, "app_env", "development") == "production":
        raise HTTPException(
            status_code=503,
            detail="local authentication mode is disabled in production",
        )
    if settings.auth_mode == "trusted_proxy":
        if not settings.auth_proxy_secret:
            raise HTTPException(status_code=503, detail="authentication is not configured")
        if not x_auth_proxy_secret or not hmac.compare_digest(
            x_auth_proxy_secret, settings.auth_proxy_secret
        ):
            raise HTTPException(status_code=401, detail="invalid authenticated proxy context")
        workspace_id = " ".join((x_authenticated_workspace or "").split())
    elif settings.auth_mode == "local":
        workspace_id = " ".join((x_workspace_id or "default").split())
    else:
        raise HTTPException(status_code=503, detail="unsupported authentication mode")
    if not workspace_id or len(workspace_id) > 100:
        raise HTTPException(status_code=422, detail="invalid workspace id")
    return workspace_id


async def resolve_actor_id(
    x_authenticated_user: Annotated[
        str | None, Header(alias="X-Authenticated-User")
    ] = None,
    x_csrf_token: Annotated[
        str | None, Header(alias="X-CSRF-Token")
    ] = None,
    request: Request = None,
) -> str:
    if settings.auth_mode == "session":
        return (await resolve_session_context(request, x_csrf_token)).user_id
    if settings.auth_mode == "trusted_proxy":
        actor_id = " ".join((x_authenticated_user or "").split())
        if not actor_id or len(actor_id) > 200:
            raise HTTPException(status_code=401, detail="authenticated user is required")
        return actor_id
    return "local-user"


def ensure_workspace(resource_workspace_id: str, requested_workspace_id: str) -> None:
    """Return 404 for cross-workspace access so resource existence is not leaked."""

    if resource_workspace_id != requested_workspace_id:
        raise HTTPException(status_code=404, detail="resource not found")
