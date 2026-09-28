from __future__ import annotations

import os
from time import monotonic
from uuid import uuid4

from app.knowledge.crawler import WebCrawler, build_web_crawler
from app.knowledge.index import (
    DeterministicTestEmbedder,
    LlamaIndexOpenAIEmbedder,
    LlamaKnowledgeIndex,
)
from app.knowledge.models import CrawledDocument, RetrievedChunk, SourceCaptureRecord
from app.knowledge.repository import (
    InMemoryKnowledgeRepository,
    KnowledgeRepository,
    PostgresKnowledgeRepository,
)


class KnowledgeService:
    def __init__(self, crawler: WebCrawler, index: LlamaKnowledgeIndex, repository: KnowledgeRepository) -> None:
        self.crawler = crawler
        self.index = index
        self.repository = repository

    async def ingest_url(
        self,
        workspace_id: str,
        url: str,
        *,
        generation_id: str | None = None,
    ) -> CrawledDocument:
        method = str(getattr(self.crawler, "method", type(self.crawler).__name__))
        try:
            document = await self.crawler.crawl(url, workspace_id)
            saved = await self.index.index(document)
        except Exception as exc:
            await self.repository.record_capture(
                SourceCaptureRecord(
                    workspace_id=workspace_id,
                    generation_id=generation_id,
                    source_url=url,
                    acquisition_method=method,
                    status="failed",
                    failure_reason=f"{type(exc).__name__}: {str(exc)[:800]}",
                    requires_human_review=True,
                )
            )
            raise
        metadata = saved.metadata
        await self.repository.record_capture(
            SourceCaptureRecord(
                workspace_id=workspace_id,
                generation_id=generation_id,
                document_id=saved.id,
                source_url=saved.source_url,
                acquisition_method=str(metadata.get("crawler") or method),
                status="success",
                completeness=float(metadata.get("completeness", 0.75)),
                confidence=float(metadata.get("confidence", 0.75)),
                excerpt=str(metadata.get("excerpt") or saved.content[:500]),
                requires_human_review=bool(metadata.get("requires_human_review", False)),
                fetched_at=saved.fetched_at,
            )
        )
        return saved

    async def list_captures(
        self,
        workspace_id: str,
        *,
        generation_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceCaptureRecord]:
        return await self.repository.list_captures(
            workspace_id,
            generation_id=generation_id,
            limit=limit,
        )

    async def retrieve(
        self,
        workspace_id: str,
        query: str,
        *,
        generation_id: str | None = None,
        limit: int = 6,
        mode: str = "knowledge_primary",
    ) -> list[RetrievedChunk]:
        run_id = str(uuid4())
        started = monotonic()
        try:
            chunks = await self.index.retrieve(workspace_id, query, limit=limit)
            await self.repository.record_retrieval(
                run_id=run_id,
                workspace_id=workspace_id,
                generation_id=generation_id,
                query=query,
                mode=mode,
                chunk_ids=[chunk.id for chunk in chunks],
                duration_ms=round((monotonic() - started) * 1000),
            )
            return chunks
        except Exception as exc:
            await self.repository.record_retrieval(
                run_id=run_id,
                workspace_id=workspace_id,
                generation_id=generation_id,
                query=query,
                mode=mode,
                chunk_ids=[],
                duration_ms=round((monotonic() - started) * 1000),
                error=type(exc).__name__,
            )
            raise


def build_knowledge_service(
    database_url: str,
    *,
    embedding_model: str = "text-embedding-3-small",
    crawler_mode: str = "auto",
) -> KnowledgeService:
    repository: KnowledgeRepository = (
        PostgresKnowledgeRepository(database_url)
        if database_url
        else InMemoryKnowledgeRepository()
    )
    embedder = (
        LlamaIndexOpenAIEmbedder(model=embedding_model)
        if os.getenv("OPENAI_API_KEY")
        else DeterministicTestEmbedder()
    )
    return KnowledgeService(
        crawler=build_web_crawler(crawler_mode),
        index=LlamaKnowledgeIndex(repository, embedder),
        repository=repository,
    )
