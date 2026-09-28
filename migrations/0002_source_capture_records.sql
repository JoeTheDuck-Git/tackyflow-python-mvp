CREATE TABLE IF NOT EXISTS ai_source_capture_records (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    generation_id TEXT,
    document_id TEXT REFERENCES ai_knowledge_documents(id) ON DELETE SET NULL,
    source_url TEXT NOT NULL,
    acquisition_method TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    completeness DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (completeness BETWEEN 0 AND 1),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
    excerpt TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    requires_human_review BOOLEAN NOT NULL DEFAULT false,
    fetched_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_source_capture_workspace
    ON ai_source_capture_records(workspace_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_source_capture_generation
    ON ai_source_capture_records(workspace_id, generation_id, fetched_at DESC);

ALTER TABLE ai_source_capture_records ENABLE ROW LEVEL SECURITY;

-- Service-role only. Workspace authorization is enforced by FastAPI.
