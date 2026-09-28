from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext


class WorkspaceAIProfileRequest(BaseModel):
    brand_name: str = Field(default="", max_length=120)
    brand_positioning: str = Field(default="", max_length=1000)
    target_audience: str = Field(default="", max_length=1000)
    tone: str = Field(default="", max_length=1000)
    preferred_vocabulary: list[str] = Field(default_factory=list, max_length=30)
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=30)
    default_cta: str = Field(default="", max_length=1000)
    script_snippets: list[str] = Field(default_factory=list, max_length=50)
    visual_style: str = Field(default="", max_length=1000)
    content_principles: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("preferred_vocabulary", "forbidden_phrases", "content_principles", "script_snippets")
    @classmethod
    def validate_list_items(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if any(len(item) > 200 for item in normalized):
            raise ValueError("profile list item is too long")
        return normalized


def build_workspace_ai_router(repository: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspace-ai-profile", tags=["workspace-ai-profile"])

    @router.get("")
    async def get_profile(context: SessionContext = Depends(resolve_session_context)) -> dict[str, Any]:
        return await repository.get(context.workspace_id)

    @router.put("")
    async def update_profile(
        payload: WorkspaceAIProfileRequest,
        context: SessionContext = Depends(resolve_session_context),
    ) -> dict[str, Any]:
        if context.role not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="workspace owner or admin access required")
        return await repository.upsert(
            context.workspace_id,
            payload.model_dump(mode="json"),
            context.user_id,
        )

    return router
