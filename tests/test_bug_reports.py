import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.bug_reports import build_bug_report_router
from app.api.workspace import resolve_actor_id, resolve_workspace_id
from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext
from app.usage.repository import SQLiteUsageRepository


class FakeWorkflowRepository:
    async def get(self, workflow_id: str, workspace_id: str):
        return {"id": workflow_id, "workspace_id": workspace_id}


class FakeScreenshotStorage:
    def __init__(self) -> None:
        self.objects = {}

    def key(self, workspace_id: str, report_id: str, extension: str) -> str:
        return f"{workspace_id}/{report_id}.{extension}"

    async def write(self, key: str, content: bytes, mime_type: str) -> None:
        self.objects[key] = content

    async def read(self, key: str) -> bytes:
        return self.objects[key]


def context(email: str, role: str = "member") -> SessionContext:
    return SessionContext(
        session_id="session", user_id="user-a", email=email, display_name="Tester",
        workspace_id="workspace-a", workspace_name="Workspace A", role=role,
        csrf_token="csrf", expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def test_bug_report_submission_and_platform_owner_access(tmp_path: Path) -> None:
    repository = SQLiteUsageRepository(tmp_path / "bug-reports.db")
    storage = FakeScreenshotStorage()
    app = FastAPI()
    app.include_router(build_bug_report_router(repository, FakeWorkflowRepository(), storage, platform_owner_emails={"owner@example.com"}))
    current = {"context": context("tester@example.com")}

    async def override_context(): return current["context"]
    async def override_workspace(): return current["context"].workspace_id
    async def override_actor(): return current["context"].user_id

    app.dependency_overrides[resolve_session_context] = override_context
    app.dependency_overrides[resolve_workspace_id] = override_workspace
    app.dependency_overrides[resolve_actor_id] = override_actor
    client = TestClient(app)

    assert client.post("/api/v1/bug-reports", json={}).status_code == 422
    png = b"\x89PNG\r\n\x1a\n" + b"test-image"
    created = client.post("/api/v1/bug-reports", json={
        "category": "interface", "severity": "medium", "description": "按鈕沒有反應",
        "page": "assets", "context": {"viewport": "1280x800"},
        "screenshot_base64": base64.b64encode(png).decode(), "screenshot_name": "bug.png", "screenshot_mime": "image/png",
    })
    assert created.status_code == 201
    report_id = created.json()["id"]
    assert client.get("/api/v1/admin/bug-reports").status_code == 403

    current["context"] = context("owner@example.com", "owner")
    reports = client.get("/api/v1/admin/bug-reports")
    assert reports.status_code == 200
    assert reports.json()[0]["description"] == "按鈕沒有反應"
    assert reports.json()[0]["has_screenshot"] is True
    screenshot = client.get(f"/api/v1/admin/bug-reports/{report_id}/screenshot")
    assert screenshot.status_code == 200 and screenshot.content == png
    updated = client.patch(f"/api/v1/admin/bug-reports/{report_id}", json={"status": "resolved", "owner_note": "已修正"})
    assert updated.status_code == 200
    assert client.get("/api/v1/admin/bug-reports?status=resolved").json()[0]["owner_note"] == "已修正"


def test_screenshot_type_is_verified(tmp_path: Path) -> None:
    repository = SQLiteUsageRepository(tmp_path / "invalid-image.db")
    app = FastAPI()
    app.include_router(build_bug_report_router(repository, FakeWorkflowRepository(), FakeScreenshotStorage(), platform_owner_emails={"owner@example.com"}))
    app.dependency_overrides[resolve_workspace_id] = lambda: "workspace-a"
    app.dependency_overrides[resolve_actor_id] = lambda: "user-a"
    client = TestClient(app)
    response = client.post("/api/v1/bug-reports", json={
        "screenshot_base64": base64.b64encode(b"not-a-real-image").decode(),
        "screenshot_name": "fake.png", "screenshot_mime": "image/png",
    })
    assert response.status_code == 422


def test_bug_report_ui_includes_direct_screen_capture() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "app/static/index.html").read_text()
    javascript = (root / "app/static/app.js").read_text()
    assert 'id="captureBugScreenshot"' in html
    assert "navigator.mediaDevices.getDisplayMedia" in javascript
    assert "bugCapturedScreenshot ||" in javascript
    for tool in ("crop", "rect", "ellipse", "arrow", "pen", "mosaic", "text", "emoji"):
        assert f'data-bug-editor-tool="{tool}"' in html
    for emoji in ("⚠️", "❌", "✅", "👀", "👉", "😕", "🐛", "💥", "🔍"):
        assert f'data-bug-editor-emoji="{emoji}"' in html
    assert "selectBugEditorEmoji" in javascript
    assert 'state.tool === "emoji" ? state.selectedEmoji' in javascript
    assert "confirmBugScreenshotEditor" in javascript
    assert "exportBugEditorBlob" in javascript
