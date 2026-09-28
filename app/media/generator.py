from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

import httpx


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    asset_id: str
    path: Path
    model: str
    mime_type: str = "image/png"


class ImageGenerator(Protocol):
    model: str

    async def generate(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        prompt: str,
        aspect_ratio: str,
    ) -> GeneratedImage: ...

    def resolve_path(self, *, workspace_id: str, workflow_id: str, asset_id: str) -> Path: ...

    async def read(self, *, workspace_id: str, workflow_id: str, asset_id: str) -> bytes: ...


class OpenAIImageGenerator:
    """Generates an image and stores it outside the public static directory."""

    _SIZES = {
        "16:9": "1536x1024",
        "9:16": "1024x1536",
        "1:1": "1024x1024",
        "4:5": "1024x1536",
    }

    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float,
        storage_root: Path,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.storage_root = storage_root.resolve()
        self.client_factory = client_factory

    async def generate(
        self,
        *,
        workspace_id: str,
        workflow_id: str,
        prompt: str,
        aspect_ratio: str,
    ) -> GeneratedImage:
        return await asyncio.to_thread(
            self._generate_sync,
            workspace_id,
            workflow_id,
            prompt,
            aspect_ratio,
        )

    def _generate_sync(
        self,
        workspace_id: str,
        workflow_id: str,
        prompt: str,
        aspect_ratio: str,
    ) -> GeneratedImage:
        if self.client_factory is not None:
            client = self.client_factory()
        else:
            from openai import OpenAI

            client = OpenAI(timeout=self.timeout_seconds, max_retries=0)
        result = client.images.generate(
            model=self.model,
            prompt=prompt,
            size=self._SIZES.get(aspect_ratio, "1536x1024"),
            quality="medium",
            output_format="png",
        )
        item = result.data[0] if getattr(result, "data", None) else None
        encoded = getattr(item, "b64_json", None)
        if not encoded:
            raise RuntimeError("OpenAI image generation returned no image data")
        image_bytes = base64.b64decode(encoded, validate=True)
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("OpenAI image generation returned an invalid PNG")

        scope = hashlib.sha256(f"{workspace_id}:{workflow_id}".encode()).hexdigest()[:24]
        directory = (self.storage_root / scope).resolve()
        if self.storage_root not in directory.parents:
            raise RuntimeError("invalid generated media storage scope")
        directory.mkdir(parents=True, exist_ok=True)
        asset_id = str(uuid4())
        path = directory / f"{asset_id}.png"
        path.write_bytes(image_bytes)
        path.chmod(0o600)
        return GeneratedImage(asset_id=asset_id, path=path, model=self.model)

    def resolve_path(self, *, workspace_id: str, workflow_id: str, asset_id: str) -> Path:
        try:
            from uuid import UUID

            normalized_id = str(UUID(asset_id))
        except (ValueError, TypeError) as exc:
            raise FileNotFoundError("generated image not found") from exc
        scope = hashlib.sha256(f"{workspace_id}:{workflow_id}".encode()).hexdigest()[:24]
        path = (self.storage_root / scope / f"{normalized_id}.png").resolve()
        if self.storage_root not in path.parents:
            raise FileNotFoundError("generated image not found")
        return path

    async def read(self, *, workspace_id: str, workflow_id: str, asset_id: str) -> bytes:
        path = self.resolve_path(workspace_id=workspace_id, workflow_id=workflow_id, asset_id=asset_id)
        if not path.is_file():
            raise FileNotFoundError("generated image not found")
        return await asyncio.to_thread(path.read_bytes)


class SupabaseImageGenerator(OpenAIImageGenerator):
    """Generate with OpenAI and persist the binary in private Supabase Storage."""

    def __init__(self, *, supabase_url: str, service_role_key: str, bucket: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.supabase_url=supabase_url.rstrip("/")
        self.service_role_key=service_role_key
        self.bucket=bucket
        self._ensure_bucket()

    def _headers(self) -> dict[str,str]:
        return {"apikey":self.service_role_key,"Authorization":f"Bearer {self.service_role_key}"}

    def _ensure_bucket(self) -> None:
        with httpx.Client(timeout=20, trust_env=False) as client:
            existing=client.get(f"{self.supabase_url}/storage/v1/bucket/{self.bucket}",headers=self._headers())
            if existing.status_code == 200:
                return
            response=client.post(f"{self.supabase_url}/storage/v1/bucket",headers=self._headers(),json={"id":self.bucket,"name":self.bucket,"public":False})
        if response.status_code not in {200,201,409}:
            response.raise_for_status()

    def _object_key(self, workspace_id: str, workflow_id: str, asset_id: str) -> str:
        scope=hashlib.sha256(f"{workspace_id}:{workflow_id}".encode()).hexdigest()[:24]
        return f"{scope}/{asset_id}.png"

    def _generate_sync(self, workspace_id: str, workflow_id: str, prompt: str, aspect_ratio: str) -> GeneratedImage:
        if self.client_factory is not None: client=self.client_factory()
        else:
            from openai import OpenAI
            client=OpenAI(timeout=self.timeout_seconds,max_retries=0)
        result=client.images.generate(model=self.model,prompt=prompt,size=self._SIZES.get(aspect_ratio,"1536x1024"),quality="medium",output_format="png")
        item=result.data[0] if getattr(result,"data",None) else None
        encoded=getattr(item,"b64_json",None)
        if not encoded: raise RuntimeError("OpenAI image generation returned no image data")
        image_bytes=base64.b64decode(encoded,validate=True)
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"): raise RuntimeError("OpenAI image generation returned an invalid PNG")
        asset_id=str(uuid4()); key=self._object_key(workspace_id,workflow_id,asset_id)
        with httpx.Client(timeout=60, trust_env=False) as client:
            response=client.post(f"{self.supabase_url}/storage/v1/object/{self.bucket}/{key}",headers={**self._headers(),"Content-Type":"image/png","x-upsert":"false"},content=image_bytes)
        response.raise_for_status()
        return GeneratedImage(asset_id=asset_id,path=Path(key),model=self.model)

    def resolve_path(self, *, workspace_id: str, workflow_id: str, asset_id: str) -> Path:
        return Path(self._object_key(workspace_id,workflow_id,asset_id))

    async def read(self, *, workspace_id: str, workflow_id: str, asset_id: str) -> bytes:
        try:
            from uuid import UUID
            normalized=str(UUID(asset_id))
        except (ValueError,TypeError) as exc: raise FileNotFoundError("generated image not found") from exc
        key=self._object_key(workspace_id,workflow_id,normalized)
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response=await client.get(f"{self.supabase_url}/storage/v1/object/{self.bucket}/{key}",headers=self._headers())
        if response.status_code==404: raise FileNotFoundError("generated image not found")
        response.raise_for_status(); return response.content
