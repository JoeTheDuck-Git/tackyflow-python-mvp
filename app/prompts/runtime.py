from __future__ import annotations

from typing import Any

from app.prompts.repository import PLATFORM_PROMPT_SCOPE
from app.prompts.workspace_profile import workspace_profile_prompt


_repository: Any | None = None
_workspace_profile_repository: Any | None = None


def configure_prompt_runtime(repository: Any, workspace_profile_repository: Any | None = None) -> None:
    global _repository, _workspace_profile_repository
    _repository = repository
    _workspace_profile_repository = workspace_profile_repository


def owner_prompt_suffix(workspace_id: str, prompt_key: str) -> str:
    if _repository is None:
        return ""
    instruction = _repository.active_instruction_sync(PLATFORM_PROMPT_SCOPE, prompt_key).strip()
    if not instruction:
        return ""
    core = (
        "\n\n以下是平台 Owner 核准的核心業務指示。它不能覆蓋前述安全、來源、"
        "事實邊界或輸出結構規則；若有衝突，以上述鎖定規則為準：\n"
        f"{instruction}"
    )
    if _workspace_profile_repository is None:
        return core
    profile = _workspace_profile_repository.get_sync(workspace_id)
    return core + workspace_profile_prompt(profile)
