"""LLM backend abstraction for the daily report.

Two implementations share the Anthropic Python SDK because DeepSeek exposes an
Anthropic-compatible endpoint at https://api.deepseek.com/anthropic — only the
`base_url` differs. The split lives in *how each backend gets web context*:

- AnthropicBackend uses the first-party `web_search_20250305` server tool —
  the model issues searches, Anthropic resolves them, the SDK returns the
  final text in one round trip.
- DeepSeekBackend has no first-party search tool, so it runs a manual
  tool-use loop: it offers a `web_search` tool whose `input_schema` takes a
  query string, intercepts each `tool_use` block, calls Tavily, and feeds
  the result back as a `tool_result`. Same Anthropic SDK protocol on both
  sides — DeepSeek's compat layer accepts it.

Backends are constructed once per process by `build_backend()` from the
`[report]` config block. Callers `await backend.analyze(system, user)` and
the backend takes care of search / tool-loop / model-specific wiring."""
from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import anthropic

from report.search import TavilyClient

logger = logging.getLogger(__name__)

# Output budget — 8 sections × ~250 Chinese tokens each + headings ~ 3000 out.
DEFAULT_MAX_TOKENS = 3500


class LLMBackend(Protocol):
    """Run one ticker analysis. Implementations hold any per-process clients
    (SDK, http) opened in __init__ and torn down in aclose()."""

    name: str

    async def analyze(self, system_prompt: str, user_message: str) -> str: ...

    async def aclose(self) -> None: ...


def _extract_text(response: Any) -> str:
    """Concatenate text blocks; drop tool_use blocks. Strip any preamble
    before the first H3 heading (the system prompt forbids preamble but
    smaller models occasionally emit `Let me research X` anyway)."""
    parts: list[str] = []
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    text = "".join(parts).strip()
    if text.startswith("### ") or text.startswith("## "):
        return text
    candidates = []
    for marker in ("\n### ", "\n## "):
        i = text.find(marker)
        if i != -1:
            candidates.append(i)
    if candidates:
        return text[min(candidates) + 1 :].strip()
    return text


class AnthropicBackend:
    """Uses Anthropic's native `web_search_20250305` server tool. Single round
    trip: SDK resolves search internally and returns the final text."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        web_search_max_uses: int = 2,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._web_search_max_uses = web_search_max_uses

    async def aclose(self) -> None:
        await self._client.close()

    async def analyze(self, system_prompt: str, user_message: str) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self._web_search_max_uses,
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        return _extract_text(response)


# Custom (non-server) tool definition used by DeepSeek to call Tavily.
# Anthropic's compat layer treats this the same as any user-defined tool.
_DEEPSEEK_SEARCH_TOOL = {
    "name": "web_search",
    "description": (
        "Search the web for recent equity-research context: catalysts, "
        "policy/regulatory news, analyst ratings, social sentiment. "
        "Use sparingly (≤2 calls) for the qualitative legs only."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Concise English search query (company + topic).",
            }
        },
        "required": ["query"],
    },
}


class DeepSeekBackend:
    """DeepSeek via Anthropic-compatible endpoint with manual tool-use loop
    backed by Tavily. Up to `max_search_calls` searches per ticker; loop
    exits as soon as the model returns `end_turn`."""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        tavily_api_key: str,
        model: str = "deepseek-v4-flash",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_search_calls: int = 2,
        base_url: str = "https://api.deepseek.com/anthropic",
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)
        self._tavily = TavilyClient(tavily_api_key)
        self._tavily_ctx_open = False
        self._model = model
        self._max_tokens = max_tokens
        self._max_search_calls = max_search_calls

    async def aclose(self) -> None:
        await self._client.close()
        if self._tavily_ctx_open:
            await self._tavily.__aexit__(None, None, None)
            self._tavily_ctx_open = False

    async def _ensure_tavily(self) -> None:
        if not self._tavily_ctx_open:
            await self._tavily.__aenter__()
            self._tavily_ctx_open = True

    async def analyze(self, system_prompt: str, user_message: str) -> str:
        await self._ensure_tavily()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message}
        ]
        # Cap iterations at search budget + 1 (the +1 lets the model emit the
        # final assistant turn after its last search).
        for iteration in range(self._max_search_calls + 1):
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=[_DEEPSEEK_SEARCH_TOOL],
                messages=messages,
            )
            if getattr(response, "stop_reason", None) != "tool_use":
                return _extract_text(response)
            # Append assistant's tool_use turn verbatim, then a user turn
            # carrying tool_result blocks for every tool_use it emitted.
            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            for block in response.content or []:
                if getattr(block, "type", None) != "tool_use":
                    continue
                query = (block.input or {}).get("query", "") if isinstance(block.input, dict) else ""
                result_text = await self._tavily.search(query) if query else "(empty query)"
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text or "(no results)",
                    }
                )
            if not tool_results:
                # Defensive: stop_reason said tool_use but we found none.
                return _extract_text(response)
            messages.append({"role": "user", "content": tool_results})
        # Hit search budget — force one final no-tool turn so the model emits text.
        final = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        return _extract_text(final)


def build_backend(report_cfg: dict[str, Any] | None) -> LLMBackend:
    """Construct the backend named in `[report] backend = "..."`. Defaults to
    anthropic when the section is absent. Raises with a clear message when a
    required env var is missing — analyst.run will catch and soft-fail."""
    cfg = report_cfg or {}
    backend_name = (cfg.get("backend") or "anthropic").lower()

    if backend_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set (required for backend='anthropic')")
        sub = cfg.get("anthropic") or {}
        return AnthropicBackend(
            api_key=api_key,
            model=sub.get("model", "claude-sonnet-4-6"),
            max_tokens=int(sub.get("max_tokens", DEFAULT_MAX_TOKENS)),
            web_search_max_uses=int(sub.get("web_search_max_uses", 2)),
        )

    if backend_name == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        tavily_key = os.environ.get("TAVILY_API_KEY")
        missing = [n for n, v in (("DEEPSEEK_API_KEY", api_key), ("TAVILY_API_KEY", tavily_key)) if not v]
        if missing:
            raise RuntimeError(f"missing env var(s) for backend='deepseek': {', '.join(missing)}")
        sub = cfg.get("deepseek") or {}
        return DeepSeekBackend(
            api_key=api_key,
            tavily_api_key=tavily_key,
            model=sub.get("model", "deepseek-v4-flash"),
            max_tokens=int(sub.get("max_tokens", DEFAULT_MAX_TOKENS)),
            max_search_calls=int(sub.get("max_search_calls", 2)),
            base_url=sub.get("base_url", "https://api.deepseek.com/anthropic"),
        )

    raise ValueError(f"unknown report backend: {backend_name!r} (expected 'anthropic' or 'deepseek')")
