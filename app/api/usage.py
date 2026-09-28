from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.api.workspace import resolve_actor_id, resolve_workspace_id
from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext
from app.workflow.repository import WorkflowNotFoundError, WorkflowRepository


class ClientEventName(StrEnum):
    PAGE_VIEWED = "page.viewed"
    WORKFLOW_OPENED = "workflow.opened"
    ARTIFACT_COPIED = "artifact.copied"
    ARTIFACT_PREVIEWED = "artifact.previewed"
    ARTIFACT_DOWNLOADED = "artifact.downloaded"
    PUBLICATION_COPY = "publication.copy"
    PUBLICATION_PACKAGE_DOWNLOADED = "publication.package_downloaded"


class ClientEventRequest(BaseModel):
    event_name: ClientEventName
    workflow_id: str | None = Field(default=None, max_length=100)
    metadata: dict[str, str | int | bool | None] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def limit_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 10:
            raise ValueError("metadata may contain at most 10 fields")
        if any(len(str(key)) > 50 or len(str(item)) > 200 for key, item in value.items()):
            raise ValueError("metadata field is too long")
        return value


class FeedbackRequest(BaseModel):
    rating: str = Field(pattern="^(helpful|needs_improvement)$")
    note: str = Field(default="", max_length=1000)
    artifact_type: str = Field(default="script", pattern="^(script|production_package)$")


def build_usage_router(
    usage_repository: Any,
    workflow_repository: WorkflowRepository,
    *,
    daily_generation_limit: int,
    platform_owner_emails: tuple[str, ...] | set[str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["usage"])
    allowed_platform_owners = (
        None
        if platform_owner_emails is None
        else {email.strip().casefold() for email in platform_owner_emails if email.strip()}
    )

    def require_platform_owner(context: SessionContext) -> None:
        allowed = (
            context.role == "owner"
            if allowed_platform_owners is None
            else context.email.strip().casefold() in allowed_platform_owners
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="platform owner access required")

    @router.get("/usage")
    async def get_usage(
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> dict[str, Any]:
        return await usage_repository.usage_summary(
            workspace_id, daily_generation_limit
        )

    @router.get("/admin/beta-usage")
    async def get_platform_beta_usage(
        days: int = Query(default=30, ge=1, le=90),
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, Any]:
        require_platform_owner(context)
        return await usage_repository.platform_usage_summary(days)

    @router.post("/events", status_code=status.HTTP_202_ACCEPTED)
    async def record_client_event(
        payload: ClientEventRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> dict[str, str]:
        if payload.workflow_id:
            try:
                await workflow_repository.get(payload.workflow_id, workspace_id)
            except WorkflowNotFoundError as exc:
                raise HTTPException(status_code=404, detail="resource not found") from exc
        event_id = await usage_repository.record_event(
            workspace_id,
            payload.event_name.value,
            actor_id=actor_id,
            workflow_id=payload.workflow_id,
            metadata=payload.metadata,
        )
        return {"id": event_id, "status": "accepted"}

    @router.post("/workflows/{workflow_id}/feedback", status_code=status.HTTP_201_CREATED)
    async def add_feedback(
        workflow_id: str,
        payload: FeedbackRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> dict[str, Any]:
        try:
            workflow = await workflow_repository.get(workflow_id, workspace_id)
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        if payload.artifact_type not in workflow.artifacts:
            raise HTTPException(status_code=409, detail="artifact is not ready")
        return await usage_repository.add_feedback(
            workspace_id,
            workflow_id,
            artifact_type=payload.artifact_type,
            rating=payload.rating,
            note=payload.note.strip(),
            actor_id=actor_id,
        )

    return router
