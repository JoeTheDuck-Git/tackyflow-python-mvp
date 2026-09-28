"""End-to-end smoke test for LlamaIndex -> OpenAI embedding -> pgvector."""

from __future__ import annotations

import asyncio
from hashlib import sha256

import psycopg

from app.config import settings
from app.db.postgres import close_pools
from app.knowledge.models import CrawledDocument
from app.knowledge.service import build_knowledge_service


WORKSPACE_ID = "__knowledge_smoke__"


async def main() -> None:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    service = build_knowledge_service(
        settings.database_url,
        embedding_model=settings.openai_embedding_model,
    )
    content = (
        "DJI Osmo 360 測試知識：內容機會研究必須保留來源，"
        "並將品牌文件與公開資料分開標示。"
    )
    try:
        await service.index.index(
            CrawledDocument(
                workspace_id=WORKSPACE_ID,
                source_url="https://example.invalid/smoke",
                title="知識服務整合測試",
                content=content,
                content_hash=sha256(content.encode()).hexdigest(),
            )
        )
        results = await service.retrieve(
            WORKSPACE_ID,
            "如何確保內容機會保留資料來源？",
            mode="smoke",
        )
        if not results or results[0].score <= 0:
            raise RuntimeError("pgvector retrieval returned no relevant result")
        print(f"knowledge smoke ok: score={results[0].score:.3f}")
    finally:
        # The integration row is synthetic and must not remain in customer data,
        # including when OpenAI or pgvector is temporarily unavailable.
        with psycopg.connect(settings.database_url) as connection:
            connection.execute(
                "DELETE FROM ai_knowledge_retrieval_runs WHERE workspace_id=%s",
                (WORKSPACE_ID,),
            )
            connection.execute(
                "DELETE FROM ai_knowledge_documents WHERE workspace_id=%s",
                (WORKSPACE_ID,),
            )
        close_pools()


if __name__ == "__main__":
    asyncio.run(main())
