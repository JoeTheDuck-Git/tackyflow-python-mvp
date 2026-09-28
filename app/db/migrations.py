from __future__ import annotations

from pathlib import Path

import psycopg


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def apply_migrations(database_url: str) -> list[str]:
    """Apply versioned Python-AI migrations exactly once.

    Repositories no longer own schema evolution.  This runner is intentionally
    small so it can run during deployment or from ``python -m app.db.migrations``.
    """

    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=False) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM ai_schema_migrations"
            ).fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in existing:
                continue
            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO ai_schema_migrations(version) VALUES (%s)",
                (path.name,),
            )
            applied.append(path.name)
        connection.commit()
    return applied


if __name__ == "__main__":
    from app.config import settings

    if not settings.database_url:
        raise SystemExit("DATABASE_URL is required")
    for migration in apply_migrations(settings.database_url):
        print(f"applied {migration}")
