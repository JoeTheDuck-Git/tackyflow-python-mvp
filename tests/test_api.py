import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp(prefix="content-workflow-tests-")) / "workflows.db")

from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_frontend_is_served_in_traditional_chinese() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="zh-Hant"' in response.text
    assert "AI 內容工作台" in response.text
    assert "AI 執行紀錄" in response.text
    assert 'id="dashboardPage"' in response.text
    assert 'id="opportunitiesPage"' in response.text
    assert 'id="planPage"' in response.text
    assert 'id="assetsPage"' in response.text
    assert 'id="publishingPage"' in response.text
    assert 'id="opportunityContext"' in response.text


def test_create_and_run_workflow() -> None:
    created = client.post(
        "/api/v1/workflows",
        json={
            "topic": "AI 內容工作流",
            "goal": "education",
            "platforms": ["youtube"],
            "output_type": "short_video",
            "target_word_count": 500,
            "brand_voice": "專業但自然",
        },
    )
    assert created.status_code == 201

    workflow_id = created.json()["id"]
    result = client.post(f"/api/v1/workflows/{workflow_id}/run")
    assert result.status_code == 200
    assert result.json()["stage"] == "approval_publish"
    assert result.json()["status"] == "waiting_for_human"
    assert len(result.json()["artifacts"]["script"]["sections"]) == 5
    assert result.json()["artifacts"]["script"]["target_word_count"] == 500
    assert result.json()["artifacts"]["script"]["word_count"] >= 450
    assert result.json()["artifacts"]["production_package"]["preview_ready"] is True
    assert any(entry["event_type"] == "agent.completed" for entry in result.json()["execution_log"])

    revised = client.patch(
        f"/api/v1/workflows/{workflow_id}/script",
        json={
            "full_text": result.json()["artifacts"]["script"]["full_text"] + "\n\n人工補充段落。",
            "regenerate_production": False,
        },
    )
    assert revised.status_code == 200
    assert revised.json()["artifacts"]["script"]["quality_status"] == "human_edited"
    assert revised.json()["artifacts"]["production_package"]["script_sync_status"] == "kept_after_manual_edit"

    visual_revision = client.patch(
        f"/api/v1/workflows/{workflow_id}/production-plan",
        json={
            "visual_cues": [
                {
                    "id": "manual-api-broll",
                    "shot": 1,
                    "cue_type": "broll",
                    "label": "人工新增操作特寫",
                    "start_seconds": 0,
                    "duration_seconds": 3,
                    "visual_description": "手部操作產品按鍵。",
                    "purpose": "補充操作細節",
                    "search_query": "產品操作特寫",
                    "source": "manual",
                }
            ]
        },
    )
    assert visual_revision.status_code == 200
    visual_body = visual_revision.json()
    assert visual_body["status"] == "waiting_for_human"
    assert visual_body["human_request"]["reason"] == "final_approval"
    assert visual_body["artifacts"]["production_package"]["visual_cues"][0]["label"] == "人工新增操作特寫"
    assert visual_body["artifacts"]["production_package"]["script_sync_status"] == "human_visual_revision"
