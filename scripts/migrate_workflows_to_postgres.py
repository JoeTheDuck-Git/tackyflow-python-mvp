"""Copy existing SQLite workflow snapshots and material-center data to PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio

from app.config import settings
from app.workflow.repository import (
    PostgresWorkflowRepository,
    SQLiteWorkflowRepository,
    WorkflowNotFoundError,
)


async def migrate(*, replace_existing: bool = False) -> tuple[int, int]:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    source = SQLiteWorkflowRepository(settings.database_path)
    target = PostgresWorkflowRepository(settings.database_url)
    workflows = await source.list(limit=10_000)
    copied = 0
    skipped = 0
    for workflow in reversed(workflows):
        try:
            existing = await target.get(workflow.id)
        except WorkflowNotFoundError:
            existing = None
        if existing is not None:
            if not replace_existing:
                skipped += 1
                continue
            await target.delete(workflow.id)
        await target.save(workflow)
        copied += 1
    return copied, skipped


def verify() -> dict[str, int]:
    target = PostgresWorkflowRepository(settings.database_url)
    tables = (
        "py_workflows",
        "py_material_projects",
        "py_material_script_sections",
        "py_material_storyboard_shots",
        "py_material_visual_cues",
        "py_material_generated_assets",
    )
    with target._connect() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
            for table in tables
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="replace matching PostgreSQL workflows with the SQLite snapshot",
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        print("PostgreSQL material-center rows:")
        for table, count in verify().items():
            print(f"  {table}={count}")
        return
    copied, skipped = asyncio.run(migrate(replace_existing=args.replace_existing))
    print(f"Migration complete: copied={copied}, skipped={skipped}")


if __name__ == "__main__":
    main()
