import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.opportunities import build_opportunity_router
from app.api.routes import build_router
from app.domain.models import WorkflowInput
from app.opportunities.repository import InMemoryOpportunityRepository
from app.workflow.orchestrator import InvalidWorkflowTransitionError, WorkflowOrchestrator
from app.workflow.repository import InMemoryWorkflowRepository


def _workflow_client() -> TestClient:
    app = FastAPI()
    repository = InMemoryWorkflowRepository()
    opportunities = InMemoryOpportunityRepository()
    app.include_router(
        build_router(
            WorkflowOrchestrator(
                repository=repository,
                creation_agents=[],
                production_agents=[],
            ),
            opportunities,
        )
    )
    app.include_router(
        build_opportunity_router(
            repository,
            opportunity_repository=opportunities,
        )
    )
    return TestClient(app)


def _payload() -> dict[str, object]:
    return {
        "topic": "工作區隔離測試",
        "goal": "education",
        "platforms": ["youtube"],
        "output_type": "short_video",
    }


def test_workflow_routes_are_scoped_by_workspace_header() -> None:
    client = _workflow_client()
    team_a_headers = {"X-Workspace-ID": "team-a"}
    team_b_headers = {"X-Workspace-ID": "team-b"}

    spoofed = {**_payload(), "workspace_id": "spoofed-body-workspace"}
    assert client.post(
        "/api/v1/workflows", json=spoofed, headers=team_a_headers
    ).status_code == 422
    created_a = client.post("/api/v1/workflows", json=_payload(), headers=team_a_headers)
    assert created_a.status_code == 201
    assert created_a.json()["input"]["workspace_id"] == "team-a"
    workflow_a_id = created_a.json()["id"]

    assert [item["id"] for item in client.get("/api/v1/workflows", headers=team_a_headers).json()] == [workflow_a_id]
    assert client.get("/api/v1/workflows", headers=team_b_headers).json() == []
    assert client.get(f"/api/v1/workflows/{workflow_a_id}", headers=team_b_headers).status_code == 404
    assert client.post(f"/api/v1/workflows/{workflow_a_id}/run", headers=team_b_headers).status_code == 404

    created_b = client.post("/api/v1/workflows", json=_payload(), headers=team_b_headers)
    assert created_b.status_code == 201
    assert created_b.json()["id"] != workflow_a_id
    assert created_b.json()["input"]["workspace_id"] == "team-b"


def test_opportunity_routes_are_scoped_by_workspace_header() -> None:
    client = _workflow_client()
    team_a_headers = {"X-Workspace-ID": "team-a"}
    team_b_headers = {"X-Workspace-ID": "team-b"}

    spoofed = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "跨工作區選題", "workspace_id": "spoofed-team"},
        headers=team_a_headers,
    )
    assert spoofed.status_code == 422
    created_a = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "跨工作區選題"},
        headers=team_a_headers,
    )
    assert created_a.status_code == 200
    generation_a = created_a.json()
    assert generation_a["request"]["workspace_id"] == "team-a"
    generation_id = generation_a["id"]
    item_id = generation_a["opportunities"][0]["id"]

    invalid_cross_workspace_handoff = {
        **_payload(),
        "topic": generation_a["opportunities"][0]["topic"],
        "source_generation_id": generation_id,
        "source_opportunity_id": item_id,
    }
    assert client.post(
        "/api/v1/workflows",
        json=invalid_cross_workspace_handoff,
        headers=team_b_headers,
    ).status_code == 422

    assert client.get("/api/v1/opportunities", headers=team_b_headers).json() == []
    assert client.get(f"/api/v1/opportunities/{generation_id}", headers=team_b_headers).status_code == 404
    assert client.patch(
        f"/api/v1/opportunities/{generation_id}/items/{item_id}",
        json={"status": "saved"},
        headers=team_b_headers,
    ).status_code == 404
    assert client.post(
        f"/api/v1/opportunities/{generation_id}/items/{item_id}/regenerate",
        json={},
        headers=team_b_headers,
    ).status_code == 404
    assert client.get(
        "/api/v1/opportunities",
        params={"workspace_id": "team-a"},
        headers=team_b_headers,
    ).status_code == 404

    created_b = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "跨工作區選題"},
        headers=team_b_headers,
    )
    assert created_b.status_code == 200
    assert created_b.json()["id"] != generation_id
    assert created_b.json()["request"]["workspace_id"] == "team-b"


class _SlowWorkflowRepository(InMemoryWorkflowRepository):
    async def save(self, workflow):
        await asyncio.sleep(0.02)
        return await super().save(workflow)


@pytest.mark.asyncio
async def test_source_handoff_create_is_concurrently_idempotent_and_detects_conflicts() -> None:
    repository = _SlowWorkflowRepository()
    orchestrator = WorkflowOrchestrator(
        repository=repository,
        creation_agents=[],
        production_agents=[],
    )
    payload = WorkflowInput(
        topic="來源題目",
        goal="education",
        platforms=["youtube"],
        output_type="short_video",
        workspace_id="team-a",
        source_generation_id="generation-one",
        source_opportunity_id="opportunity-one",
    )

    first, second = await asyncio.gather(
        orchestrator.create(payload),
        orchestrator.create(payload.model_copy(deep=True)),
    )
    assert first.id == second.id
    assert len(await repository.list()) == 1

    with pytest.raises(InvalidWorkflowTransitionError):
        await orchestrator.create(payload.model_copy(update={"topic": "不同題目"}))
