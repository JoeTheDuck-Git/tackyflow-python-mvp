import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.opportunities import build_opportunity_router
from app.api.routes import build_router
from app.opportunities.repository import SQLiteOpportunityRepository
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.repository import SQLiteWorkflowRepository


def test_opportunity_handoff_is_idempotent_and_survives_repository_recreation(
    tmp_path,
) -> None:
    """Exercise the complete opportunity-to-workflow persistence boundary."""
    database_path = tmp_path / "workflow.db"
    workflow_repository = SQLiteWorkflowRepository(database_path)
    opportunity_repository = SQLiteOpportunityRepository(database_path)
    orchestrator = WorkflowOrchestrator(
        repository=workflow_repository,
        creation_agents=[],
        production_agents=[],
    )
    app = FastAPI()
    app.include_router(build_router(orchestrator, opportunity_repository))
    app.include_router(
        build_opportunity_router(
            workflow_repository,
            opportunity_repository=opportunity_repository,
        )
    )

    with TestClient(app) as client:
        workspace_headers = {"X-Workspace-ID": "handoff-e2e"}
        generated_response = client.post(
            "/api/v1/opportunities/generate",
            json={
                "topic": "Insta360 新手旅遊拍攝",
                "audience": "第一次購買運動相機的旅遊創作者",
                "goal": "education",
                "platforms": ["youtube", "instagram"],
                "preferred_formats": ["短影音"],
                "brand_voice": "專業但自然",
                "workspace_id": "handoff-e2e",
                "count": 4,
            },
            headers=workspace_headers,
        )
        assert generated_response.status_code == 200
        generation = generated_response.json()
        assert generation["status"] == "completed"
        selected = generation["opportunities"][0]

        saved_response = client.patch(
            f"/api/v1/opportunities/{generation['id']}/items/{selected['id']}",
            json={"status": "saved", "feedback_note": "準備交由工作台製作"},
            headers=workspace_headers,
        )
        assert saved_response.status_code == 200
        saved = next(
            item
            for item in saved_response.json()["opportunities"]
            if item["id"] == selected["id"]
        )
        assert saved["status"] == "saved"

        workflow_payload = {
            "topic": saved["topic"],
            "goal": generation["request"]["goal"],
            "platforms": generation["request"]["platforms"],
            "output_type": "short_video",
            "target_word_count": 600,
            "brand_voice": generation["request"]["brand_voice"],
            "workspace_id": generation["request"]["workspace_id"],
            "source_generation_id": generation["id"],
            "source_opportunity_id": saved["id"],
        }
        first_handoff = client.post(
            "/api/v1/workflows", json=workflow_payload, headers=workspace_headers
        )
        assert first_handoff.status_code == 201
        workflow = first_handoff.json()
        assert workflow["input"]["source_generation_id"] == generation["id"]
        assert workflow["input"]["source_opportunity_id"] == saved["id"]
        assert workflow["input"]["workspace_id"] == "handoff-e2e"

        adopted_response = client.patch(
            f"/api/v1/opportunities/{generation['id']}/items/{selected['id']}",
            json={"status": "adopted", "adopted_workflow_id": workflow["id"]},
            headers=workspace_headers,
        )
        assert adopted_response.status_code == 200
        adopted_generation = adopted_response.json()
        adopted = next(
            item
            for item in adopted_generation["opportunities"]
            if item["id"] == selected["id"]
        )
        assert adopted["status"] == "adopted"
        assert adopted["feedback_note"] == "準備交由工作台製作"
        assert adopted["adopted_workflow_id"] == workflow["id"]

        repeated_handoff = client.post(
            "/api/v1/workflows", json=workflow_payload, headers=workspace_headers
        )
        assert repeated_handoff.status_code == 201
        assert repeated_handoff.json()["id"] == workflow["id"]
        assert len(client.get("/api/v1/workflows", headers=workspace_headers).json()) == 1

    recreated_opportunities = SQLiteOpportunityRepository(database_path)
    recreated_workflows = SQLiteWorkflowRepository(database_path)
    restored_generation = asyncio.run(
        recreated_opportunities.get(generation["id"])
    )
    restored_workflow = asyncio.run(recreated_workflows.get(workflow["id"]))

    restored_item = next(
        item
        for item in restored_generation.opportunities
        if item.id == adopted["id"]
    )
    assert restored_item.status.value == "adopted"
    assert restored_item.feedback_note == "準備交由工作台製作"
    assert restored_item.adopted_workflow_id == workflow["id"]
    assert restored_workflow.input.source_generation_id == generation["id"]
    assert restored_workflow.input.source_opportunity_id == adopted["id"]
    assert len(asyncio.run(recreated_workflows.list())) == 1
