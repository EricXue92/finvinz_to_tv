"""Async Anthropic call with web_search tool, system-prompt cache, and retry."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
# 8 sections × ~250 Chinese tokens each + Snapshot table + headings ≈ 2300 output
# tokens. 1500 was empirically truncating 12/14 reports mid-section. 2800 leaves
# headroom without uncapping cost. Cost per ticker ≈ $0.21 output (was $0.11);
# daily cap stays under spec's $25/market.
MAX_TOKENS = 2800
WEB_SEARCH_MAX_USES = 3
RETRY_BACKOFF_SECONDS = 5.0
PER_CALL_TIMEOUT_SECONDS = 90.0


def build_user_message(data: dict[str, Any]) -> str:
    """Serialize the enrichment dict as a structured prompt for the model."""
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return (
        f"Ticker: {data['ticker']}  |  Group: {data['group']}  |  Exchange: {data['exchange']}\n\n"
        f"Structured data (use these numbers verbatim in the Snapshot block; for any field that is "
        f"null, write '信息不足' in the qualitative analysis):\n\n```json\n{payload}\n```\n\n"
        f"Generate the Markdown section per the template in the system prompt. Use the web_search "
        f"tool sparingly (≤3 calls) for the qualitative legs."
    )


def _extract_text(response: Any) -> str:
    """Concatenate all text blocks in the response, ignoring tool_use blocks.
    Strip any model preamble before the first H2 heading — Opus sometimes adds
    a "Let me research X" sentence even when the system prompt forbids it."""
    parts: list[str] = []
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    text = "".join(parts).strip()
    idx = text.find("\n## ")
    if idx == -1 and text.startswith("## "):
        return text
    if idx != -1:
        return text[idx + 1 :].strip()
    return text


async def analyze_ticker(
    client: Any,
    system_prompt: str,
    data: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> str:
    """Call Anthropic for one ticker. On retry exhaustion, return a placeholder
    Markdown section so the renderer never sees a missing entry."""
    user_msg = build_user_message(data)
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            async with semaphore:
                response = await asyncio.wait_for(
                    client.messages.create(
                        model=MODEL,
                        max_tokens=MAX_TOKENS,
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
                                "max_uses": WEB_SEARCH_MAX_USES,
                            }
                        ],
                        messages=[{"role": "user", "content": user_msg}],
                    ),
                    timeout=PER_CALL_TIMEOUT_SECONDS,
                )
            text = _extract_text(response)
            if not text:
                raise RuntimeError("empty response")
            return text
        except anthropic.APIStatusError as e:
            # Retry only transient HTTP failures: 5xx server errors, 408 timeout,
            # 429 rate limit. 4xx (401 bad key, 400 malformed, 404 wrong model) are
            # configuration bugs — fail loudly with a distinct placeholder rather
            # than burn a retry and produce N misleading "[分析失败]" sections.
            status = getattr(e, "status_code", None)
            retriable = status is None or status >= 500 or status in (408, 429)
            if not retriable:
                logger.error(
                    f"[analyst] {data['ticker']}: non-retriable HTTP {status}: {e}"
                )
                return (
                    f"## {data['ticker']} — {data.get('company_name') or '?'} "
                    f"({data['exchange']} · {data['group']})\n\n"
                    f"[配置错误: HTTP {status}: {e}]\n"
                )
            last_error = e
            logger.warning(
                f"[analyst] {data['ticker']}: attempt {attempt} failed: "
                f"HTTP {status}: {e}"
            )
            if attempt == 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        except (
            anthropic.APIConnectionError,
            asyncio.TimeoutError,
            RuntimeError,
        ) as e:
            last_error = e
            logger.warning(
                f"[analyst] {data['ticker']}: attempt {attempt} failed: "
                f"{type(e).__name__}: {e}"
            )
            if attempt == 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
    return (
        f"## {data['ticker']} — {data.get('company_name') or '?'} "
        f"({data['exchange']} · {data['group']})\n\n"
        f"[分析失败: {type(last_error).__name__}: {last_error}]\n"
    )
