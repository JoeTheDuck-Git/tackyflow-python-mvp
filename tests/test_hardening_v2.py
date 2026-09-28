from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.api.workspace as workspace_module
from app.api.workspace import resolve_actor_id, resolve_workspace_id
from app.domain.models import OpportunityRequest, ReferenceMaterial, WorkflowInput


def test_workflow_request_enforces_platform_output_and_constraint_limits() -> None:
    with pytest.raises(ValidationError):
        WorkflowInput(topic="不支援平台", goal="education", platforms=["unknown"], output_type="short_video")
    with pytest.raises(ValidationError):
        WorkflowInput(topic="不支援格式", goal="education", platforms=["youtube"], output_type="other")
    with pytest.raises(ValidationError):
        WorkflowInput(
            topic="過多限制",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            constraints=[str(index) for index in range(21)],
        )


def test_reference_urls_only_allow_http_and_https() -> None:
    with pytest.raises(ValidationError):
        ReferenceMaterial(name="本機檔案", source_url="file:///etc/passwd")
    with pytest.raises(ValidationError):
        OpportunityRequest(topic="網址限制", reference_urls=["javascript:alert(1)"])


@pytest.mark.asyncio
async def test_trusted_proxy_auth_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_module,
        "settings",
        SimpleNamespace(auth_mode="trusted_proxy", auth_proxy_secret="shared-secret"),
    )
    with pytest.raises(HTTPException) as missing_secret:
        await resolve_workspace_id(None, "tenant-a", None)
    assert missing_secret.value.status_code == 401

    assert await resolve_workspace_id(None, "tenant-a", "shared-secret") == "tenant-a"
    assert await resolve_actor_id("user-123") == "user-123"

    with pytest.raises(HTTPException) as missing_actor:
        await resolve_actor_id(None)
    assert missing_actor.value.status_code == 401
