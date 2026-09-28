import os
import subprocess
from pathlib import Path


def test_dedicated_project_env_is_loaded_without_parent_fallback_or_process_override() -> None:
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("OPPORTUNITY_PROVIDER", None)
    environment.pop("ANTHROPIC_API_KEY", None)
    result = subprocess.run(
        [
            str(project_root / ".venv/bin/python"),
            "-c",
            (
                "from app.config import settings; import os; "
                "print(settings.opportunity_provider); "
                "print(f'{settings.app_host}:{settings.app_port}'); "
                "print(bool(os.getenv('OPENAI_API_KEY'))); "
                "print(bool(os.getenv('ANTHROPIC_API_KEY')))"
            ),
        ],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "openai",
        "127.0.0.1:8000",
        "True",
        "False",
    ]

    environment["OPPORTUNITY_PROVIDER"] = "local_rule"
    override = subprocess.run(
        [str(project_root / ".venv/bin/python"), "-c", "from app.config import settings; print(settings.opportunity_provider)"],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert override.stdout.strip() == "local_rule"
