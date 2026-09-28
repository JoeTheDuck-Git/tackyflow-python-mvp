import asyncio
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.opportunities import build_opportunity_router
from app.api.routes import build_router
from app.opportunities.repository import (
    InMemoryOpportunityRepository,
    SQLiteOpportunityRepository,
)
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.repository import (
    InMemoryWorkflowRepository,
    SQLiteWorkflowRepository,
)


def _app(workflows, opportunities) -> FastAPI:
    orchestrator = WorkflowOrchestrator(
        repository=workflows,
        creation_agents=[],
        production_agents=[],
    )
    app = FastAPI()
    app.include_router(build_router(orchestrator, opportunities))
    app.include_router(
        build_opportunity_router(
            workflows,
            opportunity_repository=opportunities,
        )
    )
    return app


def _create_adopted_handoff(client: TestClient, workspace_id: str = "cleanup"):
    headers = {"X-Workspace-ID": workspace_id}
    generation_response = client.post(
        "/api/v1/opportunities/generate",
        json={
            "topic": "刪除工作流後解除採用",
            "workspace_id": workspace_id,
            "count": 4,
        },
        headers=headers,
    )
    assert generation_response.status_code == 200
    generation = generation_response.json()
    item = generation["opportunities"][0]

    workflow_response = client.post(
        "/api/v1/workflows",
        json={
            "topic": item["topic"],
            "goal": "education",
            "platforms": ["youtube"],
            "output_type": "short_video",
            "workspace_id": workspace_id,
            "source_generation_id": generation["id"],
            "source_opportunity_id": item["id"],
        },
        headers=headers,
    )
    assert workflow_response.status_code == 201
    workflow = workflow_response.json()

    adopted_response = client.patch(
        f"/api/v1/opportunities/{generation['id']}/items/{item['id']}",
        json={
            "status": "adopted",
            "adopted_workflow_id": workflow["id"],
        },
        headers=headers,
    )
    assert adopted_response.status_code == 200
    return headers, generation, item, workflow


def test_delete_workflow_releases_adoption_and_persists_sqlite_cleanup(
    tmp_path,
) -> None:
    database_path = tmp_path / "workflow.db"
    workflows = SQLiteWorkflowRepository(database_path)
    opportunities = SQLiteOpportunityRepository(database_path)

    with TestClient(_app(workflows, opportunities)) as client:
        headers, generation, item, workflow = _create_adopted_handoff(client)

        deleted = client.delete(
            f"/api/v1/workflows/{workflow['id']}", headers=headers
        )
        assert deleted.status_code == 204
        assert (
            client.get(f"/api/v1/workflows/{workflow['id']}", headers=headers).status_code
            == 404
        )

        restored_generation = client.get(
            f"/api/v1/opportunities/{generation['id']}", headers=headers
        ).json()
        restored_item = next(
            candidate
            for candidate in restored_generation["opportunities"]
            if candidate["id"] == item["id"]
        )
        assert restored_item["status"] == "saved"
        assert restored_item["adopted_workflow_id"] is None
        assert any(
            event["action"] == "workflow_adoption_released"
            and event["item_id"] == item["id"]
            for event in restored_generation["events"]
        )

    # Assert the durable SQLite snapshot itself no longer contains the foreign link,
    # then recreate repositories to cover a process restart.
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT payload FROM opportunity_generations WHERE id = ?",
            (generation["id"],),
        ).fetchone()
        assert row is not None
        persisted = json.loads(row[0])
        persisted_item = next(
            candidate
            for candidate in persisted["opportunities"]
            if candidate["id"] == item["id"]
        )
        assert persisted_item["status"] == "saved"
        assert persisted_item["adopted_workflow_id"] is None
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workflows WHERE id = ?", (workflow["id"],)
            ).fetchone()[0]
            == 0
        )

    recreated = SQLiteOpportunityRepository(database_path)
    durable_generation = asyncio.run(recreated.get(generation["id"]))
    durable_item = next(
        candidate
        for candidate in durable_generation.opportunities
        if candidate.id == item["id"]
    )
    assert durable_item.status.value == "saved"
    assert durable_item.adopted_workflow_id is None


class FailingDeleteWorkflowRepository(InMemoryWorkflowRepository):
    async def delete(self, workflow_id: str, workspace_id: str | None = None) -> None:
        raise RuntimeError("simulated workflow storage failure")


def test_delete_failure_compensates_released_adoption() -> None:
    workflows = FailingDeleteWorkflowRepository()
    opportunities = InMemoryOpportunityRepository()
    with TestClient(
        _app(workflows, opportunities), raise_server_exceptions=False
    ) as client:
        headers, generation, item, workflow = _create_adopted_handoff(client)

        failed_delete = client.delete(
            f"/api/v1/workflows/{workflow['id']}", headers=headers
        )
        assert failed_delete.status_code == 500

        # The workflow still exists, so best-effort compensation restores the exact
        # pre-delete opportunity snapshot instead of leaving a partial state change.
        assert (
            client.get(f"/api/v1/workflows/{workflow['id']}", headers=headers).status_code
            == 200
        )
        restored_generation = client.get(
            f"/api/v1/opportunities/{generation['id']}", headers=headers
        ).json()
        restored_item = next(
            candidate
            for candidate in restored_generation["opportunities"]
            if candidate["id"] == item["id"]
        )
        assert restored_item["status"] == "adopted"
        assert restored_item["adopted_workflow_id"] == workflow["id"]
        assert not any(
            event["action"] == "workflow_adoption_released"
            for event in restored_generation["events"]
        )
