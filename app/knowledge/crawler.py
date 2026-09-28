from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
from importlib.util import find_spec
from ipaddress import ip_address, ip_network
import os
from pathlib import Path
import socket
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

# Crawl4AI creates its cache at import time. Keep it project-scoped so tests,
# containers and restricted runtimes never write to a user's home directory.
os.environ.setdefault(
    "CRAWL4_AI_BASE_DIRECTORY",
    str(Path(__file__).resolve().parents[2] / "data"),
)

from app.knowledge.models import CrawledDocument


class CrawlError(RuntimeError):
    pass


class WebCrawler(Protocol):
    async def crawl(self, url: str, workspace_id: str) -> CrawledDocument: ...


class _ReadableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self.parts: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        if normalized == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript", "svg"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        if normalized == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or self._ignored_depth:
            return
        self.parts.append(text)
        if self._in_title:
            self.title_parts.append(text)


class HttpxWebCrawler:
    """Small serverless-safe crawler used when a browser runtime is unavailable."""

    method = "httpx"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
        max_chars: int = 120_000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_chars = max_chars
        self.transport = transport

    async def crawl(self, url: str, workspace_id: str) -> CrawledDocument:
        current_url = url
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
            headers={"User-Agent": "TackyFlowKnowledgeBot/1.0"},
        ) as client:
            for _ in range(6):
                _assert_public_url(current_url)
                response = await client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise CrawlError("redirect is missing a destination")
                    current_url = urljoin(current_url, location)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise CrawlError(f"upstream returned HTTP {response.status_code}") from exc
                content_type = response.headers.get("content-type", "").casefold()
                if not (
                    content_type.startswith("text/")
                    or "json" in content_type
                    or "xml" in content_type
                ):
                    raise CrawlError("URL did not return readable text")
                raw = response.text[: self.max_chars * 4]
                parser = _ReadableTextParser()
                parser.feed(raw)
                extracted = (
                    "\n".join(parser.parts)
                    if "html" in content_type
                    else raw
                ).strip()
                content = extracted[: self.max_chars]
                if len(content) < 40:
                    raise CrawlError("crawler returned insufficient content")
                title = " ".join(parser.title_parts)[:500]
                truncated = len(extracted) > self.max_chars
                completeness = (
                    0.85
                    if truncated
                    else 0.95
                    if len(content) >= 800
                    else 0.8
                    if len(content) >= 300
                    else 0.6
                )
                confidence = 0.82 if "html" in content_type else 0.76
                return CrawledDocument(
                    workspace_id=workspace_id,
                    source_url=current_url,
                    title=title,
                    content=content,
                    content_hash=sha256(content.encode("utf-8")).hexdigest(),
                    metadata={
                        "crawler": "httpx",
                        "content_type": content_type[:200],
                        "content_length": len(content),
                        "truncated": truncated,
                        "completeness": completeness,
                        "confidence": confidence,
                        "excerpt": content[:500],
                        "requires_human_review": completeness < 0.7 or confidence < 0.75,
                    },
                )
        raise CrawlError("too many redirects")


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CrawlError("only public http(s) URLs are supported")
    host = parsed.hostname.casefold()
    if host == "localhost" or host.endswith(".local"):
        raise CrawlError("local addresses are not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise CrawlError("hostname could not be resolved") from exc
    for address in addresses:
        candidate = ip_address(address)
        # Clash fake-IP mode maps every public hostname into 198.18.0.0/15.
        # This bypass is opt-in for local development only; production must use
        # real DNS so SSRF validation remains authoritative.
        if (
            candidate in ip_network("198.18.0.0/15")
            and os.getenv("CRAWL4AI_ALLOW_CLASH_FAKE_IP", "false").casefold()
            == "true"
        ):
            continue
        if not candidate.is_global:
            raise CrawlError("private or reserved addresses are not allowed")


class Crawl4AIWebCrawler:
    method = "crawl4ai"

    def __init__(self, *, timeout_ms: int = 30_000, max_chars: int = 120_000) -> None:
        self.timeout_ms = timeout_ms
        self.max_chars = max_chars

    async def crawl(self, url: str, workspace_id: str) -> CrawledDocument:
        from crawl4ai import (
            AsyncWebCrawler,
            BrowserConfig,
            CacheMode,
            CrawlerRunConfig,
        )

        _assert_public_url(url)
        browser = BrowserConfig(headless=True, verbose=False)
        run = CrawlerRunConfig(
            cache_mode=CacheMode.ENABLED,
            page_timeout=self.timeout_ms,
            remove_overlay_elements=True,
            word_count_threshold=20,
        )
        async with AsyncWebCrawler(config=browser) as crawler:
            result = await crawler.arun(url=url, config=run)
        if not result.success:
            raise CrawlError(str(result.error_message or "crawl failed")[:500])
        markdown = result.markdown
        content = str(
            getattr(markdown, "fit_markdown", None)
            or getattr(markdown, "raw_markdown", None)
            or markdown
            or ""
        ).strip()[: self.max_chars]
        if len(content) < 40:
            raise CrawlError("crawler returned insufficient content")
        metadata = dict(result.metadata or {})
        title = str(metadata.get("title") or "")[:500]
        return CrawledDocument(
            workspace_id=workspace_id,
            source_url=url,
            title=title,
            content=content,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            metadata={
                "crawler": "crawl4ai",
                "description": str(metadata.get("description") or "")[:1000],
                "content_length": len(content),
                "truncated": len(content) >= self.max_chars,
                "completeness": 0.86 if len(content) >= self.max_chars else 0.95,
                "confidence": 0.9,
                "excerpt": content[:500],
                "requires_human_review": False,
            },
        )


def build_web_crawler(mode: str = "auto") -> WebCrawler:
    normalized = mode.strip().casefold()
    if normalized == "httpx":
        return HttpxWebCrawler()
    if normalized == "crawl4ai":
        if find_spec("crawl4ai") is None:
            raise RuntimeError(
                "KNOWLEDGE_CRAWLER=crawl4ai requires the optional crawler dependency"
            )
        return Crawl4AIWebCrawler()
    if normalized == "auto":
        return Crawl4AIWebCrawler() if find_spec("crawl4ai") else HttpxWebCrawler()
    raise ValueError("KNOWLEDGE_CRAWLER must be auto, httpx, or crawl4ai")
