from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.prompts import build_prompt_router
from app.auth.dependencies import resolve_session_context
from app.auth.repository import SessionContext
from app.prompts.repository import PROMPT_CATALOG, SQLitePromptRepository
from app.prompts.test_repository import SQLitePromptTestRepository
from app.prompts.testing import OpenAIPromptTestRunner, PromptComparisonJudgment


def context(role: str, *, workspace_id: str = "workspace-a", email: str | None = None) -> SessionContext:
    return SessionContext(
        session_id=f"session-{role}",
        user_id=f"user-{role}",
        email=email or f"{role}@example.com",
        display_name=role.title(),
        workspace_id=workspace_id,
        workspace_name="測試工作區",
        role=role,
        csrf_token="csrf",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_prompt_versions_are_workspace_scoped_and_activatable(tmp_path: Path) -> None:
    repository = SQLitePromptRepository(tmp_path / "prompts.db")
    first = await repository.create_version(
        "workspace-a", "workflow_writer", "使用短句與實測證據。", "第一版", "owner-a"
    )
    second = await repository.create_version(
        "workspace-a", "workflow_writer", "先講結論，再補充證據。", "第二版", "owner-a"
    )
    await repository.activate("workspace-a", "workflow_writer", first["version"])

    assert await repository.active_instruction("workspace-a", "workflow_writer") == "使用短句與實測證據。"
    assert len(await repository.list_versions("workspace-a", "workflow_writer")) == 2
    assert await repository.list_versions("workspace-b", "workflow_writer") == []

    await repository.activate("workspace-a", "workflow_writer", second["version"])
    versions = await repository.list_versions("workspace-a", "workflow_writer")
    assert sum(bool(item["activated_at"]) for item in versions) == 1
    assert await repository.active_instruction("workspace-a", "workflow_writer") == "先講結論，再補充證據。"


@pytest.mark.asyncio
async def test_legacy_default_prompt_versions_are_promoted_to_platform_scope(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-prompts.db"
    repository = SQLitePromptRepository(database_path)
    await repository.create_version("default", "workflow_writer", "舊核心版本", "legacy", "owner")

    migrated = SQLitePromptRepository(database_path)
    assert (await migrated.list_versions("__platform_core__", "workflow_writer"))[0]["instructions"] == "舊核心版本"
    assert await migrated.list_versions("default", "workflow_writer") == []


def test_prompt_api_is_platform_owner_only(tmp_path: Path) -> None:
    repository = SQLitePromptRepository(tmp_path / "prompt-api.db")
    app = FastAPI()
    app.include_router(build_prompt_router(repository, platform_owner_emails={"owner@example.com"}))

    current = {"context": context("owner")}

    async def override_context() -> SessionContext:
        return current["context"]

    app.dependency_overrides[resolve_session_context] = override_context
    client = TestClient(app)

    assert client.get("/api/v1/prompts").status_code == 200
    created = client.post(
        "/api/v1/prompts/workflow_writer/versions",
        json={"instructions": "使用清楚的繁體中文。", "change_note": "測試版"},
    )
    assert created.status_code == 201
    assert client.post("/api/v1/prompts/workflow_writer/versions/1/activate").status_code == 200

    for role in ("admin", "member"):
        current["context"] = context(role)
        assert client.get("/api/v1/prompts").status_code == 403
        assert client.get("/api/v1/prompts/workflow_writer/versions").status_code == 403
        assert client.post(
            "/api/v1/prompts/workflow_writer/versions",
            json={"instructions": "不應成功", "change_note": ""},
        ).status_code == 403
    current["context"] = context("owner", email="another-owner@example.com")
    assert client.get("/api/v1/prompts").status_code == 403


def test_prompt_center_frontend_has_owner_gate() -> None:
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert 'workspace?.role === "owner"' in script
    assert 'id="promptCenterPanel"' in html
    assert "平台 Owner 專屬" in html
    assert "Prompt 測試台" in script
    assert "載入精細模板" in script
    assert "同時執行 A / B 測試" in script
    assert "人工偏好" in script


def test_prompt_catalog_defaults_are_detailed_agent_specific_templates() -> None:
    assert len(PROMPT_CATALOG) == 10
    defaults = {item["key"]: item["default"] for item in PROMPT_CATALOG}
    assert all("# " in prompt for prompt in defaults.values())
    assert all(len(prompt) >= 180 for prompt in defaults.values())
    assert "evidence_needed" in defaults["opportunity_strategy"]
    assert "目標字數 ±10%" in defaults["workflow_writer"]
    assert "timing_rationale" in defaults["production_planner"]
    assert "ready_for_human_approval" in defaults["publishing_preflight"]
    assert len(set(defaults.values())) == len(defaults)


class FakePromptTestRunner:
    async def run(self, **kwargs):
        assert kwargs["instructions_a"] == "版本一指示"
        assert kwargs["instructions_b"] == "版本二指示"
        return {
            "result_a": {
                "output": "A 產出",
                "latency_ms": 120,
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                "estimated_cost_usd": 0.0001,
                "model": "test-model",
            },
            "result_b": {
                "output": "B 產出",
                "latency_ms": 90,
                "usage": {"input_tokens": 10, "output_tokens": 15, "total_tokens": 25},
                "estimated_cost_usd": 0.00008,
                "model": "test-model",
            },
            "judgment": {
                "score_a": 82,
                "score_b": 91,
                "winner": "b",
                "summary": "版本 B 較完整。",
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
                "evaluation_type": "llm_judge",
            },
        }


def test_prompt_test_bench_persists_cases_runs_and_human_preference(tmp_path: Path) -> None:
    prompt_repository = SQLitePromptRepository(tmp_path / "prompts.db")
    test_repository = SQLitePromptTestRepository(tmp_path / "prompt-tests.db")
    app = FastAPI()
    app.include_router(build_prompt_router(prompt_repository, test_repository, FakePromptTestRunner()))

    current = {"context": context("owner")}

    async def override_context() -> SessionContext:
        return current["context"]

    app.dependency_overrides[resolve_session_context] = override_context
    client = TestClient(app)
    for instructions in ("版本一指示", "版本二指示"):
        assert client.post(
            "/api/v1/prompts/workflow_writer/versions",
            json={"instructions": instructions, "change_note": "測試"},
        ).status_code == 201

    created_case = client.post(
        "/api/v1/prompts/workflow_writer/test-cases",
        json={
            "name": "固定開箱案例",
            "input_text": "請替 DJI Osmo 360 建立一份完整且不虛構的開箱逐字稿。",
            "rubric": "使用繁體中文，不得虛構實測，並符合完整逐字稿結構。",
        },
    )
    assert created_case.status_code == 201
    case_id = created_case.json()["id"]

    run = client.post(
        "/api/v1/prompts/workflow_writer/test-runs",
        json={"case_id": case_id, "version_a": 1, "version_b": 2},
    )
    assert run.status_code == 201
    assert run.json()["result"]["judgment"]["score_b"] == 91
    run_id = run.json()["id"]

    preferred = client.patch(
        f"/api/v1/prompts/test-runs/{run_id}/preference",
        json={"preference": "b", "note": "結構較完整"},
    )
    assert preferred.status_code == 200
    assert preferred.json()["preference"] == "b"
    assert len(client.get("/api/v1/prompts/workflow_writer/test-runs").json()) == 1

    current["context"] = context("owner", workspace_id="workspace-b")
    assert len(client.get("/api/v1/prompts/workflow_writer/test-cases").json()) == 1
    assert len(client.get("/api/v1/prompts/workflow_writer/test-runs").json()) == 1

    current["context"] = context("member")
    assert client.get("/api/v1/prompts/workflow_writer/test-cases").status_code == 403


class FakePromptUsage:
    def model_dump(self, mode="json"):
        return {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}


class FakePromptResponses:
    def create(self, **kwargs):
        instructions = kwargs["input"][0]["content"]
        output = "版本二的完整產出" if "版本二" in instructions else "版本一的完整產出"
        return SimpleNamespace(output_text=output, usage=FakePromptUsage(), model="test-model", id="generation")

    def parse(self, **kwargs):
        return SimpleNamespace(
            output_parsed=PromptComparisonJudgment(
                score_a=80,
                score_b=92,
                winner="b",
                summary="B 更符合驗收規準。",
                strengths_a=["簡潔"],
                strengths_b=["完整"],
            ),
            usage=FakePromptUsage(),
            model="test-model",
            id="judge",
        )


@pytest.mark.asyncio
async def test_openai_prompt_runner_compares_usage_cost_latency_and_quality() -> None:
    responses = FakePromptResponses()
    runner = OpenAIPromptTestRunner(
        model="test-model",
        timeout_seconds=30,
        input_usd_per_million=2,
        output_usd_per_million=8,
        client_factory=lambda: SimpleNamespace(responses=responses),
    )
    result = await runner.run(
        prompt={"name": "撰寫代理", "description": "產生逐字稿", "locked": "不得虛構"},
        instructions_a="版本一指示",
        instructions_b="版本二指示",
        test_input="DJI Osmo 360 開箱評測固定 Brief",
        rubric="必須使用繁體中文，而且不得虛構實測結論。",
    )

    assert result["result_a"]["output"] == "版本一的完整產出"
    assert result["result_b"]["output"] == "版本二的完整產出"
    assert result["result_a"]["usage"]["total_tokens"] == 150
    assert result["result_a"]["estimated_cost_usd"] == pytest.approx(0.0006)
    assert result["result_a"]["latency_ms"] >= 0
    assert result["judgment"]["winner"] == "b"
    assert result["judgment"]["score_b"] == 92
