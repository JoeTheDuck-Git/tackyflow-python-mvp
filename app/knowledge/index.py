from __future__ import annotations

from hashlib import sha1
import math
import re
from typing import Protocol

try:
    from llama_index.core import Document
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.embeddings.openai import OpenAIEmbedding
except ImportError:  # Vercel uses the lightweight native fallback to stay below 225 MB.
    Document = None
    SentenceSplitter = None
    OpenAIEmbedding = None

from app.knowledge.models import CrawledDocument, KnowledgeChunk, RetrievedChunk
from app.knowledge.repository import KnowledgeRepository


class Embedder(Protocol):
    dimension: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LlamaIndexOpenAIEmbedder:
    dimension = 1536

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        self.model = model
        if OpenAIEmbedding is not None:
            self.client = OpenAIEmbedding(model=model, dimensions=self.dimension)
            self._native_openai = False
        else:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI()
            self._native_openai = True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._native_openai:
            return await self.client.aget_text_embedding_batch(texts)
        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimension,
        )
        return [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]


class DeterministicTestEmbedder:
    """Offline fallback used only for local_rule/test mode, never market truth."""

    dimension = 64

    async def embed(self, texts: list[str]) -> list[list[float]]:
        output: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in re.findall(r"[\w\u4e00-\u9fff]+", text.casefold()):
                value = int(sha1(token.encode()).hexdigest()[:8], 16)
                vector[value % self.dimension] += 1.0
            norm = math.sqrt(sum(item * item for item in vector)) or 1.0
            output.append([item / norm for item in vector])
        return output


class LlamaKnowledgeIndex:
    def __init__(self, repository: KnowledgeRepository, embedder: Embedder) -> None:
        self.repository = repository
        self.embedder = embedder
        self.splitter = (
            SentenceSplitter(chunk_size=700, chunk_overlap=100)
            if SentenceSplitter is not None
            else None
        )

    @staticmethod
    def _native_chunks(content: str, *, chunk_size: int = 2800, overlap: int = 400) -> list[str]:
        """Dependency-light paragraph-aware fallback for serverless deployments."""
        text = re.sub(r"\r\n?", "\n", content).strip()
        if not text:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            if end < len(text):
                search_from = start + chunk_size // 2
                breakpoints = [
                    text.rfind(marker, search_from, end)
                    for marker in ("\n\n", "。", "！", "？", ". ", "! ", "? ")
                ]
                boundary = max(breakpoints)
                if boundary > start:
                    end = boundary + (2 if text[boundary : boundary + 2] == "\n\n" else 1)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
        return chunks

    async def index(self, document: CrawledDocument) -> CrawledDocument:
        saved = await self.repository.save_document(document)
        if self.splitter is not None and Document is not None:
            nodes = self.splitter.get_nodes_from_documents(
                [Document(text=saved.content, metadata={"source_url": saved.source_url})]
            )
            texts = [node.get_content().strip() for node in nodes if node.get_content().strip()]
        else:
            texts = self._native_chunks(saved.content)
        embeddings = await self.embedder.embed(texts)
        chunks = [
            KnowledgeChunk(
                id=f"chunk-{sha1(f'{saved.id}:{index}'.encode()).hexdigest()[:20]}",
                document_id=saved.id,
                workspace_id=saved.workspace_id,
                chunk_index=index,
                content=text,
                embedding=embedding,
                metadata={
                    **saved.metadata,
                    "source_url": saved.source_url,
                    "title": saved.title,
                    "fetched_at": saved.fetched_at.isoformat(),
                },
            )
            for index, (text, embedding) in enumerate(zip(texts, embeddings))
        ]
        await self.repository.replace_chunks(saved, chunks)
        return saved

    async def retrieve(self, workspace_id: str, query: str, *, limit: int = 6) -> list[RetrievedChunk]:
        [embedding] = await self.embedder.embed([query])
        return await self.repository.search(workspace_id, embedding, limit)
