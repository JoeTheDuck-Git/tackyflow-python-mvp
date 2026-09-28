from types import SimpleNamespace

import pytest

from app.agents.gemini_youtube import (
    GeminiMomentCheck,
    GeminiTimecodedMoment,
    GeminiVideoAnalysis,
    GeminiVideoVerification,
    GeminiYouTubeReferenceVerifierAgent,
    canonicalize_youtube_url,
    format_timestamp,
)
from app.domain.models import WorkflowInput, WorkflowRun
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.repository import InMemoryWorkflowRepository


class FakeResponse:
    def __init__(self, *, text="", parsed=None):
        self.text = text
        self.parsed = parsed
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            total_token_count=15,
        )

    def model_dump_json(self):
        return "{}"


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def make_workflow() -> WorkflowRun:
    current = WorkflowRun(
        input=WorkflowInput(
            topic="DJI Osmo 360",
            goal="review",
            platforms=["youtube"],
            output_type="short_video",
            target_word_count=500,
        )
    )
    current.artifacts["production_package"] = {
        "preview_ready": True,
        "storyboard": [
            {
                "shot": 1,
                "section": "更換鏡片",
                "voiceover": "依照官方步驟更換鏡片。",
                "visual": "俯拍拆裝過程",
                "broll_queries": ["Osmo 360 lens replacement"],
            }
        ],
        "visual_plan": {"youtube_references": []},
    }
    return current


def test_youtube_url_canonicalization_is_strict() -> None:
    assert canonicalize_youtube_url("https://youtu.be/abcDEF12345?t=8") == (
        "https://www.youtube.com/watch?v=abcDEF12345",
        "abcDEF12345",
    )
    assert canonicalize_youtube_url("https://www.youtube.com/watch?v=abcDEF12345&list=PL1") == (
        "https://www.youtube.com/watch?v=abcDEF12345",
        "abcDEF12345",
    )
    assert canonicalize_youtube_url("https://youtube.com.evil.test/watch?v=abcDEF12345") is None
    assert canonicalize_youtube_url("http://youtube.com/watch?v=abcDEF12345") is None
    assert canonicalize_youtube_url("https://www.youtube.com/shorts/abcDEF12345") is None
    assert format_timestamp(133) == "02:13"


@pytest.mark.asyncio
async def test_gemini_agent_reads_video_then_rechecks_and_emits_deep_link() -> None:
    analysis = GeminiVideoAnalysis(
        title="Osmo 360 鏡片更換教學",
        channel="官方頻道",
        summary="示範鏡片拆裝。",
        moments=[
            GeminiTimecodedMoment(
                shot=1,
                start_seconds=130,
                end_seconds=142,
                description="俯拍展示鏡片拆卸與重新固定。",
                visual_evidence="雙手使用工具拆卸鏡片座。",
                audio_evidence="旁白說明固定步驟。",
                filming_takeaway="採用俯拍與工具特寫。",
                relevance_reason="可直接參考拆裝分鏡。",
                confidence=0.91,
            )
        ],
    )
    verification = GeminiVideoVerification(
        checks=[
            GeminiMomentCheck(
                proposal_index=0,
                accepted=True,
                observed_start_seconds=131,
                observed_end_seconds=143,
                visual_match=True,
                audio_match=True,
                rationale="重新查看後確認拆裝畫面與旁白一致。",
                confidence=0.93,
            )
        ]
    )
    models = FakeModels(
        [
            FakeResponse(text="https://www.youtube.com/watch?v=abcDEF12345"),
            FakeResponse(parsed=analysis),
            FakeResponse(parsed=verification),
        ]
    )
    agent = GeminiYouTubeReferenceVerifierAgent(
        model="gemini-2.5-flash",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(models=models),
    )

    result = await agent.execute(make_workflow())

    assert result.agent == "youtube_reference_verifier"
    assert result.status == "passed"
    reference = result.artifact["youtube_reference_review"]["references"][0]
    assert reference["deep_link"].endswith("&t=131s")
    assert reference["start_timestamp"] == "02:11"
    assert reference["verification_status"] == "verified"
    assert len(models.calls) == 3
    assert models.calls[1]["contents"][0].file_data.file_uri.endswith("abcDEF12345")
    assert "最多只保留 4 個" in models.calls[1]["contents"][1]
    assert models.calls[1]["config"].max_output_tokens == 6000
    assert models.calls[2]["contents"][0].file_data.file_uri.endswith("abcDEF12345")


@pytest.mark.asyncio
async def test_gemini_agent_hides_unverified_segments() -> None:
    analysis = GeminiVideoAnalysis(
        title="不相符影片",
        summary="內容只有產品展示。",
        moments=[
            GeminiTimecodedMoment(
                shot=1,
                start_seconds=10,
                end_seconds=20,
                description="產品旋轉展示。",
                filming_takeaway="無",
                relevance_reason="可能相關。",
                confidence=0.7,
            )
        ],
    )
    verification = GeminiVideoVerification(
        checks=[
            GeminiMomentCheck(
                proposal_index=0,
                accepted=False,
                observed_start_seconds=10,
                observed_end_seconds=20,
                visual_match=False,
                audio_match=False,
                rationale="沒有鏡片拆裝內容。",
                confidence=0.95,
            )
        ]
    )
    models = FakeModels(
        [
            FakeResponse(text="https://www.youtube.com/watch?v=abcDEF12345"),
            FakeResponse(parsed=analysis),
            FakeResponse(parsed=verification),
        ]
    )
    result = await GeminiYouTubeReferenceVerifierAgent(
        model="gemini-2.5-flash",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(models=models),
    ).execute(make_workflow())

    assert result.status == "skipped"
    assert result.artifact["youtube_reference_review"]["references"] == []
    assert result.artifact["rejected"][0]["status"] == "rejected"


def test_orchestrator_merges_only_verified_references() -> None:
    workflow = make_workflow()
    workflow.agent_results.append(
        SimpleNamespace(
            agent="youtube_reference_verifier",
            artifact={
                "youtube_reference_review": {
                    "status": "verified",
                    "references": [{"shot": 1, "url": "https://www.youtube.com/watch?v=abcDEF12345"}],
                }
            },
        )
    )
    orchestrator = WorkflowOrchestrator(
        repository=InMemoryWorkflowRepository(),
        creation_agents=[],
        production_agents=[],
    )

    orchestrator._apply_latest_youtube_reference_review(workflow)

    assert workflow.artifacts["production_package"]["visual_plan"]["youtube_references"][0]["shot"] == 1
