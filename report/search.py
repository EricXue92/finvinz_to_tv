"""Minimal Tavily web-search client for report backends that lack a built-in
web_search tool (e.g. DeepSeek). Anthropic's native `web_search_20250305` is
the preferred path on the Anthropic backend — this module is only used when
the LLM backend has no first-party search.

Direct httpx call against https://api.tavily.com/search; we don't pull in
`tavily-python` to avoid an extra dependency for a single POST."""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT = 30.0


class TavilyClient:
    """Async wrapper around the Tavily Search API."""

    def __init__(self, api_key: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TavilyClient":
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def search(
        self,
        query: str,
        *,
        topic: str = "finance",
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> str:
        """Run one Tavily query, return a compact text block ready to paste
        into a tool_result. Empty string on failure (we never raise — the
        analyst should keep going with whatever context it already has)."""
        if self._http is None:
            raise RuntimeError("TavilyClient must be used as an async context manager")
        payload = {
            "query": query,
            "topic": topic,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": "basic",
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = await self._http.post(TAVILY_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"[search] tavily query failed ({query!r}): {e}")
            return ""
        return _format_results(data)


def _format_results(data: dict[str, Any]) -> str:
    """Turn the Tavily JSON into a small markdown-ish block — enough for the
    model to ground its prose without dumping raw HTML."""
    parts: list[str] = []
    answer = (data.get("answer") or "").strip()
    if answer:
        parts.append(f"Summary: {answer}")
    for r in data.get("results") or []:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        content = (r.get("content") or "").strip()
        if not (title or content):
            continue
        parts.append(f"- [{title}]({url})\n  {content}")
    return "\n\n".join(parts) if parts else "(no results)"
