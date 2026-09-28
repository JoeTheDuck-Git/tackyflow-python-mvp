from __future__ import annotations

import asyncio
from pathlib import Path

import httpx


class BugScreenshotStorage:
    def __init__(self, *, root: Path, supabase_url: str = "", service_role_key: str = "", bucket: str = "generated-assets") -> None:
        self.root = root / "bug-reports"
        self.supabase_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        self.bucket = bucket
        if not self.supabase_url:
            self.root.mkdir(parents=True, exist_ok=True)

    def _headers(self) -> dict[str, str]:
        return {"apikey": self.service_role_key, "Authorization": f"Bearer {self.service_role_key}"}

    def key(self, workspace_id: str, report_id: str, extension: str) -> str:
        safe_workspace = "".join(char for char in workspace_id if char.isalnum() or char in "-_")[:80] or "workspace"
        return f"_beta-bug-reports/{safe_workspace}/{report_id}.{extension}"

    async def write(self, key: str, content: bytes, mime_type: str) -> None:
        if self.supabase_url:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(
                    f"{self.supabase_url}/storage/v1/object/{self.bucket}/{key}",
                    headers={**self._headers(), "Content-Type": mime_type, "x-upsert": "false"},
                    content=content,
                )
            response.raise_for_status()
            return
        path = self.root / key.removeprefix("_beta-bug-reports/")
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)

    async def read(self, key: str) -> bytes:
        if self.supabase_url:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.get(
                    f"{self.supabase_url}/storage/v1/object/{self.bucket}/{key}", headers=self._headers()
                )
            if response.status_code == 404:
                raise FileNotFoundError(key)
            response.raise_for_status()
            return response.content
        path = self.root / key.removeprefix("_beta-bug-reports/")
        if not path.is_file():
            raise FileNotFoundError(key)
        return await asyncio.to_thread(path.read_bytes)
