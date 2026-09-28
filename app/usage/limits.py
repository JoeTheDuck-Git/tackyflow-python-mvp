from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RequestRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-process MVP limiter; use a shared store before multi-instance deployment."""

    def __init__(self, app, *, requests_per_minute: int) -> None:
        super().__init__(app)
        self.limit = max(1, requests_per_minute)
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        key = client
        now = time.monotonic()
        bucket = self._requests[key]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= self.limit:
            retry_after = max(1, int(60 - (now - bucket[0])))
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": "請求過於頻繁，請稍後再試。",
                    "code": "request_rate_limited",
                    "retry_after_seconds": retry_after,
                },
            )
        bucket.append(now)
        return await call_next(request)
