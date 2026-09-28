from __future__ import annotations

from typing import Any, Protocol

from psycopg.types.json import Jsonb

from app.db.postgres import get_pool
from app.knowledge.models import (
    CrawledDocument,
    KnowledgeChunk,
    RetrievedChunk,
    SourceCaptureRecord,
)


class KnowledgeRepository(Protocol):
    async def save_document(self, document: CrawledDocument) -> CrawledDocument: ...
    async def replace_chunks(self, document: CrawledDocument, chunks: list[KnowledgeChunk]) -> None: ...
    async def search(self, workspace_id: str, embedding: list[float], limit: int) -> list[RetrievedChunk]: ...
    async def record_retrieval(self, *, run_id: str, workspace_id: str, generation_id: str | None, query: str, mode: str, chunk_ids: list[str], duration_ms: int, error: str | None = None) -> None: ...
    async def record_capture(self, record: SourceCaptureRecord) -> SourceCaptureRecord: ...
    async def list_captures(self, workspace_id: str, *, generation_id: str | None = None, limit: int = 100) -> list[SourceCaptureRecord]: ...


class InMemoryKnowledgeRepository:
    def __init__(self) -> None:
        self.documents: dict[str, CrawledDocument] = {}
        self.chunks: dict[str, KnowledgeChunk] = {}
        self.retrieval_runs: list[dict[str, Any]] = []
        self.capture_records: list[SourceCaptureRecord] = []

    async def save_document(self, document: CrawledDocument) -> CrawledDocument:
        existing = next(
            (
                item
                for item in self.documents.values()
                if item.workspace_id == document.workspace_id
                and item.content_hash == document.content_hash
            ),
            None,
        )
        if existing:
            refreshed = document.model_copy(update={"id": existing.id}, deep=True)
            self.documents[existing.id] = refreshed
            return refreshed.model_copy(deep=True)
        self.documents[document.id] = document.model_copy(deep=True)
        return document

    async def replace_chunks(self, document: CrawledDocument, chunks: list[KnowledgeChunk]) -> None:
        self.chunks = {
            key: value
            for key, value in self.chunks.items()
            if value.document_id != document.id
        }
        self.chunks.update({chunk.id: chunk.model_copy(deep=True) for chunk in chunks})

    async def search(self, workspace_id: str, embedding: list[float], limit: int) -> list[RetrievedChunk]:
        def cosine(left: list[float], right: list[float]) -> float:
            dot = sum(a * b for a, b in zip(left, right))
            left_norm = sum(a * a for a in left) ** 0.5
            right_norm = sum(b * b for b in right) ** 0.5
            return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0

        ranked: list[RetrievedChunk] = []
        for chunk in self.chunks.values():
            if chunk.workspace_id != workspace_id:
                continue
            document = self.documents[chunk.document_id]
            score = max(0.0, min(1.0, cosine(embedding, chunk.embedding)))
            ranked.append(
                RetrievedChunk(
                    id=chunk.id,
                    document_id=chunk.document_id,
                    content=chunk.content,
                    score=score,
                    source_url=document.source_url,
                    title=document.title,
                    metadata=chunk.metadata,
                )
            )
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:limit]

    async def record_retrieval(self, **payload: Any) -> None:
        self.retrieval_runs.append(dict(payload))

    async def record_capture(self, record: SourceCaptureRecord) -> SourceCaptureRecord:
        self.capture_records.append(record.model_copy(deep=True))
        return record

    async def list_captures(
        self,
        workspace_id: str,
        *,
        generation_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceCaptureRecord]:
        records = [
            item
            for item in self.capture_records
            if item.workspace_id == workspace_id
            and (generation_id is None or item.generation_id == generation_id)
        ]
        return [item.model_copy(deep=True) for item in reversed(records[-limit:])]


class PostgresKnowledgeRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connection(self):
        return get_pool(self.database_url).connection()

    @staticmethod
    def _vector(values: list[float]) -> str:
        return "[" + ",".join(f"{value:.10g}" for value in values) + "]"

    async def save_document(self, document: CrawledDocument) -> CrawledDocument:
        with self._connection() as connection:
            row = connection.execute(
                """
                INSERT INTO ai_knowledge_documents
                    (id, workspace_id, source_type, source_url, title, raw_content,
                     content_hash, metadata, fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (workspace_id, content_hash) DO UPDATE SET
                    source_url=EXCLUDED.source_url, title=EXCLUDED.title,
                    metadata=EXCLUDED.metadata, fetched_at=EXCLUDED.fetched_at,
                    updated_at=now()
                RETURNING id
                """,
                (
                    document.id,
                    document.workspace_id,
                    document.source_type,
                    document.source_url,
                    document.title,
                    document.content,
                    document.content_hash,
                    Jsonb(document.metadata),
                    document.fetched_at,
                ),
            ).fetchone()
        return document.model_copy(update={"id": row["id"]})

    async def replace_chunks(self, document: CrawledDocument, chunks: list[KnowledgeChunk]) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM ai_knowledge_chunks WHERE document_id=%s AND workspace_id=%s",
                (document.id, document.workspace_id),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO ai_knowledge_chunks
                        (id, document_id, workspace_id, chunk_index, content,
                         token_count, metadata, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::vector)
                    """,
                    [
                        (
                            item.id,
                            item.document_id,
                            item.workspace_id,
                            item.chunk_index,
                            item.content,
                            len(item.content.split()),
                            Jsonb(item.metadata),
                            self._vector(item.embedding),
                        )
                        for item in chunks
                    ],
                )

    async def search(self, workspace_id: str, embedding: list[float], limit: int) -> list[RetrievedChunk]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.document_id, c.content, c.metadata,
                       d.source_url, d.title,
                       greatest(0, least(1, 1 - (c.embedding <=> %s::vector))) AS score
                FROM ai_knowledge_chunks c
                JOIN ai_knowledge_documents d ON d.id=c.document_id
                WHERE c.workspace_id=%s AND d.workspace_id=%s
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    self._vector(embedding),
                    workspace_id,
                    workspace_id,
                    self._vector(embedding),
                    limit,
                ),
            ).fetchall()
        return [RetrievedChunk(**dict(row)) for row in rows]

    async def record_retrieval(self, **payload: Any) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_knowledge_retrieval_runs
                    (id, workspace_id, generation_id, query, mode,
                     result_chunk_ids, duration_ms, error)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    payload["run_id"],
                    payload["workspace_id"],
                    payload.get("generation_id"),
                    payload["query"],
                    payload["mode"],
                    Jsonb(payload["chunk_ids"]),
                    payload["duration_ms"],
                    payload.get("error"),
                ),
            )

    async def record_capture(self, record: SourceCaptureRecord) -> SourceCaptureRecord:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO ai_source_capture_records
                    (id, workspace_id, generation_id, document_id, source_url,
                     acquisition_method, status, completeness, confidence, excerpt,
                     failure_reason, requires_human_review, fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.id,
                    record.workspace_id,
                    record.generation_id,
                    record.document_id,
                    record.source_url,
                    record.acquisition_method,
                    record.status,
                    record.completeness,
                    record.confidence,
                    record.excerpt,
                    record.failure_reason,
                    record.requires_human_review,
                    record.fetched_at,
                ),
            )
        return record

    async def list_captures(
        self,
        workspace_id: str,
        *,
        generation_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceCaptureRecord]:
        clauses = ["workspace_id=%s"]
        values: list[Any] = [workspace_id]
        if generation_id is not None:
            clauses.append("generation_id=%s")
            values.append(generation_id)
        values.append(max(1, min(limit, 500)))
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, workspace_id, generation_id, document_id, source_url,
                       acquisition_method, status, completeness, confidence, excerpt,
                       failure_reason, requires_human_review, fetched_at
                FROM ai_source_capture_records
                WHERE {' AND '.join(clauses)}
                ORDER BY fetched_at DESC
                LIMIT %s
                """,
                tuple(values),
            ).fetchall()
        return [SourceCaptureRecord(**dict(row)) for row in rows]
