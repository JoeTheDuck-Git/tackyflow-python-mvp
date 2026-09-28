import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.opportunity import LocalOpportunityAgent
from app.api.opportunities import build_opportunity_router
from app.domain.models import OpportunityRequest, WorkflowInput, WorkflowRun
from app.workflow.repository import InMemoryWorkflowRepository


@pytest.mark.asyncio
async def test_product_topic_generates_distinct_explainable_opportunities() -> None:
    result = await LocalOpportunityAgent().generate(
        OpportunityRequest(topic="大疆", count=4), []
    )

    assert result.topic_type == "product"
    assert result.topic_type_label == "品牌／產品"
    assert len(result.opportunities) == 4
    assert len({item.topic for item in result.opportunities}) == 4
    assert all("大疆" in item.topic for item in result.opportunities)
    assert all(item.recommended_formats for item in result.opportunities)
    assert all(item.score_breakdown.relevance >= 80 for item in result.opportunities)
    assert all("人工流程與 AI 協作" not in item.topic for item in result.opportunities)


@pytest.mark.asyncio
async def test_same_category_uses_seed_based_angle_selection_and_supports_eight() -> None:
    agent = LocalOpportunityAgent()
    python_result = await agent.generate(
        OpportunityRequest(topic="Python 自動化", count=4), []
    )
    ai_result = await agent.generate(
        OpportunityRequest(topic="AI 工作流", count=4), []
    )
    expanded = await agent.generate(
        OpportunityRequest(topic="AI 工作流", count=8), []
    )

    assert python_result.topic_type == ai_result.topic_type == "technology"
    assert {item.type for item in python_result.opportunities} != {
        item.type for item in ai_result.opportunities
    }
    assert len(expanded.opportunities) == 8
    assert len({item.id for item in expanded.opportunities}) == 8


@pytest.mark.asyncio
async def test_existing_topic_reduces_novelty_instead_of_using_a_fixed_score() -> None:
    agent = LocalOpportunityAgent()
    fresh = await agent.generate(OpportunityRequest(topic="大疆"), [])
    repeated = await agent.generate(OpportunityRequest(topic="大疆"), ["大疆"])

    fresh_novelty = sum(
        item.score_breakdown.novelty for item in fresh.opportunities
    ) / len(fresh.opportunities)
    repeated_novelty = sum(
        item.score_breakdown.novelty for item in repeated.opportunities
    ) / len(repeated.opportunities)
    assert repeated_novelty <= fresh_novelty - 30
    assert max(item.score for item in repeated.opportunities) < max(
        item.score for item in fresh.opportunities
    )


def test_opportunity_api_uses_workflow_history_and_validates_input() -> None:
    repository = InMemoryWorkflowRepository()
    asyncio.run(
        repository.save(
            WorkflowRun(
                input=WorkflowInput(
                    topic="大疆",
                    goal="education",
                    platforms=["youtube"],
                    output_type="short_video",
                )
            )
        )
    )
    app = FastAPI()
    app.include_router(build_opportunity_router(repository))
    client = TestClient(app)

    response = client.post(
        "/api/v1/opportunities/generate",
        json={"topic": "大疆", "platforms": ["youtube"], "count": 4},
    )
    assert response.status_code == 200
    assert response.json()["history_topics_considered"] == 1
    assert len(response.json()["opportunities"]) == 4
    assert client.post(
        "/api/v1/opportunities/generate", json={"topic": ""}
    ).status_code == 422
    assert client.post(
        "/api/v1/opportunities/generate", json={"topic": "   "}
    ).status_code == 422
