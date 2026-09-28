import asyncio

import pytest

import app.workflow.orchestrator as orchestrator_module
from app.agents.deterministic import DeterministicAgent
from app.domain.models import AgentResult, HumanDecision, ReferenceMaterial, VisualCue, VisibleStage, WorkflowInput, WorkflowStatus
from app.workflow.orchestrator import InvalidWorkflowTransitionError, WorkflowOrchestrator
from app.workflow.repository import InMemoryWorkflowRepository


class FailingAgent:
    name = "writer"

    async def execute(self, workflow):
        raise RuntimeError("provider-secret-that-must-not-be-persisted")


class StaticVisualDirector:
    name = "visual_director"

    async def execute(self, workflow):
        context = workflow.artifacts.get("_visual_review_context", {})
        return AgentResult(
            agent=self.name,
            confidence=0.94,
            summary="視覺與逐字稿已完成對齊審查。",
            evidence=["full_script_visual_alignment", "human_visual_priority"],
            artifact={
                "visual_direction_review": {
                    "confidence": 0.94,
                    "risk_level": "low",
                    "summary": "視覺與逐字稿已完成對齊審查。",
                    "issues": [],
                    "alignment_notes": ["人工素材維持原值。"],
                    "image_prompts": ["依完整逐字稿拍攝產品鏡片特寫"],
                    "suggested_visual_cues": [
                        {
                            "id": "agent-suggestion",
                            "shot": 1,
                            "cue_type": "image",
                            "label": "代理建議，不可覆寫人工內容",
                            "start_seconds": 0,
                            "duration_seconds": 2,
                            "source": "ai",
                        }
                    ],
                    "review_mode": context.get("mode", "initial_plan"),
                }
            },
        )


def make_orchestrator() -> WorkflowOrchestrator:
    return WorkflowOrchestrator(
        repository=InMemoryWorkflowRepository(),
        creation_agents=[
            DeterministicAgent("verification", "驗證"),
            DeterministicAgent("writer", "撰寫"),
        ],
        production_agents=[DeterministicAgent("planner", "製作規劃")],
    )


def make_input(topic: str = "AI 內容工作流") -> WorkflowInput:
    return WorkflowInput(
        topic=topic,
        goal="education",
        platforms=["youtube"],
        output_type="short_video",
        target_word_count=500,
        brand_voice="專業但自然",
    )


@pytest.mark.asyncio
async def test_running_agent_checkpoint_is_readable_during_slow_execution() -> None:
    repository = InMemoryWorkflowRepository()
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingResearchAgent:
        name = "research"

        async def execute(self, workflow):
            entered.set()
            await release.wait()
            return AgentResult(agent=self.name, confidence=0.9, summary="研究完成")

    orchestrator = WorkflowOrchestrator(
        repository=repository,
        creation_agents=[BlockingResearchAgent()],
        production_agents=[],
    )
    created = await orchestrator.create(make_input())
    ready = await orchestrator.advance(created.id)
    assert ready.stage == VisibleStage.AI_CREATION

    running_task = asyncio.create_task(orchestrator.advance(created.id))
    await asyncio.wait_for(entered.wait(), timeout=1)
    persisted = await repository.get(created.id)

    assert persisted.status == WorkflowStatus.RUNNING
    assert persisted.execution_log[-1].event_type == "agent.started"
    assert persisted.execution_log[-1].status == "running"
    assert persisted.execution_log[-1].agent == "research"

    release.set()
    await running_task


@pytest.mark.asyncio
async def test_low_risk_workflow_only_stops_at_final_approval() -> None:
    orchestrator = make_orchestrator()
    workflow = await orchestrator.create(make_input())

    result = await orchestrator.run_until_gate(workflow.id)

    assert result.stage == VisibleStage.APPROVAL_PUBLISH
    assert result.status == WorkflowStatus.WAITING_FOR_HUMAN
    assert result.human_request is not None
    assert result.human_request.reason == "final_approval"
    assert len(result.artifacts["script"]["sections"]) == 5
    assert result.artifacts["script"]["full_text"]
    assert result.artifacts["script"]["target_word_count"] == 500
    assert result.artifacts["script"]["word_count"] >= 450
    assert len(result.artifacts["production_package"]["storyboard"]) == 5
    assert all(
        shot["broll_queries"]
        for shot in result.artifacts["production_package"]["storyboard"]
    )
    assert result.artifacts["production_package"]["distribution_kit"][0]["label"] == "YouTube"
    assert result.artifacts["production_package"]["preview_ready"] is True
    assert len(result.execution_log) >= 8
    assert any(entry.event_type == "agent.completed" for entry in result.execution_log)
    completed_entries = [entry for entry in result.execution_log if entry.event_type == "agent.completed"]
    assert all(entry.details["risk_reason"] for entry in completed_entries)
    assert all(entry.details["risk_guidance"] for entry in completed_entries)
    assert result.execution_log[-1].event_type == "human.requested"

    completed = await orchestrator.decide(
        workflow.id, HumanDecision(approved=True, note="核准")
    )
    assert completed.status == WorkflowStatus.COMPLETED


@pytest.mark.asyncio
async def test_high_risk_topic_escalates_during_creation() -> None:
    orchestrator = make_orchestrator()
    workflow = await orchestrator.create(make_input("AI 投資建議"))

    result = await orchestrator.run_until_gate(workflow.id)

    assert result.stage == VisibleStage.AI_CREATION
    assert result.status == WorkflowStatus.WAITING_FOR_HUMAN
    assert result.human_request is not None
    assert result.human_request.reason == "high_risk"
    high_risk_entry = next(
        entry for entry in result.execution_log
        if entry.event_type == "agent.completed" and entry.risk_level == "high"
    )
    assert "高風險領域" in high_risk_entry.details["risk_reason"]
    assert "人工判斷" in high_risk_entry.details["risk_guidance"]

    resumed = await orchestrator.decide(
        workflow.id,
        HumanDecision(approved=True, note="已確認風險來源，繼續"),
    )

    assert resumed.stage == VisibleStage.PRODUCTION_PACKAGE
    assert resumed.artifacts["script"]["full_text"]
    assert [item.agent for item in resumed.agent_results] == ["verification", "writer"]
    assert resumed.stage_agent_positions[VisibleStage.AI_CREATION.value] == 2


@pytest.mark.asyncio
async def test_final_review_can_request_changes_and_preserve_version() -> None:
    orchestrator = make_orchestrator()
    workflow = await orchestrator.create(make_input("需要修改的腳本"))
    waiting = await orchestrator.run_until_gate(workflow.id)
    previous_text = waiting.artifacts["script"]["full_text"]

    revised = await orchestrator.decide(
        workflow.id,
        HumanDecision(
            approved=False,
            action="request_changes",
            note="第二段改成更具體的案例，並降低宣傳語氣。",
        ),
    )

    assert revised.status == WorkflowStatus.DRAFT
    assert revised.stage == VisibleStage.AI_CREATION
    assert revised.last_review_feedback.startswith("第二段")
    assert len(revised.revision_history) == 1
    assert revised.revision_history[0]["script"]["full_text"] == previous_text
    assert "script" not in revised.artifacts


@pytest.mark.asyncio
async def test_human_can_edit_transcript_and_keep_existing_production_plan() -> None:
    orchestrator = make_orchestrator()
    workflow = await orchestrator.create(make_input("人工修改逐字稿"))
    waiting = await orchestrator.run_until_gate(workflow.id)
    original_storyboard = waiting.artifacts["production_package"]["storyboard"]
    revised_text = waiting.artifacts["script"]["full_text"] + "\n\n這是人工補上的重要結論。"

    revised = await orchestrator.revise_script(
        workflow.id,
        revised_text,
        regenerate_production=False,
        actor_id="editor-1",
    )

    assert revised.status == WorkflowStatus.WAITING_FOR_HUMAN
    assert revised.human_request is not None
    assert revised.human_request.reason == "final_approval"
    assert revised.artifacts["script"]["full_text"] == revised_text
    assert revised.artifacts["script"]["quality_status"] == "human_edited"
    assert revised.artifacts["production_package"]["storyboard"] == original_storyboard
    assert revised.artifacts["production_package"]["script_sync_status"] == "kept_after_manual_edit"
    assert revised.revision_history[-1]["actor_id"] == "editor-1"


@pytest.mark.asyncio
async def test_human_can_edit_transcript_and_regenerate_production_plan() -> None:
    orchestrator = make_orchestrator()
    workflow = await orchestrator.create(make_input("重做分鏡"))
    waiting = await orchestrator.run_until_gate(workflow.id)
    revised_text = "新版開場需要產品特寫。\n\n人工新增的操作示範與結論。"

    revised = await orchestrator.revise_script(
        workflow.id,
        revised_text,
        regenerate_production=True,
        actor_id="editor-2",
    )

    assert revised.status == WorkflowStatus.WAITING_FOR_HUMAN
    assert revised.stage == VisibleStage.APPROVAL_PUBLISH
    assert revised.artifacts["production_package"]["preview_ready"] is True
    assert revised.artifacts["production_package"]["script_sync_status"] == "regenerated_from_human_revision"
    assert any(
        "人工新增" in shot["voiceover"]
        for shot in revised.artifacts["production_package"]["storyboard"]
    )


@pytest.mark.asyncio
async def test_human_can_edit_visual_timeline_and_require_fresh_approval() -> None:
    orchestrator = make_orchestrator()
    workflow = await orchestrator.create(make_input("人工調整 B-roll"))
    waiting = await orchestrator.run_until_gate(workflow.id)
    shot = waiting.artifacts["production_package"]["storyboard"][0]

    revised = await orchestrator.revise_production_plan(
        workflow.id,
        [
            VisualCue(
                id="manual-broll-1",
                shot=shot["shot"],
                cue_type="broll",
                label="產品外觀運鏡",
                start_seconds=0,
                duration_seconds=4,
                visual_description="從鏡頭正面緩慢移動到側面。",
                purpose="建立產品印象",
                search_query="產品 360 度運鏡",
            ),
            VisualCue(
                id="manual-text-1",
                shot=shot["shot"],
                cue_type="text_overlay",
                label="重點文字卡",
                start_seconds=1,
                duration_seconds=2,
            ),
            VisualCue(
                id="manual-subtitle-1",
                shot=shot["shot"],
                cue_type="subtitle",
                label="口播字幕",
                start_seconds=0,
                duration_seconds=3,
                visual_description="這是一段人工調整字幕",
            ),
            VisualCue(
                id="manual-title-card-1",
                shot=shot["shot"],
                cue_type="title_card",
                label="測試結論圖卡",
                start_seconds=1,
                duration_seconds=2,
            ),
        ],
        actor_id="editor-visual",
    )

    package = revised.artifacts["production_package"]
    assert revised.status == WorkflowStatus.WAITING_FOR_HUMAN
    assert revised.human_request is not None
    assert revised.human_request.reason == "final_approval"
    assert package["script_sync_status"] == "human_visual_revision"
    assert package["visual_summary"]["visual_seconds"] == 4
    assert package["visual_summary"]["broll_seconds"] == 4
    assert package["visual_summary"]["text_overlay_count"] == 1
    assert package["visual_summary"]["subtitle_count"] == 1
    assert package["visual_summary"]["title_card_count"] == 1
    assert package["manual_revision"]["actor_id"] == "editor-visual"
    assert revised.revision_history[-1]["production_package"]["storyboard"]
    assert revised.execution_log[-2].event_type == "production.human_revised"


@pytest.mark.asyncio
async def test_visual_timeline_rejects_out_of_range_cue() -> None:
    orchestrator = make_orchestrator()
    workflow = await orchestrator.create(make_input("錯誤時間軸"))
    waiting = await orchestrator.run_until_gate(workflow.id)
    package = waiting.artifacts["production_package"]

    with pytest.raises(InvalidWorkflowTransitionError, match="素材段超出影片總長"):
        await orchestrator.revise_production_plan(
            workflow.id,
            [
                VisualCue(
                    shot=1,
                    cue_type="image",
                    label="超出範圍的圖片",
                    start_seconds=package["estimated_duration_seconds"],
                    duration_seconds=3,
                )
            ],
        )


@pytest.mark.asyncio
async def test_visual_director_reviews_but_never_overwrites_human_cues() -> None:
    orchestrator = WorkflowOrchestrator(
        repository=InMemoryWorkflowRepository(),
        creation_agents=[DeterministicAgent("writer", "撰寫")],
        production_agents=[DeterministicAgent("production_planner", "製作規劃")],
        visual_agents=[StaticVisualDirector()],
    )
    workflow = await orchestrator.create(make_input("人工素材優先"))
    waiting = await orchestrator.run_until_gate(workflow.id)
    human_cue = VisualCue(
        id="human-cue-1",
        shot=1,
        cue_type="product_shot",
        label="人工指定鏡片特寫",
        start_seconds=0,
        duration_seconds=4,
        visual_description="保留這段人工描述",
        source="manual",
    )

    revised = await orchestrator.revise_production_plan(
        waiting.id,
        [human_cue],
        actor_id="human-editor",
    )

    package = revised.artifacts["production_package"]
    assert package["visual_cues"] == [human_cue.model_dump(mode="json")]
    assert package["visual_review"]["human_input_preserved"] is True
    assert package["visual_review"]["review_mode"] == "human_revision"
    assert any(entry.event_type == "visual_review.completed" for entry in revised.execution_log)
    assert revised.agent_results[-1].agent == "visual_director"


@pytest.mark.asyncio
async def test_reference_materials_are_analyzed_and_visible_in_preview() -> None:
    orchestrator = make_orchestrator()
    workflow_input = make_input("新品介紹")
    workflow_input.reference_materials = [
        ReferenceMaterial(
            name="品牌 Brief",
            kind="brand_brief",
            content="品牌語氣務實清楚，不使用誇大承諾。",
            focus="品牌方向",
        ),
        ReferenceMaterial(
            name="創作者逐字稿",
            kind="creator_reference",
            content="你有遇過這個問題嗎？先看原因，再看解法。現在就試試看。",
            focus="開場與 CTA",
        ),
    ]
    workflow_input.reference_boundary = "只參考節奏，不複製原句。"

    workflow = await orchestrator.create(workflow_input)
    result = await orchestrator.run_until_gate(workflow.id)

    analysis = result.artifacts["reference_analysis"]
    assert analysis["source_count"] == 2
    assert analysis["sources"][0]["kind"] == "brand_brief"
    assert analysis["style_signals"]
    assert result.artifacts["script"]["reference_analysis"]["reference_boundary"]
    assert any(entry.event_type == "reference_analysis.completed" for entry in result.execution_log)


@pytest.mark.asyncio
async def test_agent_exception_is_persisted_as_safe_failed_state() -> None:
    repository = InMemoryWorkflowRepository()
    orchestrator = WorkflowOrchestrator(
        repository=repository,
        creation_agents=[FailingAgent()],
        production_agents=[],
    )
    workflow = await orchestrator.create(make_input("失敗恢復測試"))

    result = await orchestrator.run_until_gate(workflow.id)
    persisted = await repository.get(workflow.id)

    assert result.status == WorkflowStatus.FAILED
    assert persisted.status == WorkflowStatus.FAILED
    failure = persisted.execution_log[-1]
    assert failure.event_type == "agent.failed"
    assert failure.agent == "writer"
    assert failure.details == {"error_type": "RuntimeError", "retryable": True}
    assert "provider-secret" not in persisted.model_dump_json()


@pytest.mark.asyncio
async def test_artifact_failure_after_human_approval_is_saved_and_retryable(
    monkeypatch,
) -> None:
    repository = InMemoryWorkflowRepository()
    orchestrator = WorkflowOrchestrator(
        repository=repository,
        creation_agents=[DeterministicAgent("verification", "驗證")],
        production_agents=[],
    )
    workflow = await orchestrator.create(make_input("AI 投資建議"))
    waiting = await orchestrator.run_until_gate(workflow.id)
    assert waiting.status == WorkflowStatus.WAITING_FOR_HUMAN
    assert waiting.stage == VisibleStage.AI_CREATION

    real_builder = orchestrator_module.build_content_preview
    calls = 0

    def flaky_builder(current_workflow):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private-provider-error")
        return real_builder(current_workflow)

    monkeypatch.setattr(orchestrator_module, "build_content_preview", flaky_builder)
    failed = await orchestrator.decide(
        workflow.id,
        HumanDecision(approved=True, note="已承擔風險，繼續產製"),
    )

    assert failed.status == WorkflowStatus.FAILED
    assert failed.stage == VisibleStage.AI_CREATION
    assert failed.execution_log[-1].event_type == "agent.failed"
    assert "private-provider-error" not in failed.model_dump_json()
    persisted = await repository.get(workflow.id)
    assert persisted.status == WorkflowStatus.FAILED

    retried = await orchestrator.advance(workflow.id)

    assert retried.status == WorkflowStatus.RUNNING
    assert retried.stage == VisibleStage.PRODUCTION_PACKAGE
    assert retried.artifacts["script"]["full_text"]
    assert calls == 2
    # The approved exception is not raised a second time while retrying the artifact.
    assert sum(
        entry.event_type == "human.requested"
        for entry in retried.execution_log
    ) == 1
