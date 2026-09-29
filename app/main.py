import os
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.staticfiles import StaticFiles

from app.agents.openai_workflow import build_workflow_agents
from app.agents.opportunity import (
    LocalOpportunityAgent,
    MeteredOpportunityAgent,
    build_opportunity_agent,
    build_signal_provider,
)
from app.agents.opportunity_graph import KnowledgeOpportunityAgent
from app.api.routes import build_router
from app.api.opportunities import build_opportunity_router
from app.api.usage import build_usage_router
from app.api.auth import build_auth_router
from app.api.prompts import build_prompt_router
from app.api.workspace_ai import build_workspace_ai_router
from app.api.knowledge import build_knowledge_router
from app.api.documents import build_document_router
from app.api.bug_reports import build_bug_report_router
from app.auth.dependencies import get_auth_repository
from app.config import settings
from app.llm.provider import MeteredContentProvider, build_content_provider
from app.knowledge import build_knowledge_service
from app.media.generator import OpenAIImageGenerator, SupabaseImageGenerator
from app.opportunities.repository import build_opportunity_repository
from app.prompts.repository import build_prompt_repository
from app.prompts.test_repository import build_prompt_test_repository
from app.prompts.testing import OpenAIPromptTestRunner
from app.prompts.runtime import configure_prompt_runtime
from app.prompts.workspace_profile import build_workspace_ai_profile_repository
from app.usage.limits import RequestRateLimitMiddleware
from app.usage.repository import build_usage_repository
from app.usage.bug_storage import BugScreenshotStorage
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.repository import build_workflow_repository


repository = build_workflow_repository(
    database_url=settings.database_url,
    database_path=settings.database_path,
)
opportunity_repository = build_opportunity_repository(database_url=settings.database_url, database_path=settings.database_path)
usage_repository = build_usage_repository(database_url=settings.database_url, database_path=settings.database_path)
bug_screenshot_storage = BugScreenshotStorage(
    root=settings.generated_assets_path,
    supabase_url=settings.supabase_url if settings.database_url else "",
    service_role_key=settings.supabase_service_role_key if settings.database_url else "",
    bucket=settings.supabase_storage_bucket,
)
auth_repository = get_auth_repository()
prompt_repository = build_prompt_repository(database_url=settings.database_url, database_path=settings.database_path)
prompt_test_repository = build_prompt_test_repository(database_url=settings.database_url, database_path=settings.database_path)
workspace_ai_profile_repository = build_workspace_ai_profile_repository(
    database_url=settings.database_url,
    database_path=settings.database_path,
)
prompt_test_runner = OpenAIPromptTestRunner(
    model=settings.openai_model,
    timeout_seconds=settings.openai_timeout_seconds,
    input_usd_per_million=settings.prompt_test_input_usd_per_million,
    output_usd_per_million=settings.prompt_test_output_usd_per_million,
)
configure_prompt_runtime(prompt_repository, workspace_ai_profile_repository)
knowledge_service = build_knowledge_service(
    settings.database_url,
    embedding_model=settings.openai_embedding_model,
    crawler_mode=settings.knowledge_crawler,
)
base_opportunity_agent = build_opportunity_agent(
    settings.opportunity_provider,
    model=settings.openai_model,
    timeout_seconds=settings.openai_timeout_seconds,
)
opportunity_agent = MeteredOpportunityAgent(
    KnowledgeOpportunityAgent(
        base_opportunity_agent,
        LocalOpportunityAgent(),
        knowledge_service,
        mode=settings.opportunity_pipeline_mode,
        rollout_percent=settings.opportunity_knowledge_rollout_percent,
    ),
    usage_repository,
    daily_limit=settings.daily_generation_limit,
)
opportunity_signal_provider = build_signal_provider(
    settings.opportunity_signal_provider
)
content_provider = MeteredContentProvider(
    build_content_provider(
        settings.content_provider,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
    ),
    usage_repository,
    daily_limit=settings.daily_generation_limit,
)
creation_agents, editorial_agents, production_agents, visual_agents = build_workflow_agents(
    settings.workflow_agent_provider,
    model=settings.openai_model,
    timeout_seconds=settings.openai_timeout_seconds,
    production_timeout_seconds=settings.openai_production_timeout_seconds,
    usage_repository=usage_repository,
    daily_limit=settings.daily_generation_limit,
    gemini_model=settings.gemini_model,
    gemini_timeout_seconds=settings.gemini_timeout_seconds,
    gemini_youtube_max_candidates=settings.gemini_youtube_max_candidates,
)
image_generator = (
    (SupabaseImageGenerator if settings.database_url else OpenAIImageGenerator)(
        model=settings.openai_image_model,
        timeout_seconds=settings.openai_image_timeout_seconds,
        storage_root=settings.generated_assets_path,
        **({"supabase_url":settings.supabase_url,"service_role_key":settings.supabase_service_role_key,"bucket":settings.supabase_storage_bucket} if settings.database_url else {}),
    )
    if os.getenv("OPENAI_API_KEY")
    else None
)
orchestrator = WorkflowOrchestrator(
    repository=repository,
    creation_agents=creation_agents,
    editorial_agents=editorial_agents,
    production_agents=production_agents,
    visual_agents=visual_agents,
    content_provider=content_provider,
    image_generator=image_generator,
    usage_repository=usage_repository,
    daily_generation_limit=settings.daily_generation_limit,
)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Platform-neutral, human-on-exception content workflow API",
)
app.add_middleware(
    RequestRateLimitMiddleware,
    requests_per_minute=settings.requests_per_minute,
)


@app.on_event("startup")
async def apply_ai_schema() -> None:
    if settings.database_url and settings.apply_ai_migrations:
        from app.db.migrations import apply_migrations

        apply_migrations(settings.database_url)
app.include_router(build_auth_router(auth_repository, usage_repository, settings.platform_owner_emails))
app.include_router(build_workspace_ai_router(workspace_ai_profile_repository))
app.include_router(build_prompt_router(
    prompt_repository,
    prompt_test_repository,
    prompt_test_runner,
    usage_repository,
    settings.daily_generation_limit,
    settings.platform_owner_emails,
))
app.include_router(build_knowledge_router(knowledge_service))
app.include_router(build_document_router())
app.include_router(build_router(orchestrator, opportunity_repository))
app.include_router(
    build_opportunity_router(
        repository,
        agent=opportunity_agent,
        opportunity_repository=opportunity_repository,
        signal_provider=opportunity_signal_provider,
    )
)
app.include_router(
    build_usage_router(
        usage_repository,
        repository,
        daily_generation_limit=settings.daily_generation_limit,
        platform_owner_emails=settings.platform_owner_emails,
    )
)
app.include_router(
    build_bug_report_router(
        usage_repository,
        repository,
        bug_screenshot_storage,
        platform_owner_emails=settings.platform_owner_emails,
    )
)


@app.get("/health", tags=["system"])
async def health(response: Response) -> dict[str, str]:
    database_status = "ok"
    try:
        # Every production repository shares one PostgreSQL pool. A single round
        # trip is enough for the liveness endpoint and keeps the UI heartbeat fast.
        await repository.ping()
    except Exception:
        database_status = "failed"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if database_status == "ok" else "degraded",
        "environment": settings.app_env,
        "database": database_status,
        "workflow_storage": "postgresql" if settings.database_url else "sqlite",
        "auth_provider": "supabase" if settings.database_url else "local_session",
        "opportunity_storage": "postgresql" if settings.database_url else "sqlite",
        "usage_storage": "postgresql" if settings.database_url else "sqlite",
        "prompt_storage": "postgresql" if settings.database_url else "sqlite",
        "media_storage": "supabase" if settings.database_url else "local_disk",
        "opportunity_provider": settings.opportunity_provider,
        "opportunity_model": opportunity_agent.model,
        "opportunity_signal_provider": settings.opportunity_signal_provider,
        "opportunity_pipeline": settings.opportunity_pipeline_mode,
        "opportunity_knowledge_rollout_percent": str(
            settings.opportunity_knowledge_rollout_percent
        ),
        "knowledge_crawler": type(knowledge_service.crawler).__name__,
        "knowledge_index": "llamaindex_pgvector" if settings.database_url else "llamaindex_memory",
        "ai_workflow_owner": "python_langgraph",
        "auth_mode": settings.auth_mode,
        "content_provider": settings.content_provider,
        "content_model": settings.openai_model if settings.content_provider == "openai" else "deterministic-v1",
        "workflow_agent_provider": settings.workflow_agent_provider,
        "workflow_agent_model": settings.openai_model,
        "production_planner_timeout_seconds": str(
            settings.openai_production_timeout_seconds
        ),
        "image_generation": "openai" if image_generator is not None else "disabled",
        "image_model": settings.openai_image_model if image_generator is not None else "",
        "youtube_reference_analysis": "gemini" if os.getenv("GEMINI_API_KEY") else "disabled",
        "youtube_reference_model": settings.gemini_model if os.getenv("GEMINI_API_KEY") else "",
        "daily_generation_limit": str(settings.daily_generation_limit),
        "requests_per_minute": str(settings.requests_per_minute),
    }


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
