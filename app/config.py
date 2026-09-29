from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Local development has one project-scoped source of truth. Existing process
# environment values always win, so Vercel/container settings are never
# silently replaced by the local file.
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Content Workflow Python")
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    platform_owner_emails: tuple[str, ...] = tuple(
        email.strip().casefold()
        for email in os.getenv("PLATFORM_OWNER_EMAILS", "").split(",")
        if email.strip()
    )
    database_path: Path = Path(
        os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "workflows.db"))
    )
    database_url: str = os.getenv("DATABASE_URL", "")
    supabase_url: str = os.getenv("NEXT_PUBLIC_SUPABASE_URL", os.getenv("SUPABASE_URL", ""))
    supabase_anon_key: str = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", os.getenv("SUPABASE_ANON_KEY", ""))
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    supabase_storage_bucket: str = os.getenv("SUPABASE_STORAGE_BUCKET", "generated-assets")
    opportunity_provider: str = os.getenv("OPPORTUNITY_PROVIDER", "local_rule")
    opportunity_pipeline_mode: str = os.getenv(
        "OPPORTUNITY_PIPELINE_MODE", "legacy"
    )
    opportunity_knowledge_rollout_percent: int = int(
        os.getenv("OPPORTUNITY_KNOWLEDGE_ROLLOUT_PERCENT", "0")
    )
    opportunity_signal_provider: str = os.getenv(
        "OPPORTUNITY_SIGNAL_PROVIDER", "local_manual"
    )
    auth_mode: str = os.getenv("AUTH_MODE", "session")
    auth_proxy_secret: str = os.getenv("AUTH_PROXY_SECRET", "")
    session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "12"))
    session_idle_minutes: int = int(os.getenv("SESSION_IDLE_MINUTES", "120"))
    password_reset_redirect_url: str = os.getenv(
        "PASSWORD_RESET_REDIRECT_URL",
        "http://127.0.0.1:8000/?auth=recovery" if os.getenv("APP_ENV", "development") != "production" else "",
    )
    signup_verify_redirect_url: str = os.getenv(
        "SIGNUP_VERIFY_REDIRECT_URL",
        "http://127.0.0.1:8000/?auth=verified" if os.getenv("APP_ENV", "development") != "production" else "",
    )
    content_provider: str = os.getenv("CONTENT_PROVIDER", "local_rule")
    workflow_agent_provider: str = os.getenv("WORKFLOW_AGENT_PROVIDER", "local_rule")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6-sol")
    openai_timeout_seconds: float = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "90"))
    openai_production_timeout_seconds: float = float(
        os.getenv("OPENAI_PRODUCTION_TIMEOUT_SECONDS", "180")
    )
    prompt_test_input_usd_per_million: float = float(
        os.getenv("PROMPT_TEST_INPUT_USD_PER_1M", "0")
    )
    prompt_test_output_usd_per_million: float = float(
        os.getenv("PROMPT_TEST_OUTPUT_USD_PER_1M", "0")
    )
    openai_embedding_model: str = os.getenv(
        "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    knowledge_crawler: str = os.getenv("KNOWLEDGE_CRAWLER", "auto")
    apply_ai_migrations: bool = os.getenv(
        "APPLY_AI_MIGRATIONS", "false"
    ).casefold() == "true"
    openai_image_model: str = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-flare")
    openai_image_timeout_seconds: float = float(os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "150"))
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_timeout_seconds: float = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "120"))
    gemini_youtube_max_candidates: int = int(os.getenv("GEMINI_YOUTUBE_MAX_CANDIDATES", "4"))
    generated_assets_path: Path = Path(
        os.getenv("GENERATED_ASSETS_PATH", str(PROJECT_ROOT / "data" / "generated"))
    )
    daily_generation_limit: int = int(os.getenv("DAILY_GENERATION_LIMIT", "30"))
    requests_per_minute: int = int(os.getenv("REQUESTS_PER_MINUTE", "300"))


settings = Settings()

if settings.app_env == "production" and not settings.database_url:
    raise RuntimeError("DATABASE_URL is required in production; SQLite is test/local-only")
