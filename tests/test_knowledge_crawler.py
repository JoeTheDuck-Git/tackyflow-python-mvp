from __future__ import annotations

import socket
from hashlib import sha256

import httpx
import pytest

from app.knowledge.crawler import HttpxWebCrawler, build_web_crawler
from app.knowledge.index import DeterministicTestEmbedder, LlamaKnowledgeIndex
from app.knowledge.models import CrawledDocument
from app.knowledge.repository import InMemoryKnowledgeRepository
from app.knowledge.service import KnowledgeService


@pytest.mark.asyncio
async def test_httpx_crawler_extracts_readable_text(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="""
                <html><head><title>官方產品頁</title><style>hidden</style></head>
                <body><h1>全景相機</h1><p>這是一段足夠長的官方產品說明，供內容機會代理建立可靠的參考脈絡。</p></body></html>
            """,
            request=request,
        )

    crawler = HttpxWebCrawler(transport=httpx.MockTransport(handler))
    document = await crawler.crawl("https://example.com/product", "workspace-a")

    assert document.title == "官方產品頁"
    assert "全景相機" in document.content
    assert "hidden" not in document.content
    assert document.metadata["crawler"] == "httpx"
    assert document.metadata["completeness"] == 0.6
    assert document.metadata["requires_human_review"] is True


def test_explicit_httpx_mode_does_not_require_browser_dependency() -> None:
    assert isinstance(build_web_crawler("httpx"), HttpxWebCrawler)


class _SuccessCrawler:
    method = "httpx"

    async def crawl(self, url: str, workspace_id: str) -> CrawledDocument:
        content = "這是一份可追蹤的官方資料。" * 30
        return CrawledDocument(
            workspace_id=workspace_id,
            source_url=url,
            title="官方資料",
            content=content,
            content_hash=sha256(content.encode()).hexdigest(),
            metadata={
                "crawler": "httpx",
                "completeness": 0.95,
                "confidence": 0.82,
                "excerpt": content[:100],
                "requires_human_review": False,
            },
        )


class _FailedCrawler:
    method = "httpx"

    async def crawl(self, url: str, workspace_id: str) -> CrawledDocument:
        raise RuntimeError("upstream unavailable")


def _service(crawler):
    repository = InMemoryKnowledgeRepository()
    return (
        KnowledgeService(
            crawler,
            LlamaKnowledgeIndex(repository, DeterministicTestEmbedder()),
            repository,
        ),
        repository,
    )


@pytest.mark.asyncio
async def test_ingestion_records_successful_source_provenance() -> None:
    service, repository = _service(_SuccessCrawler())

    document = await service.ingest_url(
        "workspace-a",
        "https://example.com/official",
        generation_id="generation-a",
    )

    records = await repository.list_captures(
        "workspace-a", generation_id="generation-a"
    )
    assert records[0].document_id == document.id
    assert records[0].status == "success"
    assert records[0].completeness == 0.95
    assert records[0].confidence == 0.82
    assert records[0].requires_human_review is False


@pytest.mark.asyncio
async def test_ingestion_records_failure_and_requires_human_review() -> None:
    service, repository = _service(_FailedCrawler())

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        await service.ingest_url(
            "workspace-a",
            "https://example.com/broken",
            generation_id="generation-b",
        )

    records = await repository.list_captures(
        "workspace-a", generation_id="generation-b"
    )
    assert records[0].status == "failed"
    assert records[0].requires_human_review is True
    assert "upstream unavailable" in records[0].failure_reason
