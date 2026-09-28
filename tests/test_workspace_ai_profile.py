from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workspace_ai import build_workspace_ai_router
from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext
from app.prompts.repository import PLATFORM_PROMPT_SCOPE, SQLitePromptRepository
from app.prompts.runtime import owner_prompt_suffix
from app.prompts.workspace_profile import SQLiteWorkspaceAIProfileRepository
import app.prompts.runtime as prompt_runtime


def _context(role: str, workspace_id: str = "workspace-a") -> SessionContext:
    return SessionContext(
        session_id=f"session-{role}",
        user_id=f"user-{role}",
        email=f"{role}@example.com",
        display_name=role.title(),
        workspace_id=workspace_id,
        workspace_name="測試工作區",
        role=role,
        csrf_token="csrf",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_workspace_ai_profile_is_workspace_scoped_and_admin_editable(tmp_path: Path) -> None:
    repository = SQLiteWorkspaceAIProfileRepository(tmp_path / "profiles.db")
    app = FastAPI()
    app.include_router(build_workspace_ai_router(repository))
    current = {"context": _context("admin")}

    async def override_context() -> SessionContext:
        return current["context"]

    app.dependency_overrides[resolve_session_context] = override_context
    client = TestClient(app)
    saved = client.put(
        "/api/v1/workspace-ai-profile",
        json={
            "brand_name": "品牌 A",
            "brand_positioning": "協助創作者理解複雜科技",
            "target_audience": "初次購買相機的使用者",
            "tone": "專業、直接、自然台灣口語",
            "preferred_vocabulary": ["實際使用"],
            "forbidden_phrases": ["業界第一"],
            "default_cta": "邀請留言分享使用情境",
            "script_snippets": ["想了解更多科技行情，記得訂閱我們喔！", "我們下次影片見！"],
            "visual_style": "真實產品畫面優先",
            "content_principles": ["先說情境，再談規格"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["brand_name"] == "品牌 A"
    assert saved.json()["script_snippets"] == ["想了解更多科技行情，記得訂閱我們喔！", "我們下次影片見！"]

    current["context"] = _context("owner", "workspace-b")
    assert client.get("/api/v1/workspace-ai-profile").json()["brand_name"] == ""

    current["context"] = _context("member")
    assert client.get("/api/v1/workspace-ai-profile").status_code == 200
    assert client.put("/api/v1/workspace-ai-profile", json={}).status_code == 403


@pytest.mark.asyncio
async def test_runtime_layers_platform_core_and_workspace_brand_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompt_repository = SQLitePromptRepository(tmp_path / "prompts.db")
    profile_repository = SQLiteWorkspaceAIProfileRepository(tmp_path / "profiles.db")
    version = await prompt_repository.create_version(
        PLATFORM_PROMPT_SCOPE,
        "workflow_writer",
        "核心規則：逐字稿必須有清楚證據邊界。",
        "平台核心",
        "platform-owner",
    )
    await prompt_repository.activate(PLATFORM_PROMPT_SCOPE, "workflow_writer", version["version"])
    await profile_repository.upsert(
        "workspace-a",
        {"tone": "溫暖而直接", "forbidden_phrases": ["保證有效"], "default_cta": "邀請分享經驗"},
        "workspace-admin",
    )
    monkeypatch.setattr(prompt_runtime, "_repository", prompt_repository)
    monkeypatch.setattr(prompt_runtime, "_workspace_profile_repository", profile_repository)

    combined = owner_prompt_suffix("workspace-a", "workflow_writer")
    assert "核心規則：逐字稿必須有清楚證據邊界" in combined
    assert "語氣與調性：溫暖而直接" in combined
    assert "禁用詞：保證有效" in combined
    assert "不能覆蓋平台核心 Prompt" in combined


def test_workspace_brand_settings_frontend_is_available() -> None:
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'id="workspaceAIProfilePanel"' in html
    assert "Workspace 品牌 AI 設定" in html
    assert "/api/v1/workspace-ai-profile" in script
    assert "之後的新 AI 任務會疊加在平台核心 Prompt 上" in script
    assert 'id="workspaceScriptSnippetInput"' in html
    assert 'id="addWorkspaceScriptSnippet"' in html
    assert 'id="workspaceScriptSnippetList"' in html
    assert 'data-script-snippet=' in script
    assert "editor.setRangeText" in script
    assert "renderWorkspaceScriptSnippets" in script
    assert "data-workspace-snippet-remove" in script
    assert 'id="openScriptSnippetEditorButton"' in script
    assert 'id="configureScriptSnippetsButton"' in script
    assert "function formatTimestamp(value)" in script
