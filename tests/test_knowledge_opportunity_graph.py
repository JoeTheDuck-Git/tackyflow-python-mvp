from __future__ import annotations

import pytest

from app.agents.opportunity import LocalOpportunityAgent
from app.agents.opportunity_graph import KnowledgeOpportunityAgent
from app.domain.models import OpportunityRequest
from app.knowledge.models import RetrievedChunk, SourceCaptureRecord


class FakeKnowledge:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.ingested: list[tuple[str, str]] = []
        self.queries: list[tuple[str, str]] = []

    async def ingest_url(self, workspace_id: str, url: str, **kwargs):
        if self.fail:
            raise RuntimeError("crawler unavailable")
        self.ingested.append((workspace_id, url))

    async def list_captures(self, workspace_id: str, **kwargs):
        if not self.ingested:
            return []
        return [
            SourceCaptureRecord(
                workspace_id=workspace_id,
                generation_id=kwargs.get("generation_id"),
                source_url=self.ingested[-1][1],
                acquisition_method="httpx",
                status="success",
                completeness=0.95,
                confidence=0.82,
                excerpt="官方產品資料",
            )
        ]

    async def retrieve(self, workspace_id: str, query: str, **kwargs):
        if self.fail:
            raise RuntimeError("index unavailable")
        self.queries.append((workspace_id, query))
        return [
            RetrievedChunk(
                id="chunk-1",
                document_id="document-1",
                content="官方資料顯示此產品主打穩定拍攝與全景後製流程。",
                score=0.91,
                source_url="https://example.com/product",
                title="產品官方資料",
            )
        ]


@pytest.mark.asyncio
async def test_python_langgraph_adds_llamaindex_context() -> None:
    knowledge = FakeKnowledge()
    agent = KnowledgeOpportunityAgent(
        LocalOpportunityAgent(),
        LocalOpportunityAgent(),
        knowledge,
        mode="knowledge_primary",
    )
    result = await agent.generate(
        OpportunityRequest(
            topic="Insta360",
            workspace_id="workspace-a",
            reference_urls=["https://example.com/product"],
        ),
        [],
        generation_id="generation-a",
    )

    assert knowledge.ingested == [
        ("workspace-a", "https://example.com/product")
    ]
    assert any(signal.source == "llamaindex_retrieval" for signal in result.signals)
    assert any(event.action == "knowledge_retrieved" for event in result.events)
    assert result.generation_mode.endswith("+llamaindex")
    assert result.source_captures[0]["acquisition_method"] == "httpx"


@pytest.mark.asyncio
async def test_knowledge_failure_preserves_legacy_result() -> None:
    agent = KnowledgeOpportunityAgent(
        LocalOpportunityAgent(),
        LocalOpportunityAgent(),
        FakeKnowledge(fail=True),
        mode="knowledge_primary",
    )
    result = await agent.generate(
        OpportunityRequest(topic="Insta360", workspace_id="workspace-a"),
        [],
        generation_id="generation-fallback",
    )

    assert len(result.opportunities) >= 4
    assert any(event.action == "knowledge_fallback" for event in result.events)


@pytest.mark.asyncio
async def test_legacy_mode_does_not_touch_knowledge_service() -> None:
    knowledge = FakeKnowledge(fail=True)
    agent = KnowledgeOpportunityAgent(
        LocalOpportunityAgent(),
        LocalOpportunityAgent(),
        knowledge,
        mode="legacy",
    )
    result = await agent.generate(
        OpportunityRequest(topic="Insta360", workspace_id="workspace-a"), []
    )
    assert result.provider == "local_rule"
    assert not any(event.action == "knowledge_fallback" for event in result.events)


def test_knowledge_migration_enforces_workspace_and_vector_schema() -> None:
    from app.db.migrations import MIGRATIONS_DIR

    sql = (MIGRATIONS_DIR / "0001_ai_knowledge.sql").read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "workspace_id TEXT NOT NULL" in sql
    assert "embedding vector(1536)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql

    audit_sql = (MIGRATIONS_DIR / "0002_source_capture_records.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE IF NOT EXISTS ai_source_capture_records" in audit_sql
    assert "requires_human_review BOOLEAN NOT NULL" in audit_sql
    assert "workspace_id TEXT NOT NULL" in audit_sql
