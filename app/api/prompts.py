from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext
from app.prompts.repository import PLATFORM_PROMPT_SCOPE, PromptNotFoundError
from app.prompts.test_repository import PromptTestNotFoundError
from app.usage.repository import GenerationQuotaExceededError


class PromptVersionRequest(BaseModel):
    instructions: str = Field(min_length=1, max_length=12000)
    change_note: str = Field(default="", max_length=500)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("instructions are required")
        return normalized


class PromptTestCaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    input_text: str = Field(min_length=10, max_length=12000)
    rubric: str = Field(min_length=10, max_length=4000)


class PromptTestRunRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=100)
    version_a: int = Field(ge=1)
    version_b: int = Field(ge=1)


class PromptPreferenceRequest(BaseModel):
    preference: Literal["a", "b", "tie"]
    note: str = Field(default="", max_length=1000)


def _require_owner(context: SessionContext, platform_owner_emails: set[str] | None) -> None:
    if platform_owner_emails is None:
        allowed = context.role == "owner"
    else:
        allowed = context.email.strip().casefold() in platform_owner_emails
    if not allowed:
        raise HTTPException(status_code=403, detail="platform owner access required")


def build_prompt_router(
    repository: Any,
    test_repository: Any | None = None,
    test_runner: Any | None = None,
    usage_repository: Any | None = None,
    daily_generation_limit: int = 30,
    platform_owner_emails: tuple[str, ...] | set[str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])
    allowed_platform_owners = (
        None
        if platform_owner_emails is None
        else {email.strip().casefold() for email in platform_owner_emails if email.strip()}
    )

    @router.get("")
    async def list_prompts(context: SessionContext = Depends(resolve_session_context)) -> list[dict[str, Any]]:
        _require_owner(context, allowed_platform_owners)
        return await repository.list_prompts(PLATFORM_PROMPT_SCOPE)

    @router.patch("/test-runs/{run_id}/preference")
    async def save_test_preference(run_id: str, payload: PromptPreferenceRequest, context: SessionContext = Depends(resolve_session_context)) -> dict[str, Any]:
        _require_owner(context, allowed_platform_owners)
        if test_repository is None:
            raise HTTPException(status_code=503, detail="prompt test repository unavailable")
        try:
            return await test_repository.set_preference(PLATFORM_PROMPT_SCOPE, run_id, payload.preference, payload.note)
        except PromptTestNotFoundError as exc:
            raise HTTPException(status_code=404, detail="prompt test run not found") from exc

    @router.get("/{prompt_key}/test-cases")
    async def list_test_cases(prompt_key: str, context: SessionContext = Depends(resolve_session_context)) -> list[dict[str, Any]]:
        _require_owner(context, allowed_platform_owners)
        if test_repository is None:
            return []
        try:
            await repository.list_versions(PLATFORM_PROMPT_SCOPE, prompt_key)
            return await test_repository.list_cases(PLATFORM_PROMPT_SCOPE, prompt_key)
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="prompt not found") from exc

    @router.post("/{prompt_key}/test-cases", status_code=status.HTTP_201_CREATED)
    async def create_test_case(prompt_key: str, payload: PromptTestCaseRequest, context: SessionContext = Depends(resolve_session_context)) -> dict[str, Any]:
        _require_owner(context, allowed_platform_owners)
        if test_repository is None:
            raise HTTPException(status_code=503, detail="prompt test repository unavailable")
        try:
            await repository.list_versions(PLATFORM_PROMPT_SCOPE, prompt_key)
            return await test_repository.create_case(
                PLATFORM_PROMPT_SCOPE, prompt_key, payload.name, payload.input_text, payload.rubric, context.user_id
            )
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="prompt not found") from exc

    @router.get("/{prompt_key}/test-runs")
    async def list_test_runs(prompt_key: str, context: SessionContext = Depends(resolve_session_context)) -> list[dict[str, Any]]:
        _require_owner(context, allowed_platform_owners)
        if test_repository is None:
            return []
        try:
            await repository.list_versions(PLATFORM_PROMPT_SCOPE, prompt_key)
            return await test_repository.list_runs(PLATFORM_PROMPT_SCOPE, prompt_key)
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="prompt not found") from exc

    @router.post("/{prompt_key}/test-runs", status_code=status.HTTP_201_CREATED)
    async def run_prompt_test(prompt_key: str, payload: PromptTestRunRequest, context: SessionContext = Depends(resolve_session_context)) -> dict[str, Any]:
        _require_owner(context, allowed_platform_owners)
        if test_repository is None or test_runner is None:
            raise HTTPException(status_code=503, detail="prompt test runner unavailable")
        if payload.version_a == payload.version_b:
            raise HTTPException(status_code=422, detail="select two different prompt versions")
        try:
            prompts = await repository.list_prompts(PLATFORM_PROMPT_SCOPE)
            prompt = next((item for item in prompts if item["key"] == prompt_key), None)
            if prompt is None:
                raise PromptNotFoundError(prompt_key)
            versions = await repository.list_versions(PLATFORM_PROMPT_SCOPE, prompt_key)
            by_version = {item["version"]: item for item in versions}
            if payload.version_a not in by_version or payload.version_b not in by_version:
                raise PromptNotFoundError(prompt_key)
            test_case = await test_repository.get_case(PLATFORM_PROMPT_SCOPE, prompt_key, payload.case_id)
            if usage_repository is not None:
                summary = await usage_repository.usage_summary(context.workspace_id, daily_generation_limit)
                if summary["generation"]["remaining"] < 3:
                    raise HTTPException(status_code=429, detail="Prompt A/B 測試需要 3 次生成額度")
                request_id = str(uuid4())
                for phase in ("variant-a", "variant-b", "judge"):
                    await usage_repository.claim_generation(
                        context.workspace_id,
                        actor_id=context.user_id,
                        workflow_id=f"prompt-test:{request_id}:{phase}",
                        provider="openai",
                        model=getattr(test_runner, "model", "prompt-test"),
                        daily_limit=daily_generation_limit,
                    )
            result = await test_runner.run(
                prompt=prompt,
                instructions_a=by_version[payload.version_a]["instructions"],
                instructions_b=by_version[payload.version_b]["instructions"],
                test_input=test_case["input_text"],
                rubric=test_case["rubric"],
            )
            return await test_repository.create_run(
                PLATFORM_PROMPT_SCOPE, prompt_key, payload.case_id, payload.version_a, payload.version_b, result, context.user_id
            )
        except (PromptNotFoundError, PromptTestNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="prompt version or test case not found") from exc
        except GenerationQuotaExceededError as exc:
            raise HTTPException(status_code=429, detail="daily generation quota exceeded") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"prompt comparison failed: {exc.__class__.__name__}") from exc

    @router.get("/{prompt_key}/versions")
    async def list_versions(prompt_key: str, context: SessionContext = Depends(resolve_session_context)) -> list[dict[str, Any]]:
        _require_owner(context, allowed_platform_owners)
        try:
            return await repository.list_versions(PLATFORM_PROMPT_SCOPE, prompt_key)
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="prompt not found") from exc

    @router.post("/{prompt_key}/versions", status_code=status.HTTP_201_CREATED)
    async def create_version(prompt_key: str, payload: PromptVersionRequest, context: SessionContext = Depends(resolve_session_context)) -> dict[str, Any]:
        _require_owner(context, allowed_platform_owners)
        try:
            return await repository.create_version(PLATFORM_PROMPT_SCOPE, prompt_key, payload.instructions, payload.change_note, context.user_id)
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="prompt not found") from exc

    @router.post("/{prompt_key}/versions/{version}/activate")
    async def activate_version(prompt_key: str, version: int, context: SessionContext = Depends(resolve_session_context)) -> dict[str, Any]:
        _require_owner(context, allowed_platform_owners)
        try:
            return await repository.activate(PLATFORM_PROMPT_SCOPE, prompt_key, version)
        except PromptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="prompt version not found") from exc

    return router
