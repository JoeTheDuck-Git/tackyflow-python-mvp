import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agents.opportunity import LocalManualSignalProvider, LocalOpportunityAgent
from app.api.opportunities import build_opportunity_router
from app.domain.models import (
    OpportunityGenerationStatus,
    OpportunityRequest,
    WorkflowInput,
)
from app.opportunities.repository import (
    InMemoryOpportunityRepository,
    SQLiteOpportunityRepository,
)
from app.workflow.repository import InMemoryWorkflowRepository


@pytest.mark.asyncio
async def test_opportunity_sqlite_snapshot_survives_repository_recreation(tmp_path) -> None:
    database_path = tmp_path / "workflow.db"
    request = OpportunityRequest(topic="Insta360", workspace_id="brand-a")
    signals = await LocalManualSignalProvider().collect(request)
    generation = await LocalOpportunityAgent().generate(
        request, [], signals, generation_id="generation-1"
    )
    generation = generation.model_copy(update={"request_hash": "hash-1"})

    first = SQLiteOpportunityRepository(database_path)
    await first.save(generation)

    recreated = SQLiteOpportunityRepository(database_path)
    restored = await recreated.get("generation-1")
    listed = await recreated.list(workspace_id="brand-a")
    cached = await recreated.find_by_request_hash("hash-1", "brand-a")

    assert restored.opportunities[0].brief.target_audience
    assert listed[0].id == "generation-1"
    assert cached is not None and cached.id == "generation-1"


@pytest.mark.asyncio
async def test_context_materially_changes_ranking_and_complete_brief() -> None:
    agent = LocalOpportunityAgent()
    beginner = OpportunityRequest(
        topic="Insta360",
        audience="第一次購買運動相機的新手",
        goal="education",
        platforms=["instagram"],
        preferred_formats=["短影音"],
        brand_voice="活潑、輕鬆、自然對話",
        brand_brief="降低新手理解門檻",
    )
    buyer = OpportunityRequest(
        topic="Insta360",
        audience="企業影像採購主管",
        goal="conversion",
        platforms=["linkedin"],
        preferred_formats=["深度文章"],
        brand_voice="專業、嚴謹、可信",
        brand_brief="強調安全合規、投資回報與證據",
    )

    beginner_result = await agent.generate(beginner, [], generation_id="beginner")
    buyer_result = await agent.generate(buyer, [], generation_id="buyer")

    assert [item.type for item in beginner_result.opportunities] != [
        item.type for item in buyer_result.opportunities
    ]
    beginner_brief = beginner_result.opportunities[0].brief
    buyer_brief = buyer_result.opportunities[0].brief
    assert beginner_brief.target_audience == beginner.audience
    assert buyer_brief.target_audience == buyer.audience
    assert "短影音" in beginner_brief.recommended_formats
    assert "深度文章" in buyer_brief.recommended_formats
    assert beginner_brief.cta != buyer_brief.cta
    assert any("降低新手" in point for point in beginner_brief.key_points)
    assert any("投資回報" in point for point in buyer_brief.key_points)


@pytest.mark.asyncio
async def test_no_live_signal_is_explicit_and_never_presented_as_market_demand() -> None:
    request = OpportunityRequest(
        topic="AI 工作流",
        reference_urls=["https://example.com/reference"],
        count=8,
    )
    signals = await LocalManualSignalProvider().collect(request)
    result = await LocalOpportunityAgent().generate(
        request, [], signals, generation_id="offline"
    )

    assert result.has_live_signals is False
    assert all(signal.is_live is False for signal in result.signals)
    assert all(item.data_confidence.value == "low" for item in result.opportunities)
    assert all(item.score <= 85 for item in result.opportunities)
    assert all("no_live_signals" in item.scoring_method for item in result.opportunities)
    assert all(
        any("即時搜尋" in evidence for evidence in item.brief.evidence_needed)
        for item in result.opportunities
    )
    assert all(not item.topic.startswith("搜尋") for item in result.opportunities)


class TrackingOpportunityRepository(InMemoryOpportunityRepository):
    def __init__(self) -> None:
        super().__init__()
        self.saved_statuses: list[OpportunityGenerationStatus] = []

    async def save(self, generation):
        self.saved_statuses.append(generation.status)
        return await super().save(generation)


def _client_with_repository(opportunity_repository):
    app = FastAPI()
    app.include_router(
        build_opportunity_router(
            InMemoryWorkflowRepository(),
            opportunity_repository=opportunity_repository,
        )
    )
    return TestClient(app)


def test_generation_api_persists_lists_updates_and_is_idempotent() -> None:
    repository = TrackingOpportunityRepository()
    client = _client_with_repository(repository)
    client.headers["X-Workspace-ID"] = "workspace-a"
    payload = {
        "topic": "Insta360",
        "audience": "旅遊影像新手",
        "goal": "education",
        "platforms": ["youtube", "instagram"],
        "preferred_formats": ["短影音"],
        "region": "台灣",
        "language": "繁體中文",
        "brand_name": "示範品牌",
        "brand_brief": "先教育，不做誇大效果承諾",
        "constraints": ["低預算"],
        "reference_urls": ["https://example.com/brief"],
        "workspace_id": "workspace-a",
        "count": 4,
    }

    created = client.post("/api/v1/opportunities/generate", json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "completed"
    assert body["provider"] == "local_rule"
    assert body["request"]["audience"] == "旅遊影像新手"
    assert repository.saved_statuses[:2] == [
        OpportunityGenerationStatus.PENDING,
        OpportunityGenerationStatus.COMPLETED,
    ]

    repeated = client.post("/api/v1/opportunities/generate", json=payload)
    assert repeated.json()["id"] == body["id"]
    assert len(repository.saved_statuses) == 2

    listed = client.get(
        "/api/v1/opportunities", params={"workspace_id": "workspace-a", "limit": 10}
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]
    assert client.get(f"/api/v1/opportunities/{body['id']}").status_code == 200

    item_id = body["opportunities"][0]["id"]
    updated = client.patch(
        f"/api/v1/opportunities/{body['id']}/items/{item_id}",
        json={"status": "saved", "feedback_note": "適合下一季"},
    )
    assert updated.status_code == 200
    saved_item = next(item for item in updated.json()["opportunities"] if item["id"] == item_id)
    assert saved_item["status"] == "saved"
    assert saved_item["feedback_note"] == "適合下一季"
    assert updated.json()["events"][-1]["action"] == "item_updated"


def test_regenerate_creates_immutable_child_and_honors_exclusions() -> None:
    repository = InMemoryOpportunityRepository()
    client = _client_with_repository(repository)
    client.headers["X-Workspace-ID"] = "workspace-r"
    parent = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "大疆", "workspace_id": "workspace-r", "count": 4},
    ).json()
    replaced = parent["opportunities"][1]
    parent_topics = {item["topic"] for item in parent["opportunities"]}

    response = client.post(
        f"/api/v1/opportunities/{parent['id']}/items/{replaced['id']}/regenerate",
        json={
            "exclude_topics": ["大疆值得升級嗎"],
            "modification_instruction": "改成新手購買決策，減少規格堆疊",
        },
    )
    assert response.status_code == 200
    child = response.json()
    assert child["id"] != parent["id"]
    assert child["parent_generation_id"] == parent["id"]
    assert child["revision"] == 1
    assert len(child["opportunities"]) == len(parent["opportunities"])
    assert len({item["id"] for item in child["opportunities"]}) == len(child["opportunities"])
    replacement = child["opportunities"][1]
    for index, original in enumerate(parent["opportunities"]):
        if index != 1:
            assert child["opportunities"][index]["id"] == original["id"]
    assert replacement["id"] != replaced["id"]
    assert replacement["status"] == "new"
    assert replacement["adopted_workflow_id"] is None
    assert replacement["feedback_note"] == "改成新手購買決策，減少規格堆疊"
    assert child["request"]["constraints"][-1] == "人工單題修改指示：改成新手購買決策，減少規格堆疊"
    assert replacement["topic"] not in parent_topics
    assert child["events"][-1]["from_topic"] == replaced["topic"]
    assert child["events"][-1]["to_topic"] == replacement["topic"]
    assert "已套用人工單題修改指示" in child["events"][-1]["note"]

    unchanged_parent = client.get(f"/api/v1/opportunities/{parent['id']}").json()
    assert unchanged_parent["opportunities"][1]["topic"] == replaced["topic"]
    versions = client.get(
        "/api/v1/opportunities", params={"workspace_id": "workspace-r"}
    ).json()
    assert {item["id"] for item in versions} == {parent["id"], child["id"]}


def test_item_regeneration_cache_identity_is_scoped_to_parent_and_item() -> None:
    repository = InMemoryOpportunityRepository()
    client = _client_with_repository(repository)
    client.headers["X-Workspace-ID"] = "workspace-regen-identity"
    parent = client.post(
        "/api/v1/opportunities/generate",
        json={
            "topic": "同一需求重做不同題目",
            "workspace_id": "workspace-regen-identity",
            "count": 4,
        },
    ).json()
    first_item = parent["opportunities"][0]
    second_item = parent["opportunities"][1]

    first_child = client.post(
        f"/api/v1/opportunities/{parent['id']}/items/{first_item['id']}/regenerate",
        json={"variation": 77},
    ).json()
    repeated_first_child = client.post(
        f"/api/v1/opportunities/{parent['id']}/items/{first_item['id']}/regenerate",
        json={"variation": 77},
    ).json()
    second_child = client.post(
        f"/api/v1/opportunities/{parent['id']}/items/{second_item['id']}/regenerate",
        json={"variation": 77},
    ).json()

    assert repeated_first_child["id"] == first_child["id"]
    assert second_child["id"] != first_child["id"]
    assert second_child["request_hash"] != first_child["request_hash"]
    assert first_child["parent_generation_id"] == parent["id"]
    assert second_child["parent_generation_id"] == parent["id"]
    assert first_child["opportunities"][0]["id"] != first_item["id"]
    assert first_child["opportunities"][1]["id"] == second_item["id"]
    assert second_child["opportunities"][0]["id"] == first_item["id"]
    assert second_child["opportunities"][1]["id"] != second_item["id"]


class FailingAgent:
    provider = "failing_test"
    model = "failure"

    async def generate(self, request, history_topics, signals=None, generation_id=None):
        raise RuntimeError("expected failure")


def test_failed_generation_is_persisted_and_validation_is_strict() -> None:
    repository = InMemoryOpportunityRepository()
    app = FastAPI()
    app.include_router(
        build_opportunity_router(
            InMemoryWorkflowRepository(),
            agent=FailingAgent(),
            opportunity_repository=repository,
        )
    )
    client = TestClient(app)

    failed = client.post("/api/v1/opportunities/generate", json={"topic": "測試"})
    assert failed.status_code == 502
    persisted = asyncio.run(repository.list())
    assert len(persisted) == 1
    assert persisted[0].status == OpportunityGenerationStatus.FAILED
    assert persisted[0].error == "RuntimeError: opportunity generation failed"
    assert "expected failure" not in persisted[0].events[-1].note
    assert client.post(
        "/api/v1/opportunities/generate", json={"topic": "   "}
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/generate", json={"topic": "測試", "count": 9}
    ).status_code == 422


def test_workflow_input_accepts_opportunity_traceability_ids() -> None:
    workflow_input = WorkflowInput(
        topic="採用的題目",
        goal="education",
        platforms=["youtube"],
        output_type="short_video",
        source_opportunity_id="opp-123",
        source_generation_id="generation-123",
    )
    assert workflow_input.source_opportunity_id == "opp-123"
    assert workflow_input.source_generation_id == "generation-123"

    with pytest.raises(ValidationError):
        WorkflowInput(
            topic="不完整來源",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            source_generation_id="generation-only",
        )
    with pytest.raises(ValidationError):
        WorkflowInput(
            topic="平台空白",
            goal="education",
            platforms=["  "],
            output_type="short_video",
        )
