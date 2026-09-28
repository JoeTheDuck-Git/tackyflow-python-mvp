from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.workspace import resolve_workspace_id
from app.knowledge.service import KnowledgeService


def build_knowledge_router(service: KnowledgeService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

    @router.get("/source-captures")
    async def list_source_captures(
        generation_id: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=100, ge=1, le=500),
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> dict[str, object]:
        records = await service.list_captures(
            workspace_id,
            generation_id=generation_id,
            limit=limit,
        )
        return {
            "items": [item.model_dump(mode="json") for item in records],
            "count": len(records),
        }

    return router
