"""Run a non-persistent paid smoke test for the complete OpenAI workflow."""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)

from app.agents.openai_workflow import (
    OpenAIConditionalVerificationAgent,
    OpenAIEditorialCriticAgent,
    OpenAIProductionPlannerAgent,
    OpenAIResearchAgent,
)
from app.domain.models import HumanDecision, VisibleStage, WorkflowInput, WorkflowStatus
from app.config import settings
from app.llm.provider import OpenAIContentProvider
from app.workflow.orchestrator import WorkflowOrchestrator
from app.workflow.repository import InMemoryWorkflowRepository


async def main() -> None:
    model = settings.openai_model
    timeout = settings.openai_timeout_seconds
    orchestrator = WorkflowOrchestrator(
        repository=InMemoryWorkflowRepository(),
        creation_agents=[
            OpenAIResearchAgent(model=model, timeout_seconds=timeout),
            OpenAIConditionalVerificationAgent(model=model, timeout_seconds=timeout),
        ],
        editorial_agents=[OpenAIEditorialCriticAgent(model=model, timeout_seconds=timeout)],
        production_agents=[OpenAIProductionPlannerAgent(model=model, timeout_seconds=timeout)],
        content_provider=OpenAIContentProvider(model=model, timeout_seconds=timeout),
    )
    workflow = await orchestrator.create(
        WorkflowInput(
            topic="DJI Osmo 360 新手選購指南",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            target_word_count=220,
            brand_voice="專業、清楚、不誇大",
            target_audience="第一次購買全景相機的繁體中文使用者",
        )
    )
    result = await orchestrator.run_until_gate(workflow.id)
    if (
        result.status == WorkflowStatus.WAITING_FOR_HUMAN
        and result.stage != VisibleStage.APPROVAL_PUBLISH
    ):
        result = await orchestrator.decide(
            workflow.id,
            HumanDecision(approved=True, note="自動化冒煙測試核准繼續"),
        )
        if result.status != WorkflowStatus.WAITING_FOR_HUMAN:
            result = await orchestrator.run_until_gate(workflow.id)

    print(
        json.dumps(
            {
                "stage": result.stage.value,
                "status": result.status.value,
                "agents": [
                    {
                        "name": item.agent,
                        "status": item.status,
                        "simulated": item.is_simulated,
                        "confidence": item.confidence,
                        "provider": item.artifact.get("_runtime", {}).get("provider"),
                    }
                    for item in result.agent_results
                ],
                "script_provider": result.artifacts.get("script", {})
                .get("generation", {})
                .get("provider"),
                "quality_status": result.artifacts.get("script", {}).get("quality_status"),
                "quality_score": result.artifacts.get("script", {}).get("quality_score"),
                "research_sources": len(
                    result.artifacts.get("script", {}).get("research", {}).get("sources", [])
                ),
                "production_provider": result.artifacts.get("production_package", {})
                .get("generation", {})
                .get("provider"),
                "storyboards": len(
                    result.artifacts.get("production_package", {}).get("storyboard", [])
                ),
                "platforms": [
                    item.get("platform")
                    for item in result.artifacts.get("production_package", {}).get(
                        "distribution_kit", []
                    )
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
