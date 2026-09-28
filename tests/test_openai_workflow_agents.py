import json
from types import SimpleNamespace

import pytest

from app.agents.openai_workflow import (
    EditorialReview,
    OpenAIConditionalVerificationAgent,
    OpenAIEditorialCriticAgent,
    OpenAIProductionQualityAgent,
    OpenAIProductionPlannerAgent,
    OpenAIPublishingPreflightAgent,
    OpenAIResearchAgent,
    OpenAIVisualDirectorAgent,
    ProductionQualityReview,
    ProductionPlan,
    PublishingPreflightReview,
    VisualDirectorReview,
)
from app.domain.models import AgentResult, WorkflowInput, WorkflowRun
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.repository import InMemoryWorkflowRepository


class FakeUsage:
    def model_dump(self, mode="json"):
        return {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}


class FakeWebResponse:
    output_text = "已查核官方產品頁與發布資料；規格仍應以發布地區官網為準。"
    model = "gpt-5.6-sol"
    id = "resp-research"
    usage = FakeUsage()

    def model_dump(self, mode="json"):
        return {
            "output": [{"content": [{"annotations": [
                {"type": "url_citation", "url": "https://example.com/official", "title": "官方資料"},
                {"type": "url_citation", "url": "https://example.com/news", "title": "發布資料"},
            ]}]}]
        }


class FakeResponses:
    def __init__(self, parsed=None):
        self.parsed = parsed
        self.create_kwargs = None
        self.parse_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return FakeWebResponse()

    def parse(self, **kwargs):
        self.parse_kwargs = kwargs
        return SimpleNamespace(
            output_parsed=self.parsed,
            model="gpt-5.6-sol",
            id="resp-structured",
            usage=FakeUsage(),
        )


class TransientFailureResponses(FakeResponses):
    def parse(self, **kwargs):
        self.parse_kwargs = kwargs

        class RemoteProtocolError(Exception):
            pass

        raise RemoteProtocolError("Server disconnected without sending a response")


def workflow() -> WorkflowRun:
    return WorkflowRun(
        input=WorkflowInput(
            topic="DJI Osmo 360",
            goal="education",
            platforms=["youtube", "instagram"],
            output_type="short_video",
            target_word_count=300,
        )
    )


def script_sections():
    return [
        {"id": "hook", "label": "Hook", "voiceover": "開場內容", "visual_direction": "產品特寫"},
        {"id": "body", "label": "主體", "voiceover": "分析內容", "visual_direction": "比較畫面"},
        {"id": "cta", "label": "CTA", "voiceover": "留言分享", "visual_direction": "收尾畫面"},
    ]


@pytest.mark.asyncio
async def test_research_uses_openai_web_search_and_verification_can_skip() -> None:
    responses = FakeResponses()
    current = workflow()
    research = await OpenAIResearchAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=responses),
    ).execute(current)

    assert research.is_simulated is False
    assert research.confidence == 0.88
    assert len(research.evidence) == 2
    assert responses.create_kwargs["tools"] == [
        {"type": "web_search", "search_context_size": "low"}
    ]
    current.agent_results.append(research)
    verification = await OpenAIConditionalVerificationAgent(
        model="gpt-5.6-sol", timeout_seconds=30
    ).execute(current)
    assert verification.status == "skipped"
    assert verification.artifact["called"] is False


@pytest.mark.asyncio
async def test_editorial_and_production_agents_return_renderable_artifacts() -> None:
    current = workflow()
    current.artifacts["script"] = {"sections": script_sections(), "full_text": "完整腳本"}
    editorial = EditorialReview.model_validate(
        {
            "score": 91,
            "confidence": 0.93,
            "risk_level": "low",
            "summary": "語氣與結構已完成修訂。",
            "issues": [],
            "review_notes": ["縮短開場"],
            "revised_sections": script_sections(),
        }
    )
    editorial_result = await OpenAIEditorialCriticAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=FakeResponses(editorial)),
    ).execute(current)
    assert editorial_result.artifact["score"] == 91
    assert len(editorial_result.artifact["revised_sections"]) == 3

    production = ProductionPlan.model_validate(
        {
            "confidence": 0.94,
            "summary": "製作包完成。",
            "storyboard": [
                {"shot": 1, "section": "Hook", "voiceover": "開場內容", "visual": "產品特寫", "broll_queries": ["camera close up"], "duration_seconds": 6}
            ],
            "editorial_plan": [],
            "visual_plan": {
                "style": "乾淨專業",
                "palette": ["#1E3A5F"],
                "image_prompts": ["全景相機產品特寫"],
                "broll_queries": ["360 camera hands on"],
            },
            "distribution_kit": [
                {"platform": "youtube", "label": "YouTube", "title": "測試", "caption": "摘要", "cta": "訂閱", "hashtags": ["#相機"]},
                {"platform": "instagram", "label": "Instagram", "title": "測試", "caption": "摘要", "cta": "收藏", "hashtags": ["#相機"]},
            ],
            "estimated_duration_seconds": 6,
            "issues": [],
        }
    )
    production_responses = FakeResponses(production)
    production_result = await OpenAIProductionPlannerAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=production_responses),
    ).execute(current)
    package = production_result.artifact["production_package"]
    assert package["preview_ready"] is True
    assert len(package["distribution_kit"]) == 2
    assert package["generation"]["prompt_version"] == "workflow-production-v2"
    production_payload = json.loads(
        production_responses.parse_kwargs["input"][1]["content"]
    )
    assert production_payload["script"]["full_text"] == "完整腳本"
    assert "reference_materials" not in production_payload["request"]


@pytest.mark.asyncio
async def test_production_planner_builds_reviewable_fallback_on_transient_disconnect() -> None:
    current = workflow()
    current.artifacts["script"] = {
        "title": "DJI Osmo 360 開箱",
        "summary": "實拍評測摘要",
        "sections": script_sections(),
        "full_text": "完整 DJI Osmo 360 測評腳本",
    }
    responses = TransientFailureResponses()

    result = await OpenAIProductionPlannerAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=responses),
    ).execute(current)

    package = result.artifact["production_package"]
    assert result.is_simulated is True
    assert result.risk_level == "medium"
    assert package["preview_ready"] is True
    assert len(package["storyboard"]) == len(script_sections())
    assert {post["platform"] for post in package["distribution_kit"]} == {"youtube", "instagram"}
    assert package["generation"]["provider"] == "local_fallback"


@pytest.mark.asyncio
async def test_production_quality_and_publishing_preflight_are_independent_gates() -> None:
    current = workflow()
    current.artifacts["script"] = {"sections": script_sections(), "full_text": "完整腳本"}
    current.artifacts["production_package"] = {
        "storyboard": [{"shot": 1, "section": "Hook", "voiceover": "開場內容", "visual": "產品特寫", "duration_seconds": 6}],
        "distribution_kit": [
            {"platform": "youtube", "label": "YouTube", "title": "測試", "caption": "摘要", "cta": "訂閱", "hashtags": []},
            {"platform": "instagram", "label": "Instagram", "title": "測試", "caption": "摘要", "cta": "收藏", "hashtags": []},
        ],
        "preview_ready": True,
    }
    quality = ProductionQualityReview(
        score=94,
        confidence=0.95,
        ready_for_approval=True,
        summary="腳本、分鏡、時間軸與平台交付均一致。",
        checks=["段落覆蓋", "平台覆蓋"],
    )
    quality_result = await OpenAIProductionQualityAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=FakeResponses(quality)),
    ).execute(current)
    assert quality_result.agent == "production_quality"
    assert quality_result.artifact["production_quality_review"]["ready_for_approval"] is True
    current.agent_results.append(quality_result)

    preflight = PublishingPreflightReview(
        score=96,
        confidence=0.96,
        ready_for_human_approval=True,
        summary="發布前主張、授權提醒與平台交付檢查通過。",
        checks=["主張邊界", "授權提醒", "平台交付"],
    )
    preflight_result = await OpenAIPublishingPreflightAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=FakeResponses(preflight)),
    ).execute(current)
    assert preflight_result.agent == "publishing_preflight"
    assert preflight_result.artifact["publishing_preflight_review"]["ready_for_human_approval"] is True


@pytest.mark.asyncio
async def test_visual_director_reads_full_script_and_returns_specific_prompts() -> None:
    current = workflow()
    current.artifacts["script"] = {"sections": script_sections(), "full_text": "完整 DJI Osmo 360 測評腳本"}
    current.artifacts["production_package"] = {
        "storyboard": [
            {"shot": 1, "section": "Hook", "voiceover": "開場內容", "visual": "產品特寫", "duration_seconds": 8}
        ],
        "visual_plan": {"style": "專業", "palette": ["#000"], "image_prompts": ["舊提示"], "broll_queries": ["camera"]},
        "estimated_duration_seconds": 8,
    }
    review = VisualDirectorReview.model_validate(
        {
            "confidence": 0.95,
            "risk_level": "low",
            "summary": "已依完整逐字稿校準。",
            "issues": [],
            "alignment_notes": ["開場應呈現相機鏡片特寫。"],
            "image_prompts": ["DJI Osmo 360 外層鏡片微距特寫，拆裝工具置於潔淨桌面"],
            "youtube_references": [
                {
                    "shot": 1,
                    "title": "DJI Osmo 360 實拍參考",
                    "channel": "DJI",
                    "url": "https://www.youtube.com/watch?v=example123",
                    "usage_note": "參考產品轉台與鏡片特寫構圖。",
                }
            ],
            "generation_prompts": [
                {
                    "shot": 1,
                    "label": "鏡片產品特寫",
                    "image_prompt": "DJI Osmo 360 外層鏡片微距特寫，潔淨桌面，自然側光，垂直構圖。",
                    "video_prompt": "DJI Osmo 360 鏡片微距，鏡頭緩慢推近，4 秒，自然側光，首尾穩定。",
                    "negative_prompt": "浮水印、物體變形",
                    "aspect_ratio": "9:16",
                }
            ],
            "suggested_visual_cues": [
                {
                    "id": "ai-visual-1",
                    "shot": 1,
                    "cue_type": "product_shot",
                    "label": "鏡片產品特寫",
                    "start_seconds": 0,
                    "duration_seconds": 3,
                    "source": "ai",
                }
            ],
        }
    )
    responses = FakeResponses(review)
    result = await OpenAIVisualDirectorAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=responses),
    ).execute(current)

    assert result.agent == "visual_director"
    assert "DJI Osmo 360 外層鏡片" in result.artifact["visual_direction_review"]["image_prompts"][0]
    user_payload = responses.parse_kwargs["input"][1]["content"]
    assert "完整 DJI Osmo 360 測評腳本" in user_payload
    assert "human_visual_cues" in user_payload
    assert responses.create_kwargs["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert "tools" not in responses.parse_kwargs
    assert "verified_youtube_candidates" in user_payload
    assert result.artifact["visual_direction_review"]["generation_prompts"][0]["aspect_ratio"] == "9:16"


class StaticAgent:
    def __init__(self, name, artifact):
        self.name = name
        self.artifact = artifact

    async def execute(self, current):
        if self.name == "editorial_critic":
            assert current.artifacts.get("script")
        if self.name == "production_planner":
            assert current.artifacts["script"]["quality_status"] == "openai_editorial_reviewed"
        return AgentResult(
            agent=self.name,
            confidence=0.95,
            summary=f"{self.name} 完成",
            artifact=self.artifact,
        )


class StaticContentProvider:
    provider = "openai"
    model = "gpt-test"

    async def generate(self, current):
        sections = script_sections()
        return {
            "title": "完整腳本",
            "summary": "摘要",
            "sections": sections,
            "full_text": "\n\n".join(item["voiceover"] for item in sections),
            "word_count": 12,
            "target_word_count": 300,
            "word_count_difference": -288,
            "quality_status": "pending",
            "quality_checks": {},
            "review_notes": [],
            "research": {"summary": "研究", "sources": []},
            "generation": {"provider": self.provider, "model": self.model, "external": True},
        }


@pytest.mark.asyncio
async def test_orchestrator_runs_editor_after_script_and_uses_ai_production_package() -> None:
    package = {
        "content_kind": "short_video",
        "storyboard": [{"shot": 1, "section": "Hook", "voiceover": "開場", "visual": "特寫", "broll_queries": ["camera close up"], "duration_seconds": 5}],
        "editorial_plan": [],
        "visual_plan": {"style": "專業", "palette": ["#000"], "image_prompts": ["產品"], "broll_queries": ["camera"]},
        "distribution_kit": [{"platform": "youtube", "label": "YouTube", "title": "標題", "caption": "摘要", "cta": "訂閱", "hashtags": []}],
        "estimated_duration_seconds": 5,
        "preview_ready": True,
    }
    orchestrator = WorkflowOrchestrator(
        repository=InMemoryWorkflowRepository(),
        creation_agents=[StaticAgent("research", {"summary": "研究"})],
        editorial_agents=[StaticAgent("editorial_critic", {"score": 90, "review_notes": ["完成"], "revised_sections": script_sections()})],
        production_agents=[StaticAgent("production_planner", {"production_package": package})],
        content_provider=StaticContentProvider(),
    )
    created = await orchestrator.create(workflow().input.model_copy(update={"platforms": ["youtube"]}))
    result = await orchestrator.run_until_gate(created.id)

    assert result.artifacts["script"]["quality_status"] == "openai_editorial_reviewed"
    assert result.artifacts["production_package"] == package
    assert [item.agent for item in result.agent_results] == [
        "research", "writer", "editorial_critic", "production_planner"
    ]
    assert all(item.is_simulated is False for item in result.agent_results)
    writer = next(item for item in result.agent_results if item.agent == "writer")
    assert writer.artifact["provider"] == "openai"
    assert any(entry.agent == "writer" and entry.title == "撰寫代理完成" for entry in result.execution_log)


@pytest.mark.asyncio
async def test_orchestrator_persists_quality_gate_reviews_before_final_approval() -> None:
    package = {
        "content_kind": "short_video",
        "storyboard": [{"shot": 1, "section": "Hook", "voiceover": "開場", "visual": "特寫", "duration_seconds": 5}],
        "editorial_plan": [],
        "visual_plan": {"style": "專業", "palette": ["#000"], "image_prompts": ["產品"], "broll_queries": ["camera"]},
        "distribution_kit": [{"platform": "youtube", "label": "YouTube", "title": "標題", "caption": "摘要", "cta": "訂閱", "hashtags": []}],
        "estimated_duration_seconds": 5,
        "preview_ready": True,
    }
    quality_review = {"score": 94, "confidence": 0.95, "ready_for_approval": True, "summary": "製作包完整", "issues": [], "checks": ["段落覆蓋"]}
    preflight_review = {"score": 96, "confidence": 0.96, "ready_for_human_approval": True, "summary": "發布前檢查通過", "issues": [], "checks": ["平台交付"]}
    orchestrator = WorkflowOrchestrator(
        repository=InMemoryWorkflowRepository(),
        creation_agents=[StaticAgent("research", {"summary": "研究"})],
        editorial_agents=[StaticAgent("editorial_critic", {"score": 90, "review_notes": ["完成"], "revised_sections": script_sections()})],
        production_agents=[StaticAgent("production_planner", {"production_package": package})],
        visual_agents=[
            StaticAgent("production_quality", {"production_quality_review": quality_review}),
            StaticAgent("publishing_preflight", {"publishing_preflight_review": preflight_review}),
        ],
        content_provider=StaticContentProvider(),
    )
    created = await orchestrator.create(workflow().input.model_copy(update={"platforms": ["youtube"]}))
    result = await orchestrator.run_until_gate(created.id)

    assert result.status.value == "waiting_for_human"
    assert result.human_request.reason == "final_approval"
    assert result.artifacts["production_package"]["production_quality_review"]["score"] == 94
    assert result.artifacts["production_package"]["publishing_preflight_review"]["score"] == 96
    assert [item.agent for item in result.agent_results][-2:] == ["production_quality", "publishing_preflight"]
