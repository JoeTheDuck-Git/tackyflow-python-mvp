-- Python AI backend is the sole owner of knowledge ingestion, retrieval and
-- LangGraph checkpoint data.  The UI/Node service must access these tables via
-- the Python API rather than writing them directly.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ai_knowledge_documents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    title TEXT,
    raw_content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_ai_knowledge_documents_workspace
    ON ai_knowledge_documents(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_knowledge_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES ai_knowledge_documents(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_ai_knowledge_chunks_workspace
    ON ai_knowledge_chunks(workspace_id, document_id);
CREATE INDEX IF NOT EXISTS idx_ai_knowledge_chunks_embedding
    ON ai_knowledge_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS ai_knowledge_retrieval_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    generation_id TEXT,
    query TEXT NOT NULL,
    mode TEXT NOT NULL,
    result_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_knowledge_retrieval_workspace
    ON ai_knowledge_retrieval_runs(workspace_id, created_at DESC);

ALTER TABLE ai_knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_knowledge_retrieval_runs ENABLE ROW LEVEL SECURITY;

-- Backend/service-role only. Workspace authorization is enforced by FastAPI
-- before every query; no anon/authenticated PostgREST policy is intentionally
-- created for these tables.
