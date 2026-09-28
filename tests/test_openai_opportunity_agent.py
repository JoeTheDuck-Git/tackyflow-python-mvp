from types import SimpleNamespace

import pytest

from app.agents.opportunity import (
    GeneratedOpportunitySet,
    OpenAIOpportunityAgent,
    OpportunityQualityReview,
)
from app.domain.models import OpportunityRequest, OpportunitySignal


def _parsed_set(count: int = 4) -> GeneratedOpportunitySet:
    return GeneratedOpportunitySet.model_validate(
        {
            "topic_type": "product",
            "topic_type_label": "品牌／產品",
            "context_summary": "已辨識 DJI Osmo 360 為全景相機產品，並依來源提出題目。",
            "opportunities": [
                {
                    "type": "產品解析",
                    "topic": f"DJI Osmo 360 實測角度 {index}",
                    "description": f"以使用情境 {index} 說明產品差異。",
                    "rationale": "符合攝影創作者的購買評估需求。",
                    "recommended_formats": ["短影音腳本"],
                    "recommended_platforms": ["YouTube"],
                    "relevance": 92,
                    "novelty": 80,
                    "audience_value": 88,
                    "feasibility": 84,
                    "target_audience": "攝影創作者",
                    "angle": "實際使用與購買判斷",
                    "hook": "這台全景相機真正適合誰？",
                    "key_points": ["使用情境", "限制與取捨"],
                    "cta": "留言分享你的拍攝需求",
                    "evidence_needed": ["確認官方規格與發布日期"],
                    "estimated_effort": "中等",
                    "production_notes": ["產品規格畫面標示來源日期"],
                }
                for index in range(1, count + 1)
            ],
        }
    )


class FakeUsage:
    def model_dump(self, mode="json"):
        return {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300}


class FakeResearchResponse:
    def __init__(self, *, citations: bool = True) -> None:
        self.output_text = "DJI Osmo 360 研究摘要：官方產品定位、使用情境與待確認限制。"
        self.model = "gpt-5.6-sol"
        self.usage = FakeUsage()
        self.id = "resp-opportunity-research-test"
        self.citations = citations

    def model_dump(self, mode="json"):
        annotations = []
        if self.citations:
            annotations = [
                {
                    "type": "url_citation",
                    "url": "https://www.dji.com/osmo-360",
                    "title": "DJI Osmo 360",
                },
                {
                    "type": "url_citation",
                    "url": "https://www.dji.com/media-center",
                    "title": "DJI Media Center",
                },
            ]
        return {"output": [{"content": [{"annotations": annotations}]}]}


class FakeStructuredResponse:
    def __init__(self) -> None:
        self.output_parsed = _parsed_set()
        self.model = "gpt-5.6-sol"
        self.usage = FakeUsage()
        self.id = "resp-opportunity-structured-test"


class FakeResponses:
    def __init__(self, *, citations: bool = True) -> None:
        self.research_response = FakeResearchResponse(citations=citations)
        self.structured_response = FakeStructuredResponse()
        self.create_kwargs = None
        self.parse_kwargs = None
        self.parse_calls = []

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.research_response

    def parse(self, **kwargs):
        self.parse_kwargs = kwargs
        self.parse_calls.append(kwargs)
        if kwargs["text_format"] is OpportunityQualityReview:
            return SimpleNamespace(
                output_parsed=OpportunityQualityReview(
                    confidence=0.93,
                    summary="題目差異、證據邊界與受眾適配均通過覆核。",
                    issues=[],
                    checks=["主題關聯", "題目差異", "證據邊界"],
                    opportunities=_parsed_set().opportunities,
                ),
                model="gpt-5.6-sol",
                usage=FakeUsage(),
                id="resp-opportunity-review-test",
            )
        return self.structured_response


@pytest.mark.asyncio
async def test_openai_opportunity_agent_uses_structured_web_search_and_citations() -> None:
    responses = FakeResponses(citations=True)
    client = SimpleNamespace(responses=responses)
    agent = OpenAIOpportunityAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: client,
    )
    request = OpportunityRequest(
        topic="DJI Osmo 360",
        audience="攝影創作者",
        count=4,
        reference_urls=["https://www.dji.com/osmo-360"],
    )

    result = await agent.generate(
        request,
        ["過去題目"],
        [
            OpportunitySignal(
                name="使用者參考網址",
                source="user_supplied_url",
                confidence=0.35,
                summary="尚未驗證",
            )
        ],
        generation_id="generation-openai",
    )

    assert result.provider == "openai"
    assert result.model == "gpt-5.6-sol"
    assert result.generation_mode == "openai_web_search"
    assert result.topic_type == "product"
    assert result.has_live_signals is True
    assert len([signal for signal in result.signals if signal.is_live]) == 2
    assert all(item.data_confidence.value == "high" for item in result.opportunities)
    assert len(result.opportunities) == 4
    assert len({item.id for item in result.opportunities}) == 4
    assert result.usage["total_tokens"] == 900
    assert result.usage["research_total_tokens"] == 300
    assert result.usage["generation_total_tokens"] == 300
    assert result.usage["review_total_tokens"] == 300
    assert result.quality_review["status"] == "reviewed"
    assert responses.create_kwargs["tools"] == [
        {"type": "web_search", "search_context_size": "low"}
    ]
    assert responses.create_kwargs["store"] is False
    assert "tools" not in responses.parse_kwargs
    assert responses.parse_kwargs["store"] is False
    assert responses.parse_calls[0]["text_format"] is GeneratedOpportunitySet
    assert responses.parse_calls[1]["text_format"] is OpportunityQualityReview
    assert "research_dossier" in responses.parse_calls[0]["input"][1]["content"]


@pytest.mark.asyncio
async def test_openai_opportunity_agent_keeps_confidence_low_without_citations() -> None:
    responses = FakeResponses(citations=False)
    agent = OpenAIOpportunityAgent(
        model="gpt-5.6-sol",
        timeout_seconds=30,
        client_factory=lambda: SimpleNamespace(responses=responses),
    )

    result = await agent.generate(
        OpportunityRequest(topic="DJI Osmo 360", count=4),
        [],
        generation_id="generation-no-citations",
    )

    assert result.has_live_signals is False
    assert all(item.data_confidence.value == "low" for item in result.opportunities)
    assert all(item.score <= 85 for item in result.opportunities)
    assert all("no_citations" in item.scoring_method for item in result.opportunities)
