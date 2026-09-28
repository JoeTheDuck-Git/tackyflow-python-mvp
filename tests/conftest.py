import os


# Existing API tests exercise the explicit local integration contract. Auth-specific
# tests instantiate their own repository and session-mode app dependencies.
os.environ.setdefault("AUTH_MODE", "local")
os.environ.setdefault("OPPORTUNITY_PROVIDER", "local_rule")
os.environ.setdefault("CONTENT_PROVIDER", "local_rule")
os.environ.setdefault("WORKFLOW_AGENT_PROVIDER", "local_rule")
# Unit and E2E tests intentionally exercise isolated temporary SQLite files.
os.environ.setdefault("DATABASE_URL", "")
