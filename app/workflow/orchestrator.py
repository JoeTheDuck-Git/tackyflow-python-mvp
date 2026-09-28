import asyncio
import logging
import re
from copy import deepcopy
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from app.agents.base import WorkflowAgent
from app.domain.models import (
    AgentResult,
    DecisionAction,
    ExecutionLogEntry,
    HumanDecision,
    HumanRequest,
    ImageGenerationRequest,
    PublicationManagementRequest,
    PublicationStatus,
    RiskLevel,
    VisualCue,
    VisibleStage,
    WorkflowInput,
    WorkflowRun,
    WorkflowStatus,
    utc_now,
)
from app.workflow.policy import evaluate_agent_result
from app.workflow.repository import WorkflowRepository
from app.llm.provider import ContentProvider
from app.media.generator import ImageGenerator
from app.usage.repository import GenerationQuotaExceededError, SQLiteUsageRepository
from app.workflow.artifacts import (
    build_content_preview,
    build_production_preview,
    build_reference_analysis,
)


logger = logging.getLogger(__name__)


class InvalidWorkflowTransitionError(RuntimeError):
    pass


class WorkflowOrchestrator:
    STAGE_ORDER = [
        VisibleStage.REQUIREMENTS,
        VisibleStage.AI_CREATION,
        VisibleStage.PRODUCTION_PACKAGE,
        VisibleStage.APPROVAL_PUBLISH,
    ]

    def __init__(
        self,
        repository: WorkflowRepository,
        creation_agents: Iterable[WorkflowAgent],
        production_agents: Iterable[WorkflowAgent],
        content_provider: ContentProvider | None = None,
        editorial_agents: Iterable[WorkflowAgent] | None = None,
        visual_agents: Iterable[WorkflowAgent] | None = None,
        image_generator: ImageGenerator | None = None,
        usage_repository: SQLiteUsageRepository | None = None,
        daily_generation_limit: int = 30,
    ) -> None:
        self.repository = repository
        self.creation_agents = list(creation_agents)
        self.production_agents = list(production_agents)
        self.editorial_agents = list(editorial_agents or [])
        self.visual_agents = list(visual_agents or [])
        self.content_provider = content_provider
        self.image_generator = image_generator
        self.usage_repository = usage_repository
        self.daily_generation_limit = daily_generation_limit
        self._source_create_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def create(self, workflow_input: WorkflowInput) -> WorkflowRun:
        if (
            workflow_input.source_generation_id
            and workflow_input.source_opportunity_id
        ):
            source_key = (
                workflow_input.workspace_id,
                workflow_input.source_generation_id,
                workflow_input.source_opportunity_id,
            )
            lock = self._source_create_locks.setdefault(source_key, asyncio.Lock())
            async with lock:
                existing_workflows = await self.repository.list(
                    workflow_input.workspace_id, limit=200
                )
                for existing in existing_workflows:
                    if (
                        existing.input.workspace_id == workflow_input.workspace_id
                        and existing.input.source_generation_id
                        == workflow_input.source_generation_id
                        and existing.input.source_opportunity_id
                        == workflow_input.source_opportunity_id
                        and existing.status != WorkflowStatus.CANCELLED
                    ):
                        if existing.input != workflow_input:
                            raise InvalidWorkflowTransitionError(
                                "這個內容機會已用不同需求建立任務；請開啟既有任務，或先建立新的內容機會版本。"
                            )
                        return existing
                return await self._create_new(workflow_input)

        return await self._create_new(workflow_input)

    async def _create_new(self, workflow_input: WorkflowInput) -> WorkflowRun:
        workflow = WorkflowRun(input=workflow_input)
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.REQUIREMENTS,
                event_type="workflow.created",
                status="completed",
                title="需求已建立",
                summary=f"已收到主題「{workflow_input.topic}」，準備判斷執行路線。",
                details={
                    "goal": workflow_input.goal,
                    "platforms": workflow_input.platforms,
                    "output_type": workflow_input.output_type,
                    "target_word_count": workflow_input.target_word_count,
                    "reference_count": len(workflow_input.reference_materials),
                    "workspace_id": workflow_input.workspace_id,
                    "source_generation_id": workflow_input.source_generation_id,
                    "source_opportunity_id": workflow_input.source_opportunity_id,
                },
            )
        )
        return await self.repository.save(workflow)

    async def run_until_gate(self, workflow_id: str) -> WorkflowRun:
        workflow = await self.repository.get(workflow_id)
        self._ensure_runnable(workflow)
        if workflow.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            return workflow

        while workflow.status == WorkflowStatus.RUNNING:
            previous_stage = workflow.stage
            await self._advance_safely(workflow)
            if workflow.stage == previous_stage and workflow.status == WorkflowStatus.RUNNING:
                break

        workflow.updated_at = utc_now()
        return await self.repository.save(workflow)

    async def advance(self, workflow_id: str) -> WorkflowRun:
        """只推進一個可見階段，供 UI 呈現與 E2E 驗證使用。"""
        workflow = await self.repository.get(workflow_id)
        self._ensure_runnable(workflow)
        if workflow.status in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            return workflow
        await self._advance_safely(workflow)
        workflow.updated_at = utc_now()
        return await self.repository.save(workflow)

    async def move_to_stage(
        self,
        workflow_id: str,
        target_stage: VisibleStage,
        note: str,
    ) -> WorkflowRun:
        """由內容負責人管理可見階段，同時維持下游成果的一致性。"""
        workflow = await self.repository.get(workflow_id)
        current_index = self.STAGE_ORDER.index(workflow.stage)
        target_index = self.STAGE_ORDER.index(target_stage)

        if current_index == target_index:
            return workflow

        previous_stage = workflow.stage
        if target_index < current_index:
            self._clear_downstream_for_stage(workflow, target_stage)
            workflow.stage = target_stage
            workflow.status = WorkflowStatus.DRAFT
            workflow.human_request = None
        else:
            self._ensure_runnable(workflow)
            while (
                self.STAGE_ORDER.index(workflow.stage) < target_index
                and workflow.status == WorkflowStatus.RUNNING
            ):
                await self._advance_safely(workflow)

        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=workflow.stage,
                event_type="workflow.stage_changed",
                status=(
                    "completed"
                    if workflow.stage == target_stage
                    else "waiting"
                ),
                title="內容階段已調整",
                summary=(
                    f"由「{_stage_label(previous_stage)}」調整至「{_stage_label(workflow.stage)}」。"
                    if workflow.stage == target_stage
                    else f"原計畫移至「{_stage_label(target_stage)}」，流程在「{_stage_label(workflow.stage)}」等待人工處理。"
                ),
                agent="orchestrator",
                details={
                    "from_stage": previous_stage.value,
                    "requested_stage": target_stage.value,
                    "reached_stage": workflow.stage.value,
                    "note": note,
                },
            )
        )
        workflow.updated_at = utc_now()
        return await self.repository.save(workflow)

    async def update_publication(
        self,
        workflow_id: str,
        publication: PublicationManagementRequest,
    ) -> WorkflowRun:
        """保存平台中立的本機發布狀態；不直接呼叫任何外部社群平台。"""
        workflow = await self.repository.get(workflow_id)
        production = workflow.artifacts.get("production_package", {})
        if workflow.status != WorkflowStatus.COMPLETED:
            raise InvalidWorkflowTransitionError("內容尚未完成最終核准，不可安排發布")
        if not production.get("preview_ready"):
            raise InvalidWorkflowTransitionError("製作素材包尚未完成，不可安排發布")
        if (
            publication.status == PublicationStatus.SCHEDULED
            and publication.scheduled_at is None
        ):
            raise InvalidWorkflowTransitionError("排程發布必須指定日期與時間")

        updated_at = utc_now()
        previous_publication = workflow.artifacts.get("publication", {})
        published_url = publication.published_url or previous_publication.get("published_url", "")
        target_platform = publication.target_platform or previous_publication.get("target_platform", "")
        workflow.artifacts["publication"] = {
            "status": publication.status.value,
            "scheduled_at": (
                publication.scheduled_at.isoformat()
                if publication.scheduled_at is not None
                else None
            ),
            "note": publication.note,
            "published_url": published_url,
            "target_platform": target_platform,
            "published_at": (
                updated_at.isoformat()
                if publication.status == PublicationStatus.PUBLISHED
                else previous_publication.get("published_at")
            ),
            "updated_at": updated_at.isoformat(),
            "mode": "semi_automatic",
        }
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.APPROVAL_PUBLISH,
                event_type="publication.updated",
                status="completed",
                title=_publication_title(publication.status),
                summary=_publication_summary(publication),
                details={
                    "publication_status": publication.status.value,
                    "scheduled_at": workflow.artifacts["publication"]["scheduled_at"],
                    "note": publication.note,
                    "published_url": published_url,
                    "target_platform": target_platform,
                    "mode": "semi_automatic",
                },
            )
        )
        workflow.updated_at = updated_at
        return await self.repository.save(workflow)

    async def reopen_for_revision(
        self,
        workflow_id: str,
        note: str,
        *,
        actor_id: str = "local-user",
    ) -> WorkflowRun:
        workflow = await self.repository.get(workflow_id)
        if workflow.status != WorkflowStatus.CANCELLED:
            raise InvalidWorkflowTransitionError("只有工作流已退回的任務可以重新開啟修改")
        prior_decisions = list(workflow.artifacts.get("human_decisions", []))
        workflow.revision_history.append(
            {
                "version": len(workflow.revision_history) + 1,
                "created_at": utc_now().isoformat(),
                "feedback": note,
                "script": workflow.artifacts.get("script"),
                "production_package": workflow.artifacts.get("production_package"),
            }
        )
        self._clear_downstream_for_stage(workflow, VisibleStage.AI_CREATION)
        if prior_decisions:
            workflow.artifacts["human_decisions"] = prior_decisions
        workflow.artifacts.pop("archive", None)
        workflow.stage = VisibleStage.AI_CREATION
        workflow.status = WorkflowStatus.DRAFT
        workflow.human_request = None
        workflow.last_review_feedback = note
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.AI_CREATION,
                event_type="workflow.reopened",
                status="completed",
                title="退回任務已重新開啟",
                summary="已保留上一版紀錄，並依新的修改說明重新進入 AI 製作。",
                agent="orchestrator",
                details={"note": note, "actor_id": actor_id},
            )
        )
        workflow.updated_at = utc_now()
        return await self.repository.save(workflow)

    async def set_archived(
        self,
        workflow_id: str,
        archived: bool,
        *,
        actor_id: str = "local-user",
    ) -> WorkflowRun:
        workflow = await self.repository.get(workflow_id)
        if workflow.status != WorkflowStatus.CANCELLED:
            raise InvalidWorkflowTransitionError("只有工作流已退回的任務可以封存")
        updated_at = utc_now()
        workflow.artifacts["archive"] = {
            "archived": archived,
            "updated_at": updated_at.isoformat(),
            "actor_id": actor_id,
        }
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=workflow.stage,
                event_type="workflow.archived" if archived else "workflow.unarchived",
                status="completed",
                title="任務已封存" if archived else "任務已取消封存",
                summary="已從預設發布清單隱藏。" if archived else "已恢復顯示於發布清單。",
                agent="orchestrator",
                details={"archived": archived, "actor_id": actor_id},
            )
        )
        workflow.updated_at = updated_at
        return await self.repository.save(workflow)

    async def revise_script(
        self,
        workflow_id: str,
        full_text: str,
        *,
        regenerate_production: bool,
        actor_id: str = "local-user",
    ) -> WorkflowRun:
        """保存人工修訂稿，並由使用者決定是否重做下游製作規劃。"""
        workflow = await self.repository.get(workflow_id)
        script = workflow.artifacts.get("script")
        if not script:
            raise InvalidWorkflowTransitionError("完整逐字稿尚未產生，無法進行人工修改")

        normalized_text = full_text.strip()
        if not normalized_text:
            raise InvalidWorkflowTransitionError("完整逐字稿不可為空白")
        if normalized_text == str(script.get("full_text", "")).strip():
            raise InvalidWorkflowTransitionError("逐字稿內容沒有變更")

        previous_production = deepcopy(workflow.artifacts.get("production_package"))
        if not regenerate_production and not previous_production:
            raise InvalidWorkflowTransitionError(
                "目前尚無可沿用的製作規劃，請選擇依新版逐字稿重新產生"
            )
        workflow.revision_history.append(
            {
                "version": len(workflow.revision_history) + 1,
                "created_at": utc_now().isoformat(),
                "feedback": "人工修改完整逐字稿",
                "script": deepcopy(script),
                "production_package": previous_production,
                "actor_id": actor_id,
            }
        )

        sections = _redistribute_script_sections(normalized_text, script.get("sections", []))
        word_count = len("".join(normalized_text.split()))
        target = int(script.get("target_word_count") or workflow.input.target_word_count)
        edited_at = utc_now()
        script.update(
            {
                "sections": sections,
                "full_text": normalized_text,
                "word_count": word_count,
                "word_count_difference": word_count - target,
                "quality_status": "human_edited",
                "human_revision": {
                    "edited_at": edited_at.isoformat(),
                    "actor_id": actor_id,
                    "regenerate_production": regenerate_production,
                },
            }
        )
        workflow.artifacts.pop("publication", None)
        workflow.artifacts.pop("human_decisions", None)
        workflow.human_request = None
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.APPROVAL_PUBLISH,
                event_type="script.human_revised",
                status="completed",
                title="人工修訂稿已存檔",
                summary=f"完整逐字稿已更新為 {word_count} 字。",
                details={
                    "actor_id": actor_id,
                    "word_count": word_count,
                    "regenerate_production": regenerate_production,
                },
            )
        )

        if regenerate_production:
            workflow.artifacts.pop("production_package", None)
            production_agent_names = {
                *(agent.name for agent in self.production_agents),
                *(agent.name for agent in self.visual_agents),
            }
            workflow.agent_results = [
                result for result in workflow.agent_results
                if result.agent not in production_agent_names
            ]
            workflow.stage_agent_positions.pop(VisibleStage.PRODUCTION_PACKAGE.value, None)
            for checkpoint_key in list(workflow.stage_agent_positions):
                if checkpoint_key.startswith(f"{VisibleStage.PRODUCTION_PACKAGE.value}:"):
                    workflow.stage_agent_positions.pop(checkpoint_key, None)
            workflow.stage = VisibleStage.PRODUCTION_PACKAGE
            workflow.status = WorkflowStatus.RUNNING
            workflow.execution_log.append(
                ExecutionLogEntry(
                    stage=VisibleStage.PRODUCTION_PACKAGE,
                    event_type="production.regeneration_requested",
                    status="running",
                    title="重新設計製作規劃",
                    summary="正依人工修訂稿重新產生分鏡、視覺方向與 B-roll 建議。",
                    agent="production_planner",
                )
            )
            await self._advance_safely(workflow)
            if workflow.artifacts.get("production_package"):
                workflow.artifacts["production_package"]["script_sync_status"] = "regenerated_from_human_revision"
                workflow.artifacts["production_package"]["script_revision_at"] = edited_at.isoformat()
        else:
            if previous_production:
                workflow.artifacts["production_package"] = previous_production
                workflow.artifacts["production_package"]["script_sync_status"] = "kept_after_manual_edit"
                workflow.artifacts["production_package"]["script_revision_at"] = edited_at.isoformat()
            workflow.stage = VisibleStage.APPROVAL_PUBLISH
            self._request_final_approval(workflow)

        workflow.updated_at = utc_now()
        return await self.repository.save(workflow)

    async def revise_production_plan(
        self,
        workflow_id: str,
        visual_cues: list[VisualCue],
        *,
        actor_id: str = "local-user",
    ) -> WorkflowRun:
        """保存人工調整的 B-roll／視覺時間軸，並使既有發布核准失效。"""
        workflow = await self.repository.get(workflow_id)
        production = workflow.artifacts.get("production_package")
        if not production or not production.get("preview_ready"):
            raise InvalidWorkflowTransitionError("製作規劃尚未產生，無法修改視覺時間軸")

        storyboard = production.get("storyboard", [])
        valid_shots = {int(shot.get("shot", index + 1)) for index, shot in enumerate(storyboard)}
        total_seconds = int(
            production.get("estimated_duration_seconds")
            or sum(int(shot.get("duration_seconds") or 0) for shot in storyboard)
        )
        if total_seconds <= 0:
            raise InvalidWorkflowTransitionError("目前內容沒有可編輯的影片時長")

        seen_ids: set[str] = set()
        for cue in visual_cues:
            if cue.id in seen_ids:
                raise InvalidWorkflowTransitionError("素材段識別碼不可重複")
            seen_ids.add(cue.id)
            if cue.shot not in valid_shots:
                raise InvalidWorkflowTransitionError("素材段對應的分鏡不存在")
            if cue.start_seconds + cue.duration_seconds > total_seconds:
                raise InvalidWorkflowTransitionError("素材段超出影片總長，請調整開始時間或時長")

        workflow.revision_history.append(
            {
                "version": len(workflow.revision_history) + 1,
                "created_at": utc_now().isoformat(),
                "feedback": "人工修改 B-roll 與視覺時間軸",
                "script": deepcopy(workflow.artifacts.get("script")),
                "production_package": deepcopy(production),
                "actor_id": actor_id,
            }
        )
        updated_at = utc_now()
        cue_payload = [cue.model_dump(mode="json") for cue in visual_cues]
        production["visual_cues"] = cue_payload
        production["visual_summary"] = _build_visual_summary(cue_payload, total_seconds)
        production["manual_revision"] = {
            "edited_at": updated_at.isoformat(),
            "actor_id": actor_id,
            "cue_count": len(cue_payload),
        }
        production["script_sync_status"] = "human_visual_revision"
        review_id = str(uuid4())
        workflow.artifacts["_visual_review_context"] = {
            "mode": "human_revision",
            "review_id": review_id,
            "visual_cues": deepcopy(cue_payload),
        }
        if self.visual_agents:
            try:
                for agent in self.visual_agents:
                    result = await agent.execute(workflow)
                    workflow.agent_results.append(result)
                    workflow.execution_log.append(
                        ExecutionLogEntry(
                            stage=VisibleStage.PRODUCTION_PACKAGE,
                            event_type="visual_review.completed",
                            status=result.status,
                            title="視覺統籌代理完成人工素材審查",
                            summary=result.summary,
                            agent=result.agent,
                            confidence=None if result.is_simulated else result.confidence,
                            risk_level=result.risk_level,
                            details={
                                "review_id": review_id,
                                "issues": result.issues,
                                "evidence": result.evidence,
                                "human_input_preserved": True,
                                "artifact_summary": _agent_artifact_summary(result.artifact),
                            },
                        )
                    )
                self._apply_latest_visual_review(
                    workflow,
                    use_suggestions=False,
                    review_id=review_id,
                )
                self._apply_latest_youtube_reference_review(workflow)
                self._apply_latest_quality_gates(workflow)
            except Exception as exc:
                logger.exception(
                    "visual review failed after human revision",
                    extra={"workflow_id": workflow.id, "review_id": review_id},
                )
                production["visual_review"] = {
                    "review_id": review_id,
                    "status": "pending_retry",
                    "review_mode": "human_revision",
                    "human_input_preserved": True,
                    "issues": ["視覺統籌代理暫時無法完成審查，人工內容已保存並等待重試。"],
                    "error_type": type(exc).__name__,
                }
                workflow.execution_log.append(
                    ExecutionLogEntry(
                        stage=VisibleStage.PRODUCTION_PACKAGE,
                        event_type="visual_review.failed",
                        status="failed",
                        title="視覺統籌代理審查待重試",
                        summary="人工 B-roll／視覺修改已保留；代理審查暫時失敗，最終核准前請人工確認。",
                        agent="visual_director",
                        risk_level=RiskLevel.MEDIUM,
                        details={
                            "review_id": review_id,
                            "human_input_preserved": True,
                            "error_type": type(exc).__name__,
                        },
                    )
                )
        else:
            production["visual_review"] = {
                "review_id": review_id,
                "status": "not_configured",
                "review_mode": "human_revision",
                "human_input_preserved": True,
                "issues": [],
            }
        workflow.artifacts.pop("_visual_review_context", None)
        workflow.artifacts.pop("publication", None)
        workflow.artifacts.pop("human_decisions", None)
        workflow.stage = VisibleStage.APPROVAL_PUBLISH
        workflow.human_request = None
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.PRODUCTION_PACKAGE,
                event_type="production.human_revised",
                status="completed",
                title="人工素材時間軸已存檔",
                summary=f"已保存 {len(cue_payload)} 個 B-roll／視覺素材段。",
                details={
                    "actor_id": actor_id,
                    "cue_count": len(cue_payload),
                    **production["visual_summary"],
                },
            )
        )
        self._request_final_approval(workflow)
        workflow.updated_at = updated_at
        return await self.repository.save(workflow)

    async def review_visual_plan(
        self,
        workflow_id: str,
        *,
        actor_id: str = "local-user",
    ) -> WorkflowRun:
        """讓視覺統籌代理重新校準既有視覺方案，供舊任務或人工要求使用。"""
        workflow = await self.repository.get(workflow_id)
        production = workflow.artifacts.get("production_package")
        if not production or not production.get("preview_ready"):
            raise InvalidWorkflowTransitionError("製作規劃尚未產生，無法執行視覺校準")
        if not self.visual_agents:
            raise InvalidWorkflowTransitionError("目前未設定視覺統籌代理")

        review_id = str(uuid4())
        existing_cues = deepcopy(production.get("visual_cues", []))
        has_human_cues = any(cue.get("source") == "manual" for cue in existing_cues)
        workflow.artifacts["_visual_review_context"] = {
            "mode": "human_revision" if has_human_cues else "recalibration",
            "review_id": review_id,
            "visual_cues": existing_cues,
        }
        try:
            for agent in self.visual_agents:
                result = await agent.execute(workflow)
                workflow.agent_results.append(result)
                workflow.execution_log.append(
                    ExecutionLogEntry(
                        stage=VisibleStage.PRODUCTION_PACKAGE,
                        event_type="visual_review.completed",
                        status=result.status,
                        title="視覺統籌代理完成重新校準",
                        summary=result.summary,
                        agent=result.agent,
                        confidence=None if result.is_simulated else result.confidence,
                        risk_level=result.risk_level,
                        details={
                            "review_id": review_id,
                            "issues": result.issues,
                            "evidence": result.evidence,
                            "human_input_preserved": has_human_cues,
                            "actor_id": actor_id,
                            "artifact_summary": _agent_artifact_summary(result.artifact),
                        },
                    )
                )
            self._apply_latest_visual_review(
                workflow,
                use_suggestions=not has_human_cues,
                review_id=review_id,
            )
            self._apply_latest_youtube_reference_review(workflow)
            self._apply_latest_quality_gates(workflow)
        except GenerationQuotaExceededError:
            raise
        except Exception as exc:
            logger.exception(
                "visual recalibration failed",
                extra={"workflow_id": workflow.id, "review_id": review_id},
            )
            production["visual_review"] = {
                "review_id": review_id,
                "status": "pending_retry",
                "review_mode": "recalibration",
                "human_input_preserved": has_human_cues,
                "issues": ["視覺統籌代理暫時無法完成校準，既有內容未被更動。"],
                "error_type": type(exc).__name__,
            }
            workflow.execution_log.append(
                ExecutionLogEntry(
                    stage=VisibleStage.PRODUCTION_PACKAGE,
                    event_type="visual_review.failed",
                    status="failed",
                    title="視覺統籌代理校準待重試",
                    summary="既有圖像提示與人工素材均已保留，代理校準暫時失敗。",
                    agent="visual_director",
                    risk_level=RiskLevel.MEDIUM,
                    details={"review_id": review_id, "error_type": type(exc).__name__},
                )
            )
        finally:
            workflow.artifacts.pop("_visual_review_context", None)

        workflow.artifacts.pop("publication", None)
        workflow.artifacts.pop("human_decisions", None)
        workflow.stage = VisibleStage.APPROVAL_PUBLISH
        workflow.human_request = None
        self._request_final_approval(workflow)
        workflow.updated_at = utc_now()
        return await self.repository.save(workflow)

    async def generate_image_asset(
        self,
        workflow_id: str,
        payload: ImageGenerationRequest,
        *,
        actor_id: str = "local-user",
    ) -> WorkflowRun:
        workflow = await self.repository.get(workflow_id)
        production = workflow.artifacts.get("production_package")
        if not production or not production.get("preview_ready"):
            raise InvalidWorkflowTransitionError("製作規劃尚未產生，無法生成圖片")
        if self.image_generator is None:
            raise InvalidWorkflowTransitionError("目前未設定 OpenAI 圖片生成服務")
        valid_shots = {
            int(item.get("shot", index + 1))
            for index, item in enumerate(production.get("storyboard", []))
        }
        if payload.shot not in valid_shots:
            raise InvalidWorkflowTransitionError("指定的分鏡不存在")

        request_id = str(uuid4())
        quota_workflow_id = f"{workflow.id}:image:{request_id}"
        if self.usage_repository is not None:
            await self.usage_repository.claim_generation(
                workflow.input.workspace_id,
                actor_id=actor_id,
                workflow_id=quota_workflow_id,
                provider="openai",
                model=self.image_generator.model,
                daily_limit=self.daily_generation_limit,
            )
        try:
            generated = await self.image_generator.generate(
                workspace_id=workflow.input.workspace_id,
                workflow_id=workflow.id,
                prompt=payload.prompt,
                aspect_ratio=payload.aspect_ratio,
            )
        except Exception as exc:
            if self.usage_repository is not None:
                await self.usage_repository.record_event(
                    workflow.input.workspace_id,
                    "generation.failed",
                    actor_id=actor_id,
                    workflow_id=quota_workflow_id,
                    provider="openai",
                    model=self.image_generator.model,
                    metadata={"kind": "image", "error_type": type(exc).__name__},
                )
            raise

        created_at = utc_now()
        asset = {
            "asset_id": generated.asset_id,
            "kind": "image",
            "shot": payload.shot,
            "label": payload.label,
            "prompt": payload.prompt,
            "aspect_ratio": payload.aspect_ratio,
            "model": generated.model,
            "mime_type": generated.mime_type,
            "created_at": created_at.isoformat(),
            "created_by": actor_id,
            "url": f"/api/v1/workflows/{workflow.id}/generated-images/{generated.asset_id}",
        }
        production.setdefault("generated_assets", []).append(asset)
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.PRODUCTION_PACKAGE,
                event_type="media.image_generated",
                status="completed",
                title="AI 圖片素材已生成",
                summary=f"已為分鏡 {payload.shot} 生成「{payload.label}」。",
                agent="visual_director",
                details={"asset_id": generated.asset_id, "shot": payload.shot, "model": generated.model},
            )
        )
        workflow.updated_at = created_at
        if self.usage_repository is not None:
            await self.usage_repository.record_event(
                workflow.input.workspace_id,
                "generation.completed",
                actor_id=actor_id,
                workflow_id=quota_workflow_id,
                provider="openai",
                model=generated.model,
                metadata={"kind": "image", "asset_id": generated.asset_id, "workflow_id": workflow.id},
            )
        return await self.repository.save(workflow)

    async def generated_image_path(self, workflow_id: str, asset_id: str) -> Path:
        workflow = await self.repository.get(workflow_id)
        production = workflow.artifacts.get("production_package", {})
        matches = [
            item
            for item in production.get("generated_assets", [])
            if item.get("kind") == "image" and item.get("asset_id") == asset_id
        ]
        if not matches or self.image_generator is None:
            raise FileNotFoundError("generated image not found")
        path = self.image_generator.resolve_path(
            workspace_id=workflow.input.workspace_id,
            workflow_id=workflow.id,
            asset_id=asset_id,
        )
        if not path.is_file():
            raise FileNotFoundError("generated image not found")
        return path

    async def generated_image_content(self, workflow_id: str, asset_id: str) -> bytes:
        workflow = await self.repository.get(workflow_id)
        production = workflow.artifacts.get("production_package", {})
        if not any(item.get("kind") == "image" and item.get("asset_id") == asset_id for item in production.get("generated_assets", [])) or self.image_generator is None:
            raise FileNotFoundError("generated image not found")
        return await self.image_generator.read(workspace_id=workflow.input.workspace_id,workflow_id=workflow.id,asset_id=asset_id)

    def _apply_latest_visual_review(
        self,
        workflow: WorkflowRun,
        *,
        use_suggestions: bool,
        review_id: str | None = None,
    ) -> None:
        production = workflow.artifacts.get("production_package")
        if not production:
            return
        result = next(
            (
                item
                for item in reversed(workflow.agent_results)
                if item.agent == "visual_director" and item.artifact.get("visual_direction_review")
            ),
            None,
        )
        if result is None:
            return
        review = deepcopy(result.artifact["visual_direction_review"])
        review.update(
            {
                "review_id": review_id or str(uuid4()),
                "status": "reviewed",
                "human_input_preserved": not use_suggestions,
            }
        )
        visual_plan = production.setdefault("visual_plan", {})
        visual_plan["image_prompts"] = review.get("image_prompts", [])
        if review.get("visual_style"):
            visual_plan["style"] = review["visual_style"]
        if review.get("palette"):
            visual_plan["palette"] = review["palette"]
        if review.get("broll_queries"):
            visual_plan["broll_queries"] = review["broll_queries"]
        if review.get("youtube_references"):
            visual_plan["youtube_references"] = review["youtube_references"]
        if review.get("generation_prompts"):
            visual_plan["generation_prompts"] = review["generation_prompts"]
        production["visual_review"] = review
        if not use_suggestions:
            return

        storyboard = production.get("storyboard", [])
        valid_shots = {int(shot.get("shot", index + 1)) for index, shot in enumerate(storyboard)}
        total_seconds = int(
            production.get("estimated_duration_seconds")
            or sum(int(shot.get("duration_seconds") or 0) for shot in storyboard)
        )
        valid_cues = []
        for cue in review.get("suggested_visual_cues", []):
            start = int(cue.get("start_seconds") or 0)
            duration = int(cue.get("duration_seconds") or 0)
            if (
                int(cue.get("shot") or 0) in valid_shots
                and start >= 0
                and duration >= 1
                and start + duration <= total_seconds
            ):
                valid_cues.append({**cue, "source": "ai"})
        if valid_cues:
            production["visual_cues"] = valid_cues
            production["visual_summary"] = _build_visual_summary(valid_cues, total_seconds)

    def _apply_latest_youtube_reference_review(self, workflow: WorkflowRun) -> None:
        production = workflow.artifacts.get("production_package")
        if not production:
            return
        result = next(
            (
                item
                for item in reversed(workflow.agent_results)
                if item.agent == "youtube_reference_verifier"
                and item.artifact.get("youtube_reference_review")
            ),
            None,
        )
        if result is None:
            return
        review = deepcopy(result.artifact["youtube_reference_review"])
        production["youtube_reference_review"] = review
        references = review.get("references", [])
        production.setdefault("visual_plan", {})["youtube_references"] = references

    def _clear_downstream_for_stage(
        self, workflow: WorkflowRun, target_stage: VisibleStage
    ) -> None:
        allowed_artifacts = {
            VisibleStage.REQUIREMENTS: set(),
            VisibleStage.AI_CREATION: {"reference_analysis", "routing_plan"},
            VisibleStage.PRODUCTION_PACKAGE: {
                "reference_analysis",
                "routing_plan",
                "script",
            },
            VisibleStage.APPROVAL_PUBLISH: {
                "reference_analysis",
                "routing_plan",
                "script",
                "production_package",
            },
        }[target_stage]
        workflow.artifacts = {
            key: value
            for key, value in workflow.artifacts.items()
            if key in allowed_artifacts
        }

        creation_agent_names = {
            *(agent.name for agent in self.creation_agents),
            *(agent.name for agent in self.editorial_agents),
        }
        production_agent_names = {
            *(agent.name for agent in self.production_agents),
            *(agent.name for agent in self.visual_agents),
        }
        if target_stage in {VisibleStage.REQUIREMENTS, VisibleStage.AI_CREATION}:
            removed_agents = creation_agent_names | production_agent_names
        elif target_stage == VisibleStage.PRODUCTION_PACKAGE:
            removed_agents = production_agent_names
        else:
            removed_agents = set()
        workflow.agent_results = [
            result for result in workflow.agent_results if result.agent not in removed_agents
        ]
        reset_stages = {
            VisibleStage.REQUIREMENTS: [
                VisibleStage.AI_CREATION.value,
                VisibleStage.PRODUCTION_PACKAGE.value,
            ],
            VisibleStage.AI_CREATION: [
                VisibleStage.AI_CREATION.value,
                VisibleStage.PRODUCTION_PACKAGE.value,
            ],
            VisibleStage.PRODUCTION_PACKAGE: [VisibleStage.PRODUCTION_PACKAGE.value],
            VisibleStage.APPROVAL_PUBLISH: [],
        }[target_stage]
        for stage_key in reset_stages:
            workflow.stage_agent_positions.pop(stage_key, None)
            for checkpoint_key in list(workflow.stage_agent_positions):
                if checkpoint_key.startswith(f"{stage_key}:"):
                    workflow.stage_agent_positions.pop(checkpoint_key, None)

    def _ensure_runnable(self, workflow: WorkflowRun) -> None:
        if workflow.status == WorkflowStatus.WAITING_FOR_HUMAN:
            raise InvalidWorkflowTransitionError("workflow is waiting for a human decision")
        if workflow.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED}:
            workflow.status = WorkflowStatus.RUNNING
            workflow.human_request = None

    async def _advance_current_stage(self, workflow: WorkflowRun) -> None:
        if workflow.stage == VisibleStage.REQUIREMENTS:
            if workflow.input.reference_materials:
                workflow.artifacts["reference_analysis"] = build_reference_analysis(workflow)
                workflow.execution_log.append(
                    ExecutionLogEntry(
                        stage=VisibleStage.REQUIREMENTS,
                        event_type="reference_analysis.completed",
                        status="completed",
                        title="參考資料分析完成",
                        summary=(
                            f"已整理 {len(workflow.input.reference_materials)} 份資料，"
                            "品牌規範與產品事實優先於外部風格參考。"
                        ),
                        agent="reference_analyst",
                        confidence=0.94,
                        details={
                            "reference_count": len(workflow.input.reference_materials),
                            "priority_rule": workflow.artifacts["reference_analysis"]["priority_rule"],
                        },
                    )
                )
            workflow.artifacts["routing_plan"] = {
                "research_mode": "standard",
                "agent_strategy": "risk_based",
                "platforms": workflow.input.platforms,
            }
            workflow.execution_log.append(
                ExecutionLogEntry(
                    stage=VisibleStage.REQUIREMENTS,
                    event_type="routing.completed",
                    status="completed",
                    title="執行路線已決定",
                    summary="採用標準研究深度，依風險動態安排研究、驗證、撰寫與品質檢查。",
                    agent="orchestrator",
                    confidence=0.95,
                    details=workflow.artifacts["routing_plan"],
                )
            )
            workflow.stage = VisibleStage.AI_CREATION
            return

        if workflow.stage == VisibleStage.AI_CREATION:
            should_pause = await self._run_agents(workflow, self.creation_agents)
            if should_pause:
                return
            if "script" not in workflow.artifacts:
                await self._record_agent_started(
                    workflow,
                    "writer",
                    checkpoint_key=f"{VisibleStage.AI_CREATION.value}:writer",
                )
                workflow.artifacts["script"] = await self._build_content(workflow)
            await self._ensure_writer_result(workflow)
            should_pause = await self._run_agents(
                workflow,
                self.editorial_agents,
                checkpoint_key=f"{VisibleStage.AI_CREATION.value}:editorial",
            )
            if should_pause:
                return
            editorial_result = self._apply_editorial_result(workflow)
            workflow.execution_log.append(
                ExecutionLogEntry(
                    stage=VisibleStage.AI_CREATION,
                    event_type="artifact.generated",
                    status="completed",
                    title="腳本成果已建立",
                    summary=(
                        f"完成 {len(workflow.artifacts['script']['sections'])} 個內容單元，"
                        f"共 {workflow.artifacts['script']['word_count']} 字；"
                        + (
                            "已完成 OpenAI 品質審閱與本機結構檢查。"
                            if editorial_result is not None
                            else "已執行本機結構檢查。"
                        )
                    ),
                    agent="editorial_critic",
                    confidence=(editorial_result.confidence if editorial_result else None),
                    details={
                        "artifact": "script",
                        "word_count": workflow.artifacts["script"]["word_count"],
                        "target_word_count": workflow.artifacts["script"]["target_word_count"],
                        "section_count": len(workflow.artifacts["script"]["sections"]),
                    },
                )
            )
            workflow.stage = VisibleStage.PRODUCTION_PACKAGE
            return

        if workflow.stage == VisibleStage.PRODUCTION_PACKAGE:
            should_pause = await self._run_agents(
                workflow,
                self.production_agents,
                checkpoint_key=f"{VisibleStage.PRODUCTION_PACKAGE.value}:planning",
            )
            if should_pause:
                return
            production_result = next(
                (
                    result
                    for result in reversed(workflow.agent_results)
                    if result.agent == "production_planner"
                    and result.artifact.get("production_package")
                ),
                None,
            )
            workflow.artifacts["production_package"] = (
                production_result.artifact["production_package"]
                if production_result is not None
                else build_production_preview(workflow)
            )
            visual_pause = await self._run_agents(
                workflow,
                self.visual_agents,
                checkpoint_key=f"{VisibleStage.PRODUCTION_PACKAGE.value}:visual",
            )
            if visual_pause:
                return
            self._apply_latest_visual_review(workflow, use_suggestions=True)
            self._apply_latest_youtube_reference_review(workflow)
            self._apply_latest_quality_gates(workflow)
            workflow.execution_log.append(
                ExecutionLogEntry(
                    stage=VisibleStage.PRODUCTION_PACKAGE,
                    event_type="artifact.generated",
                    status="completed",
                    title="製作素材包已建立",
                    summary=(
                        production_result.summary
                        if production_result is not None
                        else "分鏡、視覺方向、B-roll 搜尋詞與跨平台發布文案已完成。"
                    ),
                    agent="production_planner",
                    confidence=(production_result.confidence if production_result else None),
                    details={
                        "artifact": "production_package",
                        "shot_count": len(workflow.artifacts["production_package"]["storyboard"]),
                        "platform_count": len(workflow.artifacts["production_package"]["distribution_kit"]),
                        "visual_reviewed": bool(workflow.artifacts["production_package"].get("visual_review")),
                    },
                )
            )
            workflow.stage = VisibleStage.APPROVAL_PUBLISH
            self._request_final_approval(workflow)
            return

        if workflow.stage == VisibleStage.APPROVAL_PUBLISH:
            self._request_final_approval(workflow)

    async def _advance_safely(self, workflow: WorkflowRun) -> None:
        """推進階段並把未預期錯誤轉成可回查、可重試的領域狀態。"""
        try:
            await self._advance_current_stage(workflow)
        except GenerationQuotaExceededError:
            raise
        except Exception as exc:  # provider / artifact adapters are external boundaries
            logger.exception(
                "workflow stage execution failed",
                extra={"workflow_id": workflow.id, "stage": workflow.stage.value},
            )
            self._record_failure(workflow, exc)

    async def _build_content(self, workflow: WorkflowRun) -> dict:
        if self.content_provider is None:
            return build_content_preview(workflow)
        return await self.content_provider.generate(workflow)

    async def _ensure_writer_result(self, workflow: WorkflowRun) -> None:
        """Record content-provider generation as the writer agent's completed work.

        The OpenAI content provider is the writer implementation, but older runs only
        stored its script artifact. Keeping a first-class AgentResult makes the agent
        roster, execution log, usage trail and persisted workflow agree.
        """
        if any(result.agent == "writer" for result in workflow.agent_results):
            return
        script = workflow.artifacts.get("script")
        if not script:
            return
        generation = script.get("generation", {})
        provider = str(
            generation.get("provider")
            or getattr(self.content_provider, "provider", "local_rule")
        )
        model = str(
            generation.get("model")
            or getattr(self.content_provider, "model", "deterministic-v1")
        )
        target_adherence = bool(script.get("quality_checks", {}).get("target_adherence"))
        external = bool(generation.get("external", provider != "local_rule"))
        confidence = 0.92 if target_adherence else 0.84
        result = AgentResult(
            agent="writer",
            confidence=confidence,
            risk_level=RiskLevel.LOW if target_adherence else RiskLevel.MEDIUM,
            summary=(
                f"已完成「{script.get('title', workflow.input.topic)}」完整腳本，"
                f"共 {script.get('word_count', 0)} 字。"
            ),
            issues=([] if target_adherence else ["腳本字數與目標仍有差距，交由品質主編調整。"]),
            evidence=["structured_content_generation"],
            artifact={
                "title": script.get("title"),
                "word_count": script.get("word_count", 0),
                "target_word_count": script.get("target_word_count", workflow.input.target_word_count),
                "provider": provider,
                "model": model,
            },
            confidence_basis="content_provider_structured_output_v1",
            is_simulated=not external,
        )
        workflow.agent_results.append(result)
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.AI_CREATION,
                event_type="agent.completed",
                status=result.status,
                title="撰寫代理完成",
                summary=result.summary,
                agent="writer",
                confidence=None if result.is_simulated else result.confidence,
                risk_level=result.risk_level,
                details={
                    "artifact_summary": _agent_artifact_summary(result.artifact),
                    "confidence_basis": result.confidence_basis,
                    "is_simulated": result.is_simulated,
                },
            )
        )
        workflow.updated_at = utc_now()
        await self.repository.save(workflow)

    def _apply_editorial_result(self, workflow: WorkflowRun) -> AgentResult | None:
        result = next(
            (
                item
                for item in reversed(workflow.agent_results)
                if item.agent == "editorial_critic" and item.artifact.get("revised_sections")
            ),
            None,
        )
        if result is None:
            return None
        script = workflow.artifacts.get("script", {})
        sections = result.artifact["revised_sections"]
        full_text = "\n\n".join(section["voiceover"] for section in sections)
        word_count = len("".join(full_text.split()))
        target = workflow.input.target_word_count
        script.update(
            {
                "sections": sections,
                "full_text": full_text,
                "word_count": word_count,
                "word_count_difference": word_count - target,
                "quality_score": result.artifact.get("score"),
                "quality_status": "openai_editorial_reviewed",
                "quality_checks": {
                    "target_adherence": 0.9 <= (word_count / target) <= 1.1,
                    "structure_present": len(sections) >= 3,
                    "basis": "openai_editorial_structured_output",
                },
                "review_notes": [
                    *script.get("review_notes", []),
                    *result.artifact.get("review_notes", []),
                ],
                "editorial_review": {
                    "provider": "openai",
                    "confidence": result.confidence,
                    "risk_level": result.risk_level.value,
                    "issues": result.issues,
                    **result.artifact.get("_runtime", {}),
                },
            }
        )
        return result

    def _record_failure(
        self,
        workflow: WorkflowRun,
        error: Exception,
        *,
        agent: str | None = None,
    ) -> None:
        """保存安全錯誤摘要；完整例外只留在伺服器 log，不回傳或寫入 snapshot。"""
        workflow.status = WorkflowStatus.FAILED
        workflow.human_request = None
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=workflow.stage,
                event_type="agent.failed" if agent else "workflow.failed",
                status="failed",
                title=(
                    f"{_agent_label(agent)}執行失敗"
                    if agent
                    else "工作流執行失敗"
                ),
                summary="任務已安全停止；請確認服務設定或稍後重試。",
                agent=agent or "orchestrator",
                details={
                    "error_type": type(error).__name__,
                    "retryable": True,
                },
            )
        )

    def _request_final_approval(self, workflow: WorkflowRun) -> None:
        workflow.status = WorkflowStatus.WAITING_FOR_HUMAN
        workflow.human_request = HumanRequest(
            reason="final_approval",
            question="內容與製作素材包已完成，是否核准進入發布？",
            suggested_action="預覽腳本、來源、視覺與發布素材後核准。",
        )
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=VisibleStage.APPROVAL_PUBLISH,
                event_type="human.requested",
                status="waiting",
                title="等待最終人工核准",
                summary="完整成果預覽已就緒，發布前需要內容負責人確認。",
                details={"reason": "final_approval"},
            )
        )

    def _apply_latest_quality_gates(self, workflow: WorkflowRun) -> None:
        """Expose independent QA results beside the production package for UI review."""
        production = workflow.artifacts.get("production_package")
        if not production:
            return
        production_quality = next(
            (
                result.artifact.get("production_quality_review")
                for result in reversed(workflow.agent_results)
                if result.agent == "production_quality"
                and result.artifact.get("production_quality_review")
            ),
            None,
        )
        publishing_preflight = next(
            (
                result.artifact.get("publishing_preflight_review")
                for result in reversed(workflow.agent_results)
                if result.agent == "publishing_preflight"
                and result.artifact.get("publishing_preflight_review")
            ),
            None,
        )
        if production_quality is not None:
            production["production_quality_review"] = deepcopy(production_quality)
        if publishing_preflight is not None:
            production["publishing_preflight_review"] = deepcopy(publishing_preflight)

    async def decide(
        self,
        workflow_id: str,
        decision: HumanDecision,
        *,
        actor_id: str = "local-user",
    ) -> WorkflowRun:
        workflow = await self.repository.get(workflow_id)
        if workflow.status != WorkflowStatus.WAITING_FOR_HUMAN or workflow.human_request is None:
            raise InvalidWorkflowTransitionError("workflow is not waiting for a human decision")

        reason = workflow.human_request.reason
        if reason == "final_approval":
            script = workflow.artifacts.get("script", {})
            production = workflow.artifacts.get("production_package", {})
            if not script.get("sections") or not production.get("preview_ready"):
                raise InvalidWorkflowTransitionError(
                    "完整成果預覽尚未產生，不可進行最終核准"
                )
        workflow.artifacts.setdefault("human_decisions", []).append(
            {
                "reason": reason,
                "approved": decision.approved,
                "action": decision.action.value,
                "note": decision.note,
                "stage": workflow.stage.value,
                "actor_id": actor_id,
            }
        )
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=workflow.stage,
                event_type="human.decided",
                status=decision.action.value,
                title="人工決策已記錄",
                summary=decision.note or ("已核准繼續" if decision.approved else "已退回任務"),
                details={
                    "reason": reason,
                    "approved": decision.approved,
                    "action": decision.action.value,
                    "actor_id": actor_id,
                },
            )
        )
        workflow.human_request = None

        if decision.action == DecisionAction.REQUEST_CHANGES:
            workflow.revision_history.append(
                {
                    "version": len(workflow.revision_history) + 1,
                    "created_at": utc_now().isoformat(),
                    "feedback": decision.note,
                    "script": workflow.artifacts.get("script"),
                    "production_package": workflow.artifacts.get("production_package"),
                }
            )
            workflow.last_review_feedback = decision.note
            self._clear_downstream_for_stage(workflow, VisibleStage.AI_CREATION)
            workflow.stage = VisibleStage.AI_CREATION
            workflow.status = WorkflowStatus.DRAFT
        elif not decision.approved:
            workflow.status = WorkflowStatus.CANCELLED
        elif reason == "final_approval":
            workflow.status = WorkflowStatus.COMPLETED
            workflow.artifacts.setdefault(
                "publication",
                {
                    "status": PublicationStatus.READY.value,
                    "scheduled_at": None,
                    "note": "內容已核准，等待安排發布",
                    "updated_at": utc_now().isoformat(),
                    "published_url": "",
                    "target_platform": "",
                    "published_at": None,
                    "mode": "semi_automatic",
                },
            )
        else:
            workflow.status = WorkflowStatus.RUNNING
            try:
                # Gate 前已完成的代理以 stage_agent_positions checkpoint 記錄；
                # 核准後從下一個代理繼續，不能略過 Writer 或品質代理。
                await self._advance_current_stage(workflow)
            except GenerationQuotaExceededError:
                raise
            except Exception as exc:
                failed_agent = (
                    "editorial_critic"
                    if workflow.stage == VisibleStage.AI_CREATION
                    else "production_planner"
                )
                logger.exception(
                    "approved workflow artifact generation failed",
                    extra={
                        "workflow_id": workflow.id,
                        "stage": workflow.stage.value,
                        "agent": failed_agent,
                    },
                )
                self._record_failure(workflow, exc, agent=failed_agent)

        workflow.updated_at = utc_now()
        return await self.repository.save(workflow)

    async def _run_agents(
        self,
        workflow: WorkflowRun,
        agents: list[WorkflowAgent],
        *,
        checkpoint_key: str | None = None,
    ) -> bool:
        stage_key = checkpoint_key or workflow.stage.value
        start_index = workflow.stage_agent_positions.get(stage_key, 0)
        for index, agent in enumerate(agents[start_index:], start=start_index):
            await self._record_agent_started(
                workflow,
                agent.name,
                checkpoint_key=stage_key,
                position=index,
            )
            try:
                result = await agent.execute(workflow)
            except Exception as exc:  # provider implementations may fail independently
                logger.exception(
                    "workflow agent execution failed",
                    extra={
                        "workflow_id": workflow.id,
                        "stage": workflow.stage.value,
                        "agent": agent.name,
                    },
                )
                self._record_failure(workflow, exc, agent=agent.name)
                return True
            workflow.agent_results.append(result)
            workflow.stage_agent_positions[stage_key] = index + 1
            risk_reason, risk_guidance = _agent_risk_explanation(result)
            workflow.execution_log.append(
                ExecutionLogEntry(
                    stage=workflow.stage,
                    event_type="agent.completed",
                    status=result.status,
                    title=f"{_agent_label(result.agent)}完成",
                    summary=result.summary,
                    agent=result.agent,
                    confidence=None if result.is_simulated else result.confidence,
                    risk_level=result.risk_level,
                    details={
                        "issues": result.issues,
                        "evidence": result.evidence,
                        "artifact_summary": _agent_artifact_summary(result.artifact),
                        "confidence_basis": result.confidence_basis,
                        "is_simulated": result.is_simulated,
                        "risk_reason": risk_reason,
                        "risk_guidance": risk_guidance,
                    },
                )
            )
            workflow.updated_at = utc_now()
            await self.repository.save(workflow)
            human_request = evaluate_agent_result(result)
            if human_request is not None:
                workflow.status = WorkflowStatus.WAITING_FOR_HUMAN
                workflow.human_request = human_request
                workflow.execution_log.append(
                    ExecutionLogEntry(
                        stage=workflow.stage,
                        event_type="human.requested",
                        status="waiting",
                        title="代理偵測到需要人工判斷的例外",
                        summary=human_request.question,
                        agent=result.agent,
                        confidence=None if result.is_simulated else result.confidence,
                        risk_level=result.risk_level,
                        details={
                            "reason": human_request.reason,
                            "issues": result.issues,
                            "confidence_basis": result.confidence_basis,
                            "is_simulated": result.is_simulated,
                            "risk_reason": risk_reason,
                            "risk_guidance": risk_guidance,
                        },
                    )
                )
                return True
        return False

    async def _record_agent_started(
        self,
        workflow: WorkflowRun,
        agent_name: str,
        *,
        checkpoint_key: str,
        position: int | None = None,
    ) -> None:
        """Persist a truthful progress checkpoint before a slow provider call.

        The request that advances a workflow can remain open for several minutes.
        Saving this event first lets a separate GET request report which agent is
        actually running, and survives a browser refresh or reconnect.
        """
        attempt = 1 + sum(
            entry.event_type == "agent.started" and entry.agent == agent_name
            for entry in workflow.execution_log
        )
        workflow.status = WorkflowStatus.RUNNING
        workflow.execution_log.append(
            ExecutionLogEntry(
                stage=workflow.stage,
                event_type="agent.started",
                status="running",
                title=f"{_agent_label(agent_name)}執行中",
                summary="已送出代理工作，正在等待模型回傳並驗證結構化結果。",
                agent=agent_name,
                details={
                    "attempt": attempt,
                    "checkpoint": checkpoint_key,
                    **({"position": position + 1} if position is not None else {}),
                },
            )
        )
        workflow.updated_at = utc_now()
        await self.repository.save(workflow)


def _redistribute_script_sections(full_text: str, existing_sections: list[dict]) -> list[dict]:
    """將人工修訂的全文同步回分段腳本，保留既有段落名稱與畫面提示。"""
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", full_text) if item.strip()]
    existing = existing_sections or []
    desired_count = max(1, len(existing))

    if len(paragraphs) == desired_count:
        chunks = paragraphs
    else:
        sentences = [
            item.strip()
            for item in re.split(r"(?<=[。！？!?])\s*|\n+", full_text)
            if item.strip()
        ] or [full_text]
        section_count = min(desired_count, len(sentences))
        chunks = []
        for index in range(section_count):
            start = round(index * len(sentences) / section_count)
            end = round((index + 1) * len(sentences) / section_count)
            chunks.append("".join(sentences[start:end]).strip())

    sections: list[dict] = []
    for index, voiceover in enumerate(chunks):
        prior = existing[index] if index < len(existing) else {}
        sections.append(
            {
                **prior,
                "id": prior.get("id") or f"section-{index + 1}",
                "label": prior.get("label") or f"段落 {index + 1}",
                "voiceover": voiceover,
                "visual_direction": prior.get("visual_direction") or "依新版逐字稿確認畫面安排。",
            }
        )
    return sections


def _interval_union_seconds(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _build_visual_summary(cues: list[dict], total_seconds: int) -> dict[str, int]:
    replacement_types = {"broll", "product_shot", "screen_recording", "image"}
    visual_intervals = [
        (int(cue["start_seconds"]), int(cue["start_seconds"]) + int(cue["duration_seconds"]))
        for cue in cues
        if cue.get("cue_type") in replacement_types
    ]
    broll_intervals = [
        (int(cue["start_seconds"]), int(cue["start_seconds"]) + int(cue["duration_seconds"]))
        for cue in cues
        if cue.get("cue_type") == "broll"
    ]
    visual_seconds = min(total_seconds, _interval_union_seconds(visual_intervals))
    broll_seconds = min(total_seconds, _interval_union_seconds(broll_intervals))
    return {
        "total_seconds": total_seconds,
        "visual_seconds": visual_seconds,
        "visual_percent": round(visual_seconds / total_seconds * 100),
        "broll_seconds": broll_seconds,
        "broll_percent": round(broll_seconds / total_seconds * 100),
        "text_overlay_count": sum(1 for cue in cues if cue.get("cue_type") == "text_overlay"),
        "subtitle_count": sum(1 for cue in cues if cue.get("cue_type") == "subtitle"),
        "title_card_count": sum(1 for cue in cues if cue.get("cue_type") == "title_card"),
    }


def _agent_artifact_summary(artifact: dict) -> dict:
    """Keep execution logs readable while the complete artifact remains in agent_results."""
    summary = {
        key: value
        for key, value in artifact.items()
        if key not in {"_runtime", "revised_sections", "production_package", "summary"}
    }
    if artifact.get("summary"):
        summary["summary_excerpt"] = str(artifact["summary"])[:500]
    if artifact.get("revised_sections"):
        summary["revised_section_count"] = len(artifact["revised_sections"])
    if artifact.get("production_package"):
        package = artifact["production_package"]
        summary["production_package"] = {
            "storyboard_count": len(package.get("storyboard", [])),
            "platform_count": len(package.get("distribution_kit", [])),
            "preview_ready": package.get("preview_ready", False),
        }
    runtime = artifact.get("_runtime", {})
    if runtime:
        summary["provider"] = runtime.get("provider")
        summary["model"] = runtime.get("model")
        summary["response_id"] = runtime.get("response_id")
        summary["usage"] = runtime.get("usage", {})
        summary["prompt_version"] = runtime.get("prompt_version")
    return summary


def _agent_risk_explanation(result: AgentResult) -> tuple[str, str]:
    """Build an auditable explanation from agent output, without asking another model to guess."""
    issues = [str(issue).strip() for issue in result.issues if str(issue).strip()]
    if issues:
        reason = "；".join(issues[:3])
    elif result.risk_level == RiskLevel.HIGH:
        reason = "此階段未通過必要的安全或品質條件，需要人工確認後才能繼續。"
    elif result.risk_level == RiskLevel.MEDIUM:
        if result.is_simulated or "fallback" in result.confidence_basis.lower():
            reason = "此階段使用本機規則或備援結果，缺少完整的模型查核依據。"
        elif result.confidence < 0.85:
            reason = f"代理信心為 {round(result.confidence * 100)}%，低於自動繼續門檻 85%。"
        else:
            reason = "偵測到需要留意的非阻擋性問題，建議在進入下一階段前複核。"
    elif result.agent == "research" and len(result.evidence) >= 2:
        reason = "已取得至少 2 項研究依據，且未偵測到需要升級處理的問題。"
    elif result.agent == "verification":
        reason = "事實與風險檢查已通過，未發現需要升級處理的問題。"
    else:
        reason = "必要檢查已通過，未發現阻擋流程的問題。"

    guidance = {
        RiskLevel.HIGH: "請先檢查來源、關鍵主張與發布邊界，完成人工判斷後再繼續。",
        RiskLevel.MEDIUM: "建議查看問題與依據；必要時補充資料、修改內容或重新執行此階段。",
        RiskLevel.LOW: "可以繼續流程；正式發布前仍需完成最終人工核准。",
    }[result.risk_level]
    return reason, guidance


def _agent_label(agent: str) -> str:
    return {
        "orchestrator": "流程協調代理",
        "reference_analyst": "參考分析代理",
        "research": "研究代理",
        "verification": "驗證代理",
        "writer": "撰寫代理",
        "editorial_critic": "品質主編",
        "production_planner": "製作規劃代理",
        "visual_director": "視覺統籌代理",
        "youtube_reference_verifier": "影片參考驗證代理",
        "production_quality": "製作包 QA 代理",
        "publishing_preflight": "發布前 Preflight",
    }.get(agent, agent)


def _stage_label(stage: VisibleStage) -> str:
    return {
        VisibleStage.REQUIREMENTS: "需求設定",
        VisibleStage.AI_CREATION: "AI 研究與創作",
        VisibleStage.PRODUCTION_PACKAGE: "製作素材包",
        VisibleStage.APPROVAL_PUBLISH: "核准與發布",
    }[stage]


def _publication_title(status: PublicationStatus) -> str:
    return {
        PublicationStatus.READY: "內容已設為可發布",
        PublicationStatus.RETURNED: "內容已退回修改",
        PublicationStatus.SCHEDULED: "發布排程已更新",
        PublicationStatus.PUBLISHING: "已開始人工發布",
        PublicationStatus.PUBLISHED: "內容已標記為發布",
        PublicationStatus.FAILED: "人工發布失敗",
    }[status]


def _publication_summary(publication: PublicationManagementRequest) -> str:
    if publication.status == PublicationStatus.SCHEDULED:
        return f"已建立本機發布排程：{publication.scheduled_at.isoformat()}。"
    if publication.status == PublicationStatus.PUBLISHED:
        return "已記錄人工發布結果；尚未呼叫外部平台 API。"
    if publication.status == PublicationStatus.PUBLISHING:
        return "已前往外部平台進行人工發布，等待回填公開網址。"
    if publication.status == PublicationStatus.FAILED:
        return "人工發布未完成；請依備註修正後重試。"
    if publication.status == PublicationStatus.RETURNED:
        return "內容已從發布佇列退回修改；修正完成後可重新設為可發布。"
    return "內容已回到可發布清單，等待排程或外部平台處理。"
