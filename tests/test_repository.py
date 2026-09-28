import pytest

from app.domain.models import WorkflowInput, WorkflowRun
from app.workflow.repository import SQLiteWorkflowRepository, WorkflowConflictError


@pytest.mark.asyncio
async def test_sqlite_repository_survives_repository_recreation(tmp_path) -> None:
    database_path = tmp_path / "workflows.db"
    workflow = WorkflowRun(
        input=WorkflowInput(
            topic="可持久化的內容任務",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            target_word_count=500,
        )
    )

    first_repository = SQLiteWorkflowRepository(database_path)
    await first_repository.save(workflow)

    recreated_repository = SQLiteWorkflowRepository(database_path)
    restored = await recreated_repository.get(workflow.id)
    history = await recreated_repository.list()

    assert restored.input.topic == "可持久化的內容任務"
    assert history[0].id == workflow.id


@pytest.mark.asyncio
async def test_sqlite_repository_rejects_stale_snapshot(tmp_path) -> None:
    repository = SQLiteWorkflowRepository(tmp_path / "conflict.db")
    workflow = WorkflowRun(
        input=WorkflowInput(
            topic="並行更新測試",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
        )
    )
    await repository.save(workflow)
    first = await repository.get(workflow.id)
    stale = await repository.get(workflow.id)
    first.last_review_feedback = "第一個更新"
    await repository.save(first)
    stale.last_review_feedback = "過期更新"

    with pytest.raises(WorkflowConflictError):
        await repository.save(stale)
