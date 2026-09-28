import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agents.opportunity import LocalOpportunityAgent
from app.api.opportunities import build_opportunity_router
from app.domain.models import (
    OpportunityRegenerateRequest,
    OpportunityRequest,
    WorkflowInput,
    WorkflowRun,
)
from app.opportunities.repository import InMemoryOpportunityRepository
from app.workflow.repository import InMemoryWorkflowRepository


def _app(workflows, opportunities, agent=None) -> FastAPI:
    app = FastAPI()
    app.include_router(
        build_opportunity_router(
            workflows,
            agent=agent,
            opportunity_repository=opportunities,
        )
    )
    return app


def test_request_and_regeneration_normalize_lists_and_reject_blank_platforms() -> None:
    request = OpportunityRequest(
        topic="  Insta360   入門  ",
        goal="  education ",
        platforms=[" youtube ", "", "YOUTUBE", " instagram  "],
        preferred_formats=[" 短影音 ", "短影音", " "],
    )
    assert request.topic == "Insta360 入門"
    assert request.goal == "education"
    assert request.platforms == ["youtube", "instagram"]
    assert request.preferred_formats == ["短影音"]

    regeneration = OpportunityRegenerateRequest(
        platforms=[" youtube ", "YOUTUBE"],
        exclude_topics=[" 題目 A ", "題目 A", ""],
    )
    assert regeneration.platforms == ["youtube"]
    assert regeneration.exclude_topics == ["題目 A"]

    with pytest.raises(ValidationError):
        OpportunityRequest(topic="測試", platforms=[" ", "\n"])
    with pytest.raises(ValidationError):
        OpportunityRegenerateRequest(platforms=[" "])
    with pytest.raises(ValidationError):
        OpportunityRegenerateRequest(goal="   ")

    client = TestClient(
        _app(InMemoryWorkflowRepository(), InMemoryOpportunityRepository())
    )
    response = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "測試", "platforms": [" "]},
    )
    assert response.status_code == 422


class SlowUpdateOpportunityRepository(InMemoryOpportunityRepository):
    delay_saves = False

    async def save(self, generation):
        if self.delay_saves:
            await asyncio.sleep(0.03)
        return await super().save(generation)


@pytest.mark.asyncio
async def test_concurrent_item_updates_do_not_overwrite_each_other() -> None:
    opportunities = SlowUpdateOpportunityRepository()
    app = _app(InMemoryWorkflowRepository(), opportunities)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        created = (
            await client.post(
                "/api/v1/opportunities/generate",
                json={"topic": "並行更新", "count": 4},
            )
        ).json()
        first_id, second_id = [
            item["id"] for item in created["opportunities"][:2]
        ]
        opportunities.delay_saves = True

        first_response, second_response = await asyncio.gather(
            client.patch(
                f"/api/v1/opportunities/{created['id']}/items/{first_id}",
                json={"status": "saved"},
            ),
            client.patch(
                f"/api/v1/opportunities/{created['id']}/items/{second_id}",
                json={"status": "dismissed"},
            ),
        )
        assert first_response.status_code == second_response.status_code == 200

        final = (
            await client.get(f"/api/v1/opportunities/{created['id']}")
        ).json()
        statuses = {item["id"]: item["status"] for item in final["opportunities"]}
        assert statuses[first_id] == "saved"
        assert statuses[second_id] == "dismissed"
        assert sum(event["action"] == "item_updated" for event in final["events"]) == 2


def test_adopted_status_requires_a_matching_persisted_workflow() -> None:
    workflows = InMemoryWorkflowRepository()
    opportunities = InMemoryOpportunityRepository()
    client = TestClient(_app(workflows, opportunities))
    client.headers["X-Workspace-ID"] = "workspace-a"
    generation = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "採用完整性", "workspace_id": "workspace-a"},
    ).json()
    item = generation["opportunities"][0]
    endpoint = (
        f"/api/v1/opportunities/{generation['id']}/items/{item['id']}"
    )

    assert client.patch(endpoint, json={"status": "adopted"}).status_code == 422
    assert (
        client.patch(
            endpoint,
            json={"status": "adopted", "adopted_workflow_id": "missing"},
        ).status_code
        == 409
    )

    mismatch = WorkflowRun(
        id="workflow-mismatch",
        input=WorkflowInput(
            topic=item["topic"],
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            workspace_id="another-workspace",
            source_generation_id=generation["id"],
            source_opportunity_id=item["id"],
        ),
    )
    asyncio.run(workflows.save(mismatch))
    assert (
        client.patch(
            endpoint,
            json={
                "status": "adopted",
                "adopted_workflow_id": mismatch.id,
            },
        ).status_code
        == 409
    )

    matching = WorkflowRun(
        id="workflow-good",
        input=mismatch.input.model_copy(update={"workspace_id": "workspace-a"}),
    )
    asyncio.run(workflows.save(matching))
    adopted_response = client.patch(
        endpoint,
        json={"status": "adopted", "adopted_workflow_id": matching.id},
    )
    assert adopted_response.status_code == 200
    adopted = next(
        candidate
        for candidate in adopted_response.json()["opportunities"]
        if candidate["id"] == item["id"]
    )
    assert adopted["adopted_workflow_id"] == matching.id

    restored_response = client.patch(endpoint, json={"status": "saved"})
    restored = next(
        candidate
        for candidate in restored_response.json()["opportunities"]
        if candidate["id"] == item["id"]
    )
    assert restored["adopted_workflow_id"] is None


def test_adopted_workflow_provenance_is_valid_only_through_generation_ancestry() -> None:
    workflows = InMemoryWorkflowRepository()
    opportunities = InMemoryOpportunityRepository()
    client = TestClient(_app(workflows, opportunities))
    client.headers["X-Workspace-ID"] = "lineage"
    parent = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "版本採用語意", "workspace_id": "lineage", "count": 4},
    ).json()
    adopted_item, first_replaced, second_replaced, sibling_item = parent[
        "opportunities"
    ]

    parent_workflow = WorkflowRun(
        id="workflow-from-parent",
        input=WorkflowInput(
            topic=adopted_item["topic"],
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            workspace_id="lineage",
            source_generation_id=parent["id"],
            source_opportunity_id=adopted_item["id"],
        ),
    )
    asyncio.run(workflows.save(parent_workflow))
    assert client.patch(
        f"/api/v1/opportunities/{parent['id']}/items/{adopted_item['id']}",
        json={
            "status": "adopted",
            "adopted_workflow_id": parent_workflow.id,
        },
    ).status_code == 200

    first_child_response = client.post(
        f"/api/v1/opportunities/{parent['id']}/items/{first_replaced['id']}/regenerate",
        json={"variation": 31},
    )
    second_child_response = client.post(
        f"/api/v1/opportunities/{parent['id']}/items/{second_replaced['id']}/regenerate",
        json={"variation": 31},
    )
    assert first_child_response.status_code == second_child_response.status_code == 200
    first_child = first_child_response.json()
    second_child = second_child_response.json()

    inherited_adoption = next(
        item
        for item in first_child["opportunities"]
        if item["id"] == adopted_item["id"]
    )
    assert inherited_adoption["status"] == "adopted"
    assert inherited_adoption["adopted_workflow_id"] == parent_workflow.id
    # The workflow truthfully retains the parent as its source, while the child
    # accepts it because that source is in the immutable ancestry and item ID is
    # unchanged.
    inherited_update = client.patch(
        f"/api/v1/opportunities/{first_child['id']}/items/{adopted_item['id']}",
        json={
            "status": "adopted",
            "adopted_workflow_id": parent_workflow.id,
        },
    )
    assert inherited_update.status_code == 200

    sibling_workflow = WorkflowRun(
        id="workflow-from-sibling",
        input=WorkflowInput(
            topic=sibling_item["topic"],
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            workspace_id="lineage",
            source_generation_id=first_child["id"],
            source_opportunity_id=sibling_item["id"],
        ),
    )
    asyncio.run(workflows.save(sibling_workflow))
    assert client.patch(
        f"/api/v1/opportunities/{first_child['id']}/items/{sibling_item['id']}",
        json={
            "status": "adopted",
            "adopted_workflow_id": sibling_workflow.id,
        },
    ).status_code == 200
    # A sibling snapshot is not provenance ancestry, even when it contains the
    # same unchanged item ID.
    rejected_sibling = client.patch(
        f"/api/v1/opportunities/{second_child['id']}/items/{sibling_item['id']}",
        json={
            "status": "adopted",
            "adopted_workflow_id": sibling_workflow.id,
        },
    )
    assert rejected_sibling.status_code == 409


class RecordingOpportunityAgent(LocalOpportunityAgent):
    def __init__(self) -> None:
        self.requests: list[OpportunityRequest] = []
        self.histories: list[list[str]] = []

    async def generate(self, request, history_topics, signals=None, generation_id=None):
        self.requests.append(request.model_copy(deep=True))
        self.histories.append(list(history_topics))
        return await super().generate(
            request, history_topics, signals, generation_id
        )


def test_saved_and_dismissed_feedback_change_future_generation_context() -> None:
    agent = RecordingOpportunityAgent()
    client = TestClient(
        _app(InMemoryWorkflowRepository(), InMemoryOpportunityRepository(), agent)
    )
    client.headers["X-Workspace-ID"] = "feedback"
    parent = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "內容回饋", "workspace_id": "feedback"},
    ).json()
    saved, dismissed, untouched = parent["opportunities"][:3]
    assert client.patch(
        f"/api/v1/opportunities/{parent['id']}/items/{saved['id']}",
        json={"status": "saved"},
    ).status_code == 200
    assert client.patch(
        f"/api/v1/opportunities/{parent['id']}/items/{dismissed['id']}",
        json={"status": "dismissed"},
    ).status_code == 200

    next_generation = client.post(
        "/api/v1/opportunities/generate",
        json={
            "topic": "內容回饋",
            "workspace_id": "feedback",
            "variation": 1,
        },
    )
    assert next_generation.status_code == 200
    assert saved["topic"] in agent.histories[-1]
    assert dismissed["topic"] not in agent.histories[-1]
    assert dismissed["topic"] in agent.requests[-1].exclude_topics
    assert untouched["topic"] not in agent.histories[-1]
    assert untouched["topic"] not in agent.requests[-1].exclude_topics


class FailOnRegenerationAgent(LocalOpportunityAgent):
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request, history_topics, signals=None, generation_id=None):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("regeneration failed as expected")
        return await super().generate(
            request, history_topics, signals, generation_id
        )


def test_failed_item_regeneration_returns_gateway_error_and_persists_failure() -> None:
    agent = FailOnRegenerationAgent()
    opportunities = InMemoryOpportunityRepository()
    client = TestClient(_app(InMemoryWorkflowRepository(), opportunities, agent))
    parent = client.post(
        "/api/v1/opportunities/generate", json={"topic": "失敗重做"}
    ).json()

    failed = client.post(
        f"/api/v1/opportunities/{parent['id']}/items/"
        f"{parent['opportunities'][0]['id']}/regenerate",
        json={},
    )
    assert failed.status_code == 502
    assert failed.json()["detail"] == "opportunity generation failed"
    persisted = asyncio.run(opportunities.list())
    failed_children = [
        generation
        for generation in persisted
        if generation.parent_generation_id == parent["id"]
        and generation.status.value == "failed"
    ]
    assert len(failed_children) == 1
    assert failed_children[0].error == "RuntimeError: opportunity generation failed"
    assert "regeneration failed as expected" not in failed_children[0].events[-1].note
