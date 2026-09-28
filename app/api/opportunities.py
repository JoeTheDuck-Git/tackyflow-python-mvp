from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.agents.opportunity import (
    LocalManualSignalProvider,
    LocalOpportunityAgent,
    OpportunityAgent,
    SignalProvider,
)
from app.domain.models import (
    OpportunityGenerationEvent,
    OpportunityGenerationStatus,
    OpportunityItemStatus,
    OpportunityItemUpdate,
    OpportunityRegenerateRequest,
    OpportunityRequest,
    OpportunityResponse,
)
from app.opportunities.repository import (
    InMemoryOpportunityRepository,
    OpportunityNotFoundError,
    OpportunityRepository,
)
from app.workflow.repository import WorkflowNotFoundError, WorkflowRepository
from app.api.workspace import ensure_workspace, resolve_workspace_id
from app.usage.repository import GenerationQuotaExceededError


logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _request_hash(
    payload: OpportunityRequest,
    history_topics: list[str] | None = None,
    dismissed_topics: list[str] | None = None,
    cache_identity: dict[str, str] | None = None,
) -> str:
    canonical = json.dumps(
        {
            "request": payload.model_dump(mode="json"),
            "history_topics": sorted(history_topics or []),
            "dismissed_topics": sorted(dismissed_topics or []),
            # Generation and item regeneration have different identity semantics.
            # In particular, two items in the same immutable snapshot may have
            # identical override payloads, but replacing them must never share a
            # cached child generation.
            "cache_identity": cache_identity or {"operation": "generation"},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


def build_opportunity_router(
    repository: WorkflowRepository,
    agent: OpportunityAgent | None = None,
    opportunity_repository: OpportunityRepository | None = None,
    signal_provider: SignalProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/opportunities", tags=["opportunities"])
    opportunity_agent = agent or LocalOpportunityAgent()
    generations = opportunity_repository or InMemoryOpportunityRepository()
    signals = signal_provider or LocalManualSignalProvider()
    generation_lock = asyncio.Lock()
    item_update_locks: dict[str, asyncio.Lock] = {}

    async def workflow_source_matches_lineage(
        generation: OpportunityResponse,
        source_generation_id: str | None,
        source_item_id: str | None,
    ) -> bool:
        """Validate an adoption against the snapshot's immutable ancestry.

        Unchanged opportunities retain their item ID when a child snapshot is
        created.  Their workflow keeps the *actual* source generation where the
        handoff happened; rewriting that provenance to every newer child would
        be inaccurate.  A workflow is therefore valid for the current snapshot
        only when its source is this generation or one of its ancestors and the
        same item existed in that source snapshot.
        """

        if not source_generation_id or not source_item_id:
            return False
        current = generation
        visited: set[str] = set()
        while current.id not in visited:
            visited.add(current.id)
            if current.request.workspace_id != generation.request.workspace_id:
                return False
            if current.id == source_generation_id:
                return any(item.id == source_item_id for item in current.opportunities)
            if current.parent_generation_id is None:
                return False
            try:
                current = await generations.get(current.parent_generation_id)
            except OpportunityNotFoundError:
                return False
        return False

    async def history_context(workspace_id: str) -> tuple[list[str], list[str]]:
        workflows = await repository.list(workspace_id)
        workflow_topics = [
            workflow.input.topic
            for workflow in workflows
            if workflow.input.workspace_id == workspace_id
        ]
        prior_generations = await generations.list(limit=100, workspace_id=workspace_id)
        latest_feedback_by_item = {}
        for generation in prior_generations:
            if generation.status != OpportunityGenerationStatus.COMPLETED:
                continue
            for item in generation.opportunities:
                if item.status != OpportunityItemStatus.NEW:
                    latest_feedback_by_item.setdefault(item.id, item)
        positive_topics = [
            item.topic
            for item in latest_feedback_by_item.values()
            if item.status in {OpportunityItemStatus.SAVED, OpportunityItemStatus.ADOPTED}
        ]
        dismissed_topics = [
            item.topic
            for item in latest_feedback_by_item.values()
            if item.status == OpportunityItemStatus.DISMISSED
        ]
        return (
            list(dict.fromkeys(workflow_topics + positive_topics)),
            list(dict.fromkeys(dismissed_topics)),
        )

    def pending_generation(
        payload: OpportunityRequest,
        generation_id: str,
        request_hash: str,
        *,
        parent_generation_id: str | None = None,
        revision: int = 0,
    ) -> OpportunityResponse:
        now = _utc_now()
        return OpportunityResponse(
            id=generation_id,
            status=OpportunityGenerationStatus.PENDING,
            parent_generation_id=parent_generation_id,
            revision=revision,
            request_hash=request_hash,
            request=payload,
            seed=payload.topic,
            generation_mode=getattr(
                opportunity_agent, "generation_mode", "local_rule"
            ),
            provider=getattr(opportunity_agent, "provider", opportunity_agent.__class__.__name__),
            model=getattr(opportunity_agent, "model", "unspecified"),
            context_summary="內容機會正在產生中。",
            created_at=now,
            updated_at=now,
            generated_at=now,
        )

    async def run_generation(
        payload: OpportunityRequest,
        generation_id: str,
        request_hash: str,
        *,
        parent_generation_id: str | None = None,
        revision: int = 0,
        context: tuple[list[str], list[str]] | None = None,
    ) -> OpportunityResponse:
        pending = pending_generation(
            payload,
            generation_id,
            request_hash,
            parent_generation_id=parent_generation_id,
            revision=revision,
        )
        await generations.save(pending)
        try:
            historical_topics, dismissed_topics = context or await history_context(
                payload.workspace_id
            )
            effective_payload = payload.model_copy(
                update={
                    "exclude_topics": list(
                        dict.fromkeys(payload.exclude_topics + dismissed_topics)
                    )
                },
                deep=True,
            )
            collected_signals = await signals.collect(effective_payload)
            generated = await opportunity_agent.generate(
                effective_payload,
                historical_topics,
                collected_signals,
                generation_id,
            )
            now = _utc_now()
            completed = generated.model_copy(
                update={
                    "id": generation_id,
                    "status": OpportunityGenerationStatus.COMPLETED,
                    "parent_generation_id": parent_generation_id,
                    "revision": revision,
                    "request_hash": request_hash,
                    "request": payload,
                    "error": None,
                    "created_at": pending.created_at,
                    "updated_at": now,
                    "generated_at": now,
                    "events": generated.events
                    + (
                        [
                            OpportunityGenerationEvent(
                                action="feedback_context_applied",
                                note=(
                                    f"納入 {len(historical_topics)} 筆既有內容，並排除 "
                                    f"{len(dismissed_topics)} 筆已略過題目。"
                                ),
                            )
                        ]
                        if historical_topics or dismissed_topics
                        else []
                    ),
                },
                deep=True,
            )
            await generations.save(completed)
            return completed
        except GenerationQuotaExceededError as exc:
            failed = pending.model_copy(
                update={
                    "status": OpportunityGenerationStatus.FAILED,
                    "error": "GenerationQuotaExceededError: daily generation quota exceeded",
                    "updated_at": _utc_now(),
                    "events": [
                        OpportunityGenerationEvent(
                            action="generation_failed",
                            note="今日 AI 生成額度已用完；未呼叫外部模型。",
                        )
                    ],
                },
                deep=True,
            )
            await generations.save(failed)
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "daily_generation_quota_exceeded",
                    "limit": exc.limit,
                    "resets_at": exc.resets_at.isoformat(),
                },
            ) from exc
        except Exception as exc:
            logger.exception(
                "Opportunity generation failed for generation_id=%s provider=%s",
                generation_id,
                getattr(opportunity_agent, "provider", opportunity_agent.__class__.__name__),
            )
            safe_error = f"{exc.__class__.__name__}: opportunity generation failed"
            failed = pending.model_copy(
                update={
                    "status": OpportunityGenerationStatus.FAILED,
                    "error": safe_error,
                    "updated_at": _utc_now(),
                    "events": [
                        OpportunityGenerationEvent(
                            action="generation_failed",
                            note=(
                                f"{safe_error}; generation_id={generation_id}. "
                                "完整診斷只記錄於伺服器日誌。"
                            ),
                        )
                    ],
                },
                deep=True,
            )
            await generations.save(failed)
            raise HTTPException(status_code=502, detail="opportunity generation failed") from exc

    @router.post("/generate", response_model=OpportunityResponse)
    async def generate_opportunities(
        payload: OpportunityRequest,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> OpportunityResponse:
        if (
            "workspace_id" in payload.model_fields_set
            and payload.workspace_id != workspace_id
        ):
            raise HTTPException(
                status_code=422,
                detail="workspace_id must match the authenticated workspace context",
            )
        payload = payload.model_copy(update={"workspace_id": workspace_id}, deep=True)
        async with generation_lock:
            context = await history_context(payload.workspace_id)
            request_hash = _request_hash(payload, *context)
            cached = await generations.find_by_request_hash(
                request_hash, payload.workspace_id
            )
            if cached is not None:
                return cached
            return await run_generation(
                payload,
                generation_id=str(uuid4()),
                request_hash=request_hash,
                context=context,
            )

    @router.get("", response_model=list[OpportunityResponse])
    async def list_opportunities(
        limit: int = Query(default=50, ge=1, le=100),
        requested_workspace_id: str | None = Query(
            default=None, alias="workspace_id", max_length=100
        ),
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> list[OpportunityResponse]:
        if requested_workspace_id is not None:
            normalized_requested = " ".join(requested_workspace_id.split())
            if normalized_requested != workspace_id:
                raise HTTPException(status_code=404, detail="resource not found")
        return await generations.list(limit=limit, workspace_id=workspace_id)

    @router.get("/{generation_id}", response_model=OpportunityResponse)
    async def get_opportunity(
        generation_id: str,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> OpportunityResponse:
        try:
            generation = await generations.get(generation_id)
        except OpportunityNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        ensure_workspace(generation.request.workspace_id, workspace_id)
        return generation

    @router.patch(
        "/{generation_id}/items/{item_id}", response_model=OpportunityResponse
    )
    async def update_opportunity_item(
        generation_id: str,
        item_id: str,
        payload: OpportunityItemUpdate,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> OpportunityResponse:
        if (
            payload.status is None
            and payload.feedback_note is None
            and payload.adopted_workflow_id is None
        ):
            raise HTTPException(
                status_code=422,
                detail="status, feedback_note or adopted_workflow_id is required",
            )
        if (
            payload.adopted_workflow_id is not None
            and payload.status != OpportunityItemStatus.ADOPTED
        ):
            raise HTTPException(
                status_code=422,
                detail="adopted_workflow_id is only valid when status is adopted",
            )
        if (
            payload.status == OpportunityItemStatus.ADOPTED
            and payload.adopted_workflow_id is None
        ):
            raise HTTPException(
                status_code=422,
                detail="adopted_workflow_id is required when status is adopted",
            )
        lock = item_update_locks.setdefault(generation_id, asyncio.Lock())
        async with lock:
            try:
                generation = await generations.get(generation_id)
            except OpportunityNotFoundError as exc:
                raise HTTPException(
                    status_code=404, detail="resource not found"
                ) from exc
            ensure_workspace(generation.request.workspace_id, workspace_id)
            if not any(item.id == item_id for item in generation.opportunities):
                raise HTTPException(status_code=404, detail="opportunity item not found")
            if payload.status == OpportunityItemStatus.ADOPTED:
                try:
                    adopted_workflow = await repository.get(
                        payload.adopted_workflow_id or "",
                        generation.request.workspace_id,
                    )
                except WorkflowNotFoundError as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="adopted workflow is not available for this item",
                    ) from exc
                if (
                    adopted_workflow.input.source_opportunity_id != item_id
                    or adopted_workflow.input.workspace_id
                    != generation.request.workspace_id
                    or not await workflow_source_matches_lineage(
                        generation,
                        adopted_workflow.input.source_generation_id,
                        adopted_workflow.input.source_opportunity_id,
                    )
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="adopted workflow is not available for this item",
                    )
            found = False
            updated_items = []
            for item in generation.opportunities:
                if item.id != item_id:
                    updated_items.append(item)
                    continue
                found = True
                changes = {}
                if payload.status is not None:
                    changes["status"] = payload.status
                if payload.feedback_note is not None:
                    changes["feedback_note"] = payload.feedback_note
                if payload.status == OpportunityItemStatus.ADOPTED:
                    changes["adopted_workflow_id"] = payload.adopted_workflow_id
                elif payload.status is not None:
                    changes["adopted_workflow_id"] = None
                updated_items.append(item.model_copy(update=changes, deep=True))
            if not found:
                raise HTTPException(status_code=404, detail="opportunity item not found")
            updated = generation.model_copy(
                update={
                    "opportunities": updated_items,
                    "updated_at": _utc_now(),
                    "events": generation.events
                    + [
                        OpportunityGenerationEvent(
                            action="item_updated",
                            item_id=item_id,
                            note=(
                                f"status={payload.status.value if payload.status else 'unchanged'}; "
                                f"feedback={'updated' if payload.feedback_note is not None else 'unchanged'}; "
                                f"workflow={payload.adopted_workflow_id or 'unchanged'}"
                            ),
                        )
                    ],
                },
                deep=True,
            )
            await generations.save(updated)
            return updated

    @router.post(
        "/{generation_id}/items/{item_id}/regenerate",
        response_model=OpportunityResponse,
    )
    async def regenerate_opportunity_item(
        generation_id: str,
        item_id: str,
        payload: OpportunityRegenerateRequest | None = None,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> OpportunityResponse:
        lock = item_update_locks.setdefault(generation_id, asyncio.Lock())
        async with lock:
            try:
                parent = await generations.get(generation_id)
            except OpportunityNotFoundError as exc:
                raise HTTPException(
                    status_code=404, detail="resource not found"
                ) from exc
            ensure_workspace(parent.request.workspace_id, workspace_id)
        old_item = next((item for item in parent.opportunities if item.id == item_id), None)
        if old_item is None:
            raise HTTPException(status_code=404, detail="opportunity item not found")

        overrides = (
            payload.model_dump(
                exclude_none=True,
                exclude={"exclude_topics", "modification_instruction"},
            )
            if payload is not None
            else {}
        )
        modification_instruction = (
            payload.modification_instruction if payload is not None else None
        )
        if modification_instruction:
            base_constraints = list(
                overrides.get("constraints", parent.request.constraints)
            )
            overrides["constraints"] = [
                *base_constraints[:19],
                f"人工單題修改指示：{modification_instruction}",
            ]
        requested_exclusions = payload.exclude_topics if payload is not None else []
        variation = (
            payload.variation
            if payload is not None and payload.variation is not None
            else parent.request.variation + 1
        )
        overrides["variation"] = variation
        overrides["exclude_topics"] = list(
            dict.fromkeys(
                parent.request.exclude_topics
                + [item.topic for item in parent.opportunities]
                + requested_exclusions
            )
        )
        try:
            regenerated_request = OpportunityRequest.model_validate(
                {**parent.request.model_dump(), **overrides}
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "invalid regeneration request",
                    "errors": exc.errors(include_context=False),
                },
            ) from exc
        context = await history_context(regenerated_request.workspace_id)
        request_hash = _request_hash(
            regenerated_request,
            *context,
            cache_identity={
                "operation": "item_regeneration",
                "parent_generation_id": parent.id,
                "item_id": old_item.id,
            },
        )
        cached = await generations.find_by_request_hash(
            request_hash, regenerated_request.workspace_id
        )
        if cached is not None and cached.parent_generation_id == parent.id:
            return cached

        child_id = str(uuid4())
        generated = await run_generation(
            regenerated_request,
            generation_id=child_id,
            request_hash=request_hash,
            parent_generation_id=parent.id,
            revision=parent.revision + 1,
            context=context,
        )
        if not generated.opportunities:
            failed = generated.model_copy(
                update={
                    "status": OpportunityGenerationStatus.FAILED,
                    "error": "no replacement candidate remained after exclusions",
                    "updated_at": _utc_now(),
                },
                deep=True,
            )
            await generations.save(failed)
            raise HTTPException(status_code=409, detail="no replacement candidate available")

        generated_replacement = generated.opportunities[0]
        replacement = generated_replacement.model_copy(
            update={
                "id": "opp-"
                + sha256(
                    f"{child_id}:replacement:{generated_replacement.topic}".encode()
                ).hexdigest()[:12],
                "status": OpportunityItemStatus.NEW,
                "feedback_note": modification_instruction or "",
                "adopted_workflow_id": None,
            },
            deep=True,
        )
        child_items = []
        for item in parent.opportunities:
            if item.id == item_id:
                child_items.append(replacement)
                continue
            child_items.append(item.model_copy(deep=True))
        completed_child = generated.model_copy(
            update={
                "opportunities": child_items,
                "events": generated.events
                + [
                    OpportunityGenerationEvent(
                        action="item_regenerated",
                        item_id=replacement.id,
                        from_topic=old_item.topic,
                        to_topic=replacement.topic,
                        note=(
                            f"由 generation {parent.id} 的 item {old_item.id} 建立 "
                            f"revision {parent.revision + 1}；未替換題目沿用原 item ID。"
                            + (
                                "本次已套用人工單題修改指示。"
                                if modification_instruction
                                else "本次未提供額外人工修改指示。"
                            )
                            + "既有採用連結保留工作流真正的來源 generation，"
                            + "並以祖先版本關係驗證。"
                        ),
                    )
                ],
                "updated_at": _utc_now(),
            },
            deep=True,
        )
        await generations.save(completed_child)
        return completed_child

    return router
