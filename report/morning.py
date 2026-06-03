"""Pre-market catalyst report — CLI entrypoint.

Triggered by main.py morning-gap path as a detached subprocess. Reads a
snapshot JSON sidecar (per-ticker gap%, price, market cap, first-seen
offset), fans out DeepSeek + Tavily catalyst analysis per ticker, appends
to output/Reports/<date>_us_premarket.md, then pushes ntfy."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import anthropic

from report.llm import LLMBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnapshotEntry:
    """One pre-market gapper as captured at scan time. All numeric fields
    are optional because main.py builds these from Futu snapshots that
    occasionally have null cells."""

    ticker: str
    company_name: str | None = None
    gap_pct: float | None = None
    last_price: float | None = None
    market_cap: float | None = None
    first_seen_offset_minutes: int | None = None


def write_snapshot(path: Path, entries: list[SnapshotEntry]) -> None:
    payload = [asdict(e) for e in entries]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_snapshot(path: Path) -> list[SnapshotEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        SnapshotEntry(
            ticker=row["ticker"],
            company_name=row.get("company_name"),
            gap_pct=row.get("gap_pct"),
            last_price=row.get("last_price"),
            market_cap=row.get("market_cap"),
            first_seen_offset_minutes=row.get("first_seen_offset_minutes"),
        )
        for row in raw
    ]


RETRY_BACKOFF_SECONDS = 5.0
PER_CALL_TIMEOUT_SECONDS = 180.0

CATALYST_FAILURE_PLACEHOLDER = (
    "### 主催化剂\n\n[分析失败: {exc_type}]\n\n"
    "### 证据\n\n—\n\n"
    "### 分类\n\n**其他**\n\n"
    "### 提示\n\n—\n"
)


def _fmt(value: Any, *, default: str = "?") -> str:
    return default if value is None else str(value)


def build_user_message(entry: SnapshotEntry) -> str:
    """Serialize one snapshot entry into the user message for DeepSeek.
    The structured snapshot is shown to the model for grounding but the
    system prompt forbids reprinting it."""
    payload = {
        "ticker": entry.ticker,
        "company_name": entry.company_name,
        "gap_pct": entry.gap_pct,
        "last_price": entry.last_price,
        "market_cap": entry.market_cap,
        "first_seen_offset_minutes": entry.first_seen_offset_minutes,
    }
    return (
        f"Ticker: {entry.ticker}\n\n"
        f"Pre-market snapshot (for grounding only — do NOT reprint):\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n\n"
        f"Identify the most likely catalyst behind today's pre-market gap. "
        f"Issue ≤3 web_search calls in English. Emit the 4 sections per the "
        f"system prompt."
    )


async def analyze_catalyst(
    backend: LLMBackend,
    system_prompt: str,
    entry: SnapshotEntry,
    semaphore: asyncio.Semaphore,
) -> str:
    """Single-ticker DeepSeek call with one retry. Returns the model's
    4-section markdown, or the catalyst-shaped placeholder on failure."""
    user_msg = build_user_message(entry)
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            async with semaphore:
                text = await asyncio.wait_for(
                    backend.analyze(system_prompt, user_msg),
                    timeout=PER_CALL_TIMEOUT_SECONDS,
                )
            if not text:
                raise RuntimeError("empty response")
            return text
        except anthropic.APIStatusError as e:
            status = getattr(e, "status_code", None)
            retriable = status is None or status >= 500 or status in (408, 429)
            if not retriable:
                logger.error(
                    f"[morning] {entry.ticker}: non-retriable HTTP {status}: {e}"
                )
                return CATALYST_FAILURE_PLACEHOLDER.format(
                    exc_type=f"HTTP{status}"
                )
            last_error = e
            logger.warning(
                f"[morning] {entry.ticker}: attempt {attempt}: HTTP {status}: {e}"
            )
        except (
            anthropic.APIConnectionError,
            asyncio.TimeoutError,
            RuntimeError,
        ) as e:
            last_error = e
            logger.warning(
                f"[morning] {entry.ticker}: attempt {attempt}: "
                f"{type(e).__name__}: {e}"
            )
        if attempt == 1:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
    return CATALYST_FAILURE_PLACEHOLDER.format(
        exc_type=type(last_error).__name__ if last_error else "Unknown"
    )
