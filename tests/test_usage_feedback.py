from pathlib import Path
import os
import tempfile
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.usage import build_usage_router
from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext
from app.usage.repository import (
    GenerationQuotaExceededError,
    SQLiteUsageRepository,
)


@pytest.mark.asyncio
async def test_generation_quota_is_persistent_and_workspace_scoped(tmp_path: Path) -> None:
    repository = SQLiteUsageRepository(tmp_path / "usage.db")
    first_claim = await repository.claim_generation(
        "workspace-a",
        actor_id="user-a",
        workflow_id="workflow-a",
        provider="openai",
        model="test-model",
        daily_limit=1,
    )
    duplicate_claim = await repository.claim_generation(
        "workspace-a",
        actor_id="workflow-agent:editorial_critic",
        workflow_id="workflow-a",
        provider="openai",
        model="test-model",
        daily_limit=1,
    )
    assert duplicate_claim == first_claim

    with pytest.raises(GenerationQuotaExceededError):
        await repository.claim_generation(
            "workspace-a",
            actor_id="user-a",
            workflow_id="workflow-b",
            provider="openai",
            model="test-model",
            daily_limit=1,
        )

    await repository.claim_generation(
        "workspace-b",
        actor_id="user-b",
        workflow_id="workflow-c",
        provider="openai",
        model="test-model",
        daily_limit=1,
    )
    summary = await repository.usage_summary("workspace-a", 1)
    assert summary["generation"]["used"] == 1
    assert summary["generation"]["remaining"] == 0


@pytest.mark.asyncio
async def test_platform_usage_summary_aggregates_all_workspaces(tmp_path: Path) -> None:
    repository = SQLiteUsageRepository(tmp_path / "platform-usage.db")
    await repository.record_event("workspace-a", "page.viewed", actor_id="user-a", metadata={"page": "assets"})
    await repository.record_event("workspace-b", "generation.failed", actor_id="workflow-agent:writer")

    summary = await repository.platform_usage_summary(30)

    assert summary["totals"]["workspaces"] == 2
    assert summary["totals"]["events"] == 2
    assert summary["totals"]["generation_failed"] == 1
    assert {item["workspace_id"] for item in summary["workspaces"]} == {"workspace-a", "workspace-b"}


def test_platform_beta_usage_is_restricted_to_configured_owner(tmp_path: Path) -> None:
    repository = SQLiteUsageRepository(tmp_path / "owner-usage.db")
    app = FastAPI()
    app.include_router(
        build_usage_router(
            repository,
            object(),
            daily_generation_limit=30,
            platform_owner_emails={"owner@example.com"},
        )
    )
    current = {
        "context": SessionContext(
            session_id="session",
            user_id="owner-user",
            email="owner@example.com",
            display_name="Owner",
            workspace_id="default",
            workspace_name="Default",
            role="owner",
            csrf_token="csrf",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    }

    async def override_context() -> SessionContext:
        return current["context"]

    app.dependency_overrides[resolve_session_context] = override_context
    client = TestClient(app)
    assert client.get("/api/v1/admin/beta-usage").status_code == 200

    current["context"] = SessionContext(
        session_id="session-2",
        user_id="other-user",
        email="other@example.com",
        display_name="Other",
        workspace_id="default",
        workspace_name="Default",
        role="owner",
        csrf_token="csrf-2",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert client.get("/api/v1/admin/beta-usage").status_code == 403


def test_feedback_and_usage_api_are_workspace_scoped() -> None:
    os.environ.setdefault(
        "DATABASE_PATH",
        str(Path(tempfile.mkdtemp(prefix="usage-api-tests-")) / "workflows.db"),
    )
    from app.main import app

    client = TestClient(app)

    workspace_id = f"feedback-workspace-{uuid4()}"
    headers = {"X-Workspace-ID": workspace_id}
    created = client.post(
        "/api/v1/workflows",
        headers=headers,
        json={
            "topic": "MVP 回饋測試",
            "goal": "education",
            "platforms": ["youtube"],
            "output_type": "short_video",
            "target_word_count": 300,
        },
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]
    generated = client.post(f"/api/v1/workflows/{workflow_id}/run", headers=headers)
    assert generated.status_code == 200

    feedback = client.post(
        f"/api/v1/workflows/{workflow_id}/feedback",
        headers=headers,
        json={"rating": "helpful", "note": "結構清楚", "artifact_type": "script"},
    )
    assert feedback.status_code == 201
    assert feedback.json()["rating"] == "helpful"

    usage = client.get("/api/v1/usage", headers=headers)
    assert usage.status_code == 200
    assert usage.json()["generation"]["used"] == 1
    assert usage.json()["feedback_count"] == 1

    cross_workspace = client.post(
        f"/api/v1/workflows/{workflow_id}/feedback",
        headers={"X-Workspace-ID": f"other-workspace-{uuid4()}"},
        json={"rating": "needs_improvement", "artifact_type": "script"},
    )
    assert cross_workspace.status_code == 404


def test_client_events_use_an_allowlist() -> None:
    from app.main import app

    client = TestClient(app)

    invalid = client.post(
        "/api/v1/events",
        headers={"X-Workspace-ID": "event-workspace"},
        json={"event_name": "arbitrary.secret.event", "metadata": {}},
    )
    assert invalid.status_code == 422
    accepted = client.post(
        "/api/v1/events",
        headers={"X-Workspace-ID": "event-workspace"},
        json={"event_name": "artifact.previewed", "metadata": {"artifact_type": "broll"}},
    )
    assert accepted.status_code == 202
