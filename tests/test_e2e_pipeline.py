import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.deterministic import DeterministicAgent
from app.api.routes import build_router
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.repository import SQLiteWorkflowRepository


def build_test_client(database_path) -> tuple[TestClient, SQLiteWorkflowRepository]:
    repository = SQLiteWorkflowRepository(database_path)
    orchestrator = WorkflowOrchestrator(
        repository=repository,
        creation_agents=[
            DeterministicAgent("research", "研究與來源整理"),
            DeterministicAgent("verification", "事實與風險驗證"),
            DeterministicAgent("writer", "腳本撰寫"),
            DeterministicAgent("editorial_critic", "品質檢查"),
        ],
        production_agents=[DeterministicAgent("production_planner", "製作規劃")],
    )
    app = FastAPI()
    app.include_router(build_router(orchestrator))
    return TestClient(app), repository


def test_pipeline_advances_every_visible_stage_and_persists(tmp_path) -> None:
    database_path = tmp_path / "e2e-workflows.db"
    client, _ = build_test_client(database_path)
    payload = {
        "topic": "Pipeline E2E 測試",
        "goal": "education",
        "platforms": ["youtube", "instagram"],
        "output_type": "short_video",
        "target_word_count": 500,
        "brand_voice": "專業但自然",
        "reference_materials": [
            {
                "name": "測試品牌 Brief",
                "kind": "brand_brief",
                "content": "語氣清楚，不使用誇大承諾。",
                "focus": "品牌方向",
            }
        ],
    }

    created = client.post("/api/v1/workflows", json=payload)
    assert created.status_code == 201
    workflow_id = created.json()["id"]
    assert created.json()["stage"] == "requirements"
    assert created.json()["status"] == "draft"

    creation_stage = client.post(f"/api/v1/workflows/{workflow_id}/advance")
    assert creation_stage.status_code == 200
    assert creation_stage.json()["stage"] == "ai_creation"
    assert creation_stage.json()["status"] == "running"
    assert creation_stage.json()["artifacts"]["reference_analysis"]["source_count"] == 1

    production_stage = client.post(f"/api/v1/workflows/{workflow_id}/advance")
    assert production_stage.status_code == 200
    assert production_stage.json()["stage"] == "production_package"
    assert production_stage.json()["status"] == "running"
    assert production_stage.json()["artifacts"]["script"]["word_count"] >= 450

    approval_stage = client.post(f"/api/v1/workflows/{workflow_id}/advance")
    assert approval_stage.status_code == 200
    assert approval_stage.json()["stage"] == "approval_publish"
    assert approval_stage.json()["status"] == "waiting_for_human"
    assert approval_stage.json()["artifacts"]["production_package"]["preview_ready"] is True

    completed = client.post(
        f"/api/v1/workflows/{workflow_id}/decisions",
        json={"approved": True, "note": "E2E 核准"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    history = client.get("/api/v1/workflows")
    assert history.status_code == 200
    assert history.json()[0]["id"] == workflow_id
    assert history.json()[0]["status"] == "completed"

    recreated_repository = SQLiteWorkflowRepository(database_path)
    restored = asyncio.run(recreated_repository.get(workflow_id))
    assert restored.status.value == "completed"
    assert restored.artifacts["script"]["reference_analysis"]["source_count"] == 1


def test_pipeline_stage_management_preserves_consistency_and_can_delete(tmp_path) -> None:
    client, _ = build_test_client(tmp_path / "managed-workflows.db")
    created = client.post(
        "/api/v1/workflows",
        json={
            "topic": "可管理的 Pipeline 任務",
            "goal": "education",
            "platforms": ["youtube"],
            "output_type": "short_video",
            "target_word_count": 500,
            "reference_materials": [
                {
                    "name": "品牌規範",
                    "kind": "brand_brief",
                    "content": "語氣清楚且不誇張。",
                    "focus": "品牌方向",
                }
            ],
        },
    )
    workflow_id = created.json()["id"]
    ready_for_approval = client.post(f"/api/v1/workflows/{workflow_id}/run")
    assert ready_for_approval.status_code == 200
    completed = client.post(
        f"/api/v1/workflows/{workflow_id}/decisions",
        json={"approved": True, "note": "先完成再測試退回"},
    )
    assert completed.json()["status"] == "completed"

    moved_back = client.patch(
        f"/api/v1/workflows/{workflow_id}/stage",
        json={"target_stage": "ai_creation", "note": "需要重寫腳本"},
    )
    assert moved_back.status_code == 200
    assert moved_back.json()["stage"] == "ai_creation"
    assert moved_back.json()["status"] == "draft"
    assert "reference_analysis" in moved_back.json()["artifacts"]
    assert "routing_plan" in moved_back.json()["artifacts"]
    assert "script" not in moved_back.json()["artifacts"]
    assert "production_package" not in moved_back.json()["artifacts"]
    assert "human_decisions" not in moved_back.json()["artifacts"]
    assert moved_back.json()["agent_results"] == []
    assert moved_back.json()["execution_log"][-1]["event_type"] == "workflow.stage_changed"

    moved_forward = client.patch(
        f"/api/v1/workflows/{workflow_id}/stage",
        json={"target_stage": "approval_publish", "note": "重跑到核准階段"},
    )
    assert moved_forward.status_code == 200
    assert moved_forward.json()["stage"] == "approval_publish"
    assert moved_forward.json()["status"] == "waiting_for_human"
    assert moved_forward.json()["artifacts"]["script"]["full_text"]
    assert moved_forward.json()["artifacts"]["production_package"]["preview_ready"] is True

    deleted = client.delete(f"/api/v1/workflows/{workflow_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/workflows/{workflow_id}").status_code == 404
    assert client.get("/api/v1/workflows").json() == []


def test_publication_status_requires_approval_and_persists(tmp_path) -> None:
    database_path = tmp_path / "publication-workflows.db"
    client, _ = build_test_client(database_path)
    created = client.post(
        "/api/v1/workflows",
        json={
            "topic": "發布中心 E2E 測試",
            "goal": "education",
            "platforms": ["youtube", "instagram"],
            "output_type": "short_video",
            "target_word_count": 500,
        },
    )
    workflow_id = created.json()["id"]

    blocked = client.patch(
        f"/api/v1/workflows/{workflow_id}/publication",
        json={"status": "scheduled", "scheduled_at": "2026-10-01T10:00:00Z"},
    )
    assert blocked.status_code == 409

    assert client.post(f"/api/v1/workflows/{workflow_id}/run").status_code == 200
    approved = client.post(
        f"/api/v1/workflows/{workflow_id}/decisions",
        json={"approved": True, "note": "核准進入發布中心"},
    )
    assert approved.status_code == 200
    assert approved.json()["artifacts"]["publication"]["status"] == "ready"

    scheduled = client.patch(
        f"/api/v1/workflows/{workflow_id}/publication",
        json={
            "status": "scheduled",
            "scheduled_at": "2026-10-01T10:00:00Z",
            "note": "安排首波發布",
        },
    )
    assert scheduled.status_code == 200
    assert scheduled.json()["artifacts"]["publication"]["status"] == "scheduled"
    assert scheduled.json()["artifacts"]["publication"]["mode"] == "semi_automatic"
    assert scheduled.json()["execution_log"][-1]["event_type"] == "publication.updated"

    recreated_repository = SQLiteWorkflowRepository(database_path)
    restored = asyncio.run(recreated_repository.get(workflow_id))
    assert restored.artifacts["publication"]["status"] == "scheduled"
    assert restored.artifacts["publication"]["scheduled_at"].startswith("2026-10-01T10:00:00")

    published = client.patch(
        f"/api/v1/workflows/{workflow_id}/publication",
        json={
            "status": "published",
            "published_url": "https://www.youtube.com/watch?v=example",
            "target_platform": "youtube",
            "note": "已由平台後台手動發布",
        },
    )
    assert published.status_code == 200
    assert published.json()["artifacts"]["publication"]["status"] == "published"
    assert published.json()["artifacts"]["publication"]["scheduled_at"] is None
    assert published.json()["artifacts"]["publication"]["published_url"].startswith(
        "https://www.youtube.com/watch?v=example"
    )
    assert published.json()["artifacts"]["publication"]["target_platform"] == "youtube"
    assert published.json()["artifacts"]["publication"]["published_at"]

    returned = client.patch(
        f"/api/v1/workflows/{workflow_id}/publication",
        json={"status": "returned", "note": "發布前退回修改"},
    )
    assert returned.status_code == 200
    assert returned.json()["artifacts"]["publication"]["status"] == "returned"
    assert returned.json()["execution_log"][-1]["event_type"] == "publication.updated"


def test_cancelled_workflow_can_be_archived_restored_and_reopened(tmp_path) -> None:
    client, repository = build_test_client(tmp_path / "returned-workflows.db")
    created = client.post(
        "/api/v1/workflows",
        json={
            "topic": "工作流退回恢復測試",
            "goal": "education",
            "platforms": ["youtube"],
            "output_type": "short_video",
            "target_word_count": 500,
        },
    )
    workflow_id = created.json()["id"]
    ready_for_approval = client.post(f"/api/v1/workflows/{workflow_id}/run")
    assert ready_for_approval.status_code == 200
    assert ready_for_approval.json()["status"] == "waiting_for_human"

    rejected = client.post(
        f"/api/v1/workflows/{workflow_id}/decisions",
        json={"approved": False, "note": "產品規格需要重新確認"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "cancelled"
    assert rejected.json()["artifacts"]["production_package"]["preview_ready"] is True

    archived = client.patch(
        f"/api/v1/workflows/{workflow_id}/archive",
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["artifacts"]["archive"]["archived"] is True
    assert archived.json()["execution_log"][-1]["event_type"] == "workflow.archived"

    restored = client.patch(
        f"/api/v1/workflows/{workflow_id}/archive",
        json={"archived": False},
    )
    assert restored.status_code == 200
    assert restored.json()["artifacts"]["archive"]["archived"] is False
    assert restored.json()["execution_log"][-1]["event_type"] == "workflow.unarchived"

    reopened = client.post(
        f"/api/v1/workflows/{workflow_id}/reopen",
        json={"note": "依最新規格重寫產品段落"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "draft"
    assert reopened.json()["stage"] == "ai_creation"
    assert reopened.json()["last_review_feedback"] == "依最新規格重寫產品段落"
    assert reopened.json()["revision_history"][0]["script"]["full_text"]
    assert reopened.json()["revision_history"][0]["production_package"]["preview_ready"] is True
    assert "script" not in reopened.json()["artifacts"]
    assert "production_package" not in reopened.json()["artifacts"]
    assert "archive" not in reopened.json()["artifacts"]
    assert reopened.json()["execution_log"][-1]["event_type"] == "workflow.reopened"

    rerun = client.post(f"/api/v1/workflows/{workflow_id}/run")
    assert rerun.status_code == 200
    assert rerun.json()["status"] == "waiting_for_human"
    assert rerun.json()["artifacts"]["script"]["full_text"]
    assert rerun.json()["artifacts"]["production_package"]["preview_ready"] is True

    persisted = asyncio.run(repository.get(workflow_id))
    assert persisted.status.value == "waiting_for_human"
    assert len(persisted.revision_history) == 1
