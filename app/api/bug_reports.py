from __future__ import annotations

import base64
import binascii
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.workspace import resolve_actor_id, resolve_workspace_id
from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext
from app.workflow.repository import WorkflowNotFoundError, WorkflowRepository


MIME_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024


class BugReportRequest(BaseModel):
    category: str = Field(default="functional", pattern="^(functional|interface|performance|ai_output|other)$")
    severity: str = Field(default="medium", pattern="^(low|medium|blocking)$")
    description: str = Field(default="", max_length=3000)
    steps_to_reproduce: str = Field(default="", max_length=3000)
    expected_behavior: str = Field(default="", max_length=2000)
    actual_behavior: str = Field(default="", max_length=2000)
    page: str = Field(default="", max_length=100)
    workflow_id: str | None = Field(default=None, max_length=100)
    context: dict[str, str | int | bool | None] = Field(default_factory=dict)
    screenshot_base64: str = Field(default="", max_length=7_100_000)
    screenshot_name: str = Field(default="", max_length=180)
    screenshot_mime: str = Field(default="", max_length=40)

    @field_validator("context")
    @classmethod
    def safe_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 12 or any(len(str(k)) > 50 or len(str(v)) > 300 for k, v in value.items()):
            raise ValueError("context is too large")
        return value

    @model_validator(mode="after")
    def meaningful_report(self) -> "BugReportRequest":
        text = "".join((self.description, self.steps_to_reproduce, self.expected_behavior, self.actual_behavior)).strip()
        if not text and not self.screenshot_base64:
            raise ValueError("description or screenshot is required")
        return self


class BugReportUpdate(BaseModel):
    status: str = Field(pattern="^(new|reviewing|resolved|dismissed)$")
    owner_note: str = Field(default="", max_length=2000)


def _decode_screenshot(payload: BugReportRequest) -> tuple[bytes, str] | None:
    if not payload.screenshot_base64:
        return None
    if payload.screenshot_mime not in MIME_EXTENSIONS:
        raise HTTPException(status_code=422, detail="unsupported screenshot type")
    try:
        content = base64.b64decode(payload.screenshot_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="invalid screenshot") from exc
    if not content or len(content) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(status_code=413, detail="screenshot exceeds 5 MB")
    signatures = {
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
        "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
    }
    if not signatures[payload.screenshot_mime]:
        raise HTTPException(status_code=422, detail="screenshot content does not match its type")
    return content, MIME_EXTENSIONS[payload.screenshot_mime]


def build_bug_report_router(
    usage_repository: Any,
    workflow_repository: WorkflowRepository,
    screenshot_storage: Any,
    *,
    platform_owner_emails: tuple[str, ...] | set[str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["bug-reports"])
    allowed = None if platform_owner_emails is None else {email.strip().casefold() for email in platform_owner_emails if email.strip()}

    def require_platform_owner(context: SessionContext) -> None:
        permitted = context.role == "owner" if allowed is None else context.email.strip().casefold() in allowed
        if not permitted:
            raise HTTPException(status_code=403, detail="platform owner access required")

    @router.post("/bug-reports", status_code=status.HTTP_201_CREATED)
    async def create_bug_report(
        payload: BugReportRequest,
        workspace_id: str = Depends(resolve_workspace_id),
        actor_id: str = Depends(resolve_actor_id),
    ) -> dict[str, Any]:
        if payload.workflow_id:
            try:
                await workflow_repository.get(payload.workflow_id, workspace_id)
            except WorkflowNotFoundError as exc:
                raise HTTPException(status_code=404, detail="resource not found") from exc
        report_id = str(uuid4())
        decoded = _decode_screenshot(payload)
        screenshot_key = None
        if decoded:
            content, extension = decoded
            screenshot_key = screenshot_storage.key(workspace_id, report_id, extension)
            await screenshot_storage.write(screenshot_key, content, payload.screenshot_mime)
        return await usage_repository.create_bug_report(
            workspace_id,
            actor_id=actor_id,
            data={
                "id": report_id, "workflow_id": payload.workflow_id, "category": payload.category,
                "severity": payload.severity, "description": payload.description.strip(),
                "steps_to_reproduce": payload.steps_to_reproduce.strip(),
                "expected_behavior": payload.expected_behavior.strip(), "actual_behavior": payload.actual_behavior.strip(),
                "page": payload.page.strip(), "context": payload.context, "screenshot_key": screenshot_key,
                "screenshot_name": payload.screenshot_name.strip(), "screenshot_mime": payload.screenshot_mime,
            },
        )

    @router.get("/admin/bug-reports")
    async def list_bug_reports(
        report_status: str | None = Query(default=None, alias="status", pattern="^(new|reviewing|resolved|dismissed)$"),
        limit: int = Query(default=100, ge=1, le=250),
        context: SessionContext = Depends(resolve_session_context),
    ) -> list[dict[str, Any]]:
        require_platform_owner(context)
        return await usage_repository.list_bug_reports(limit=limit, report_status=report_status)

    @router.patch("/admin/bug-reports/{report_id}")
    async def update_bug_report(report_id: str, payload: BugReportUpdate, context: SessionContext = Depends(resolve_session_context)) -> dict[str, Any]:
        require_platform_owner(context)
        result = await usage_repository.update_bug_report(report_id, status=payload.status, owner_note=payload.owner_note.strip())
        if result is None:
            raise HTTPException(status_code=404, detail="resource not found")
        return result

    @router.get("/admin/bug-reports/{report_id}/screenshot")
    async def get_bug_report_screenshot(report_id: str, context: SessionContext = Depends(resolve_session_context)) -> Response:
        require_platform_owner(context)
        record = await usage_repository.get_bug_report_screenshot(report_id)
        if not record:
            raise HTTPException(status_code=404, detail="resource not found")
        try:
            content = await screenshot_storage.read(record["screenshot_key"])
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        return Response(content=content, media_type=record.get("screenshot_mime") or "application/octet-stream",
                        headers={"Cache-Control": "private, no-store", "Content-Disposition": f'inline; filename="{report_id}"'})

    return router
