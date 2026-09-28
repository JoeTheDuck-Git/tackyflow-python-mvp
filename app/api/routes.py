import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse

from app.domain.models import (
    HumanDecision,
    ImageGenerationRequest,
    PublicationManagementRequest,
    ProductionPlanRevisionRequest,
    ScriptRevisionRequest,
    StageManagementRequest,
    WorkflowArchiveRequest,
    WorkflowInput,
    WorkflowReopenRequest,
    WorkflowRun,
)
from app.workflow.orchestrator import InvalidWorkflowTransitionError, WorkflowOrchestrator
from app.workflow.repository import WorkflowConflictError, WorkflowNotFoundError
from app.api.workspace import ensure_workspace, resolve_actor_id, resolve_workspace_id
from app.opportunities.repository import OpportunityNotFoundError, OpportunityRepository
from app.usage.repository import GenerationQuotaExceededError


logger = logging.getLogger(__name__)


def _quota_http_error(exc: GenerationQuotaExceededError) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "message": "今日 AI 生成額度已用完，請在額度重置後再試。",
            "code": "daily_generation_quota_exceeded",
            "limit": exc.limit,
            "resets_at": exc.resets_at.isoformat(),
        },
    )


def build_router(
    orchestrator: WorkflowOrchestrator,
    opportunity_repository: OpportunityRepository | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

    async def get_scoped_workflow(workflow_id: str, workspace_id: str) -> WorkflowRun:
        try:
            workflow = await orchestrator.repository.get(workflow_id, workspace_id)
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        ensure_workspace(workflow.input.workspace_id, workspace_id)
        return workflow

    @router.post("", response_model=WorkflowRun, status_code=status.HTTP_201_CREATED)
    async def create_workflow(
        payload: WorkflowInput,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> WorkflowRun:
        if (
            "workspace_id" in payload.model_fields_set
            and payload.workspace_id != workspace_id
        ):
            raise HTTPException(
                status_code=422,
                detail="workspace_id must match the authenticated workspace context",
            )
        scoped_payload = payload.model_copy(update={"workspace_id": workspace_id}, deep=True)
        if (
            opportunity_repository is not None
            and scoped_payload.source_generation_id
            and scoped_payload.source_opportunity_id
        ):
            try:
                generation = await opportunity_repository.get(
                    scoped_payload.source_generation_id
                )
                ensure_workspace(generation.request.workspace_id, workspace_id)
                if not any(
                    item.id == scoped_payload.source_opportunity_id
                    for item in generation.opportunities
                ):
                    raise OpportunityNotFoundError(
                        scoped_payload.source_opportunity_id
                    )
            except (OpportunityNotFoundError, HTTPException) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="source opportunity is not available in this workspace",
                ) from exc
        try:
            return await orchestrator.create(scoped_payload)
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.get("", response_model=list[WorkflowRun])
    async def list_workflows(
        workspace_id: str = Depends(resolve_workspace_id),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[WorkflowRun]:
        return await orchestrator.repository.list(
            workspace_id, limit=limit, offset=offset
        )

    @router.get("/{workflow_id}", response_model=WorkflowRun)
    async def get_workflow(
        workflow_id: str,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> WorkflowRun:
        return await get_scoped_workflow(workflow_id, workspace_id)

    @router.post("/{workflow_id}/run", response_model=WorkflowRun)
    async def run_workflow(
        workflow_id: str,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.run_until_gate(workflow_id)
        except GenerationQuotaExceededError as exc:
            raise _quota_http_error(exc) from exc
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.post("/{workflow_id}/advance", response_model=WorkflowRun)
    async def advance_workflow(
        workflow_id: str,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.advance(workflow_id)
        except GenerationQuotaExceededError as exc:
            raise _quota_http_error(exc) from exc
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.patch("/{workflow_id}/stage", response_model=WorkflowRun)
    async def manage_workflow_stage(
        workflow_id: str,
        payload: StageManagementRequest,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.move_to_stage(
                workflow_id, payload.target_stage, payload.note
            )
        except GenerationQuotaExceededError as exc:
            raise _quota_http_error(exc) from exc
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.patch("/{workflow_id}/publication", response_model=WorkflowRun)
    async def manage_workflow_publication(
        workflow_id: str,
        payload: PublicationManagementRequest,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.update_publication(workflow_id, payload)
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.post("/{workflow_id}/reopen", response_model=WorkflowRun)
    async def reopen_workflow(
        workflow_id: str,
        payload: WorkflowReopenRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.reopen_for_revision(
                workflow_id, payload.note, actor_id=actor_id
            )
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.patch("/{workflow_id}/archive", response_model=WorkflowRun)
    async def archive_workflow(
        workflow_id: str,
        payload: WorkflowArchiveRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.set_archived(
                workflow_id, payload.archived, actor_id=actor_id
            )
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.patch("/{workflow_id}/script", response_model=WorkflowRun)
    async def revise_workflow_script(
        workflow_id: str,
        payload: ScriptRevisionRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.revise_script(
                workflow_id,
                payload.full_text,
                regenerate_production=payload.regenerate_production,
                actor_id=actor_id,
            )
        except GenerationQuotaExceededError as exc:
            raise _quota_http_error(exc) from exc
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.patch("/{workflow_id}/production-plan", response_model=WorkflowRun)
    async def revise_workflow_production_plan(
        workflow_id: str,
        payload: ProductionPlanRevisionRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.revise_production_plan(
                workflow_id,
                payload.visual_cues,
                actor_id=actor_id,
            )
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.post("/{workflow_id}/visual-review", response_model=WorkflowRun)
    async def review_workflow_visual_plan(
        workflow_id: str,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.review_visual_plan(workflow_id, actor_id=actor_id)
        except GenerationQuotaExceededError as exc:
            raise _quota_http_error(exc) from exc
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc

    @router.post("/{workflow_id}/generated-images", response_model=WorkflowRun)
    async def generate_workflow_image(
        workflow_id: str,
        payload: ImageGenerationRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.generate_image_asset(
                workflow_id, payload, actor_id=actor_id
            )
        except GenerationQuotaExceededError as exc:
            raise _quota_http_error(exc) from exc
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(status_code=409, detail="workflow changed; reload and retry") from exc
        except Exception as exc:
            logger.exception("image generation failed", extra={"workflow_id": workflow_id})
            raise HTTPException(status_code=502, detail="圖片生成失敗，請稍後再試。") from exc

    @router.get("/{workflow_id}/generated-images/{asset_id}")
    async def get_workflow_image(
        workflow_id: str,
        asset_id: str,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> Response:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            content = await orchestrator.generated_image_content(workflow_id, asset_id)
        except (WorkflowNotFoundError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        return Response(content=content, media_type="image/png", headers={"Content-Disposition": f'inline; filename="tackyflow-{asset_id}.png"', "Cache-Control": "private, max-age=300"})

    @router.post("/{workflow_id}/decisions", response_model=WorkflowRun)
    async def decide_workflow(
        workflow_id: str,
        payload: HumanDecision,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> WorkflowRun:
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            return await orchestrator.decide(workflow_id, payload, actor_id=actor_id)
        except GenerationQuotaExceededError as exc:
            raise _quota_http_error(exc) from exc
        except WorkflowNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except InvalidWorkflowTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail="workflow changed while this request was running; reload and retry",
            ) from exc

    @router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_workflow(
        workflow_id: str,
        workspace_id: str = Depends(resolve_workspace_id),
    ) -> Response:
        released_snapshots = []
        try:
            await get_scoped_workflow(workflow_id, workspace_id)
            if opportunity_repository is not None:
                released_snapshots = (
                    await opportunity_repository.release_workflow_adoptions(
                        workflow_id, workspace_id
                    )
                )
            await orchestrator.repository.delete(workflow_id, workspace_id)
        except WorkflowNotFoundError as exc:
            # A concurrent delete may win after the initial lookup. In that case the
            # adoption cleanup is still correct because the workflow no longer exists.
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except Exception:
            # Cross-repository deletion cannot be a single transaction for every
            # backend. Restore adoption snapshots only when we can confirm the workflow
            # still exists; restoring after a committed delete would recreate a
            # dangling adopted_workflow_id.
            if opportunity_repository is not None and released_snapshots:
                try:
                    await orchestrator.repository.get(workflow_id, workspace_id)
                except WorkflowNotFoundError:
                    pass
                except Exception:
                    logger.exception(
                        "Could not determine workflow deletion outcome for workflow_id=%s",
                        workflow_id,
                    )
                else:
                    try:
                        for snapshot in released_snapshots:
                            await opportunity_repository.save(snapshot)
                    except Exception:
                        logger.exception(
                            "Could not compensate opportunity adoption cleanup for workflow_id=%s",
                            workflow_id,
                        )
            raise
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
