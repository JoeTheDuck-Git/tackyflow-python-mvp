from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.workspace import resolve_workspace_id
from app.documents import DocumentExtractionError, extract_document_text


MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class DocumentExtractionRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=12_000_000)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        normalized = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not normalized or normalized in {".", ".."}:
            raise ValueError("invalid filename")
        return normalized


def build_document_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

    @router.post("/extract")
    async def extract_document(
        payload: DocumentExtractionRequest,
        _workspace_id: str = Depends(resolve_workspace_id),
    ) -> dict[str, object]:
        try:
            data = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="檔案編碼無效。") from exc
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="單一檔案不可超過 8 MB。")
        try:
            return extract_document_text(payload.filename, data)
        except DocumentExtractionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
