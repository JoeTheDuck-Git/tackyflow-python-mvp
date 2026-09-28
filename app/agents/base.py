from typing import Protocol

from app.domain.models import AgentResult, WorkflowRun


class WorkflowAgent(Protocol):
    name: str

    async def execute(self, workflow: WorkflowRun) -> AgentResult: ...

