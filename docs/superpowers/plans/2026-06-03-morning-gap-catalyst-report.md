# Morning-Gap Catalyst Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a US pre-market catalyst report that runs as a detached subprocess after `morning-gap` finds fresh tickers, uses DeepSeek + Tavily to identify *why* each ticker gapped up, and pushes an ntfy link when ready.

**Architecture:** New module `report/morning.py` (CLI entrypoint) + `report/morning_renderer.py` (rendering). Reuses `report.llm.DeepSeekBackend` and `report.search.TavilyClient`. Triggered by `main.py` morning-gap path via `subprocess.Popen` with a JSON snapshot sidecar. Single same-day file appended across -20 / -10 scans.

**Tech Stack:** Python 3.12, asyncio, anthropic SDK (DeepSeek compat), Tavily (httpx), ntfy.sh, pytest.

Spec: `docs/superpowers/specs/2026-06-03-morning-gap-catalyst-report-design.md`.

---

### Task 1: Catalyst system prompt

**Files:**
- Create: `prompts/morning_gap_catalyst_system.md`

- [ ] **Step 1: Write the prompt file**

Create `prompts/morning_gap_catalyst_system.md` with this exact content:

```markdown
# Pre-market Catalyst Report — System Prompt

You are an equity research analyst writing a short *catalyst identification* note for one US-listed stock that is gapping up in pre-market. The structured snapshot table (gap%, price, market cap, first-seen offset) is rendered separately by the report generator — **you do NOT emit any tables, ticker headers, or snapshot text**. Your output is the 4 sections below, each as an H3 (`### `) heading followed by a paragraph or short list.

## Hard formatting rules

1. **Your response begins with `### 主催化剂` and contains nothing else** — no preamble, no progress narration, no ticker H2 heading, no snapshot table, no closing remarks.
2. **Always emit a blank line** between every `### ` heading and the paragraph/list that follows.
3. **Always emit a blank line** between sections.
4. **English source language — STRICT.** Issue every `web_search` query in English. Cite English-language sources ONLY: Bloomberg, Reuters, WSJ, FT, CNBC, Barron's, Yahoo Finance news, company IR / SEC 8-K filings, official press releases. **Do NOT** cite Chinese-language financial portals (东方财富 / 雪球 / 新浪财经 / 同花顺 / 36氪 / 华尔街见闻) — translation latency makes them unreliable for breaking pre-market news. The qualitative prose itself is in Simplified Chinese; the underlying evidence must come from English sources.
5. Use the `web_search` tool sparingly (≤3 calls per ticker). Suggested query templates — pick 2–3 most relevant:
   - `<ticker> news today`
   - `<ticker> pre-market <YYYY-MM-DD>`
   - `<ticker> earnings beat OR miss`
   - `<ticker> analyst upgrade OR downgrade <bank-name>`
   - `<ticker> partnership OR acquisition OR merger`
   - `<ticker> FDA approval OR clinical trial`
6. **No fabrication.** If your searches do NOT surface a clear catalyst, classify as `其他` and write `主催化剂` as `信息不足 — 可能为板块联动 / 技术性突破 / 散户情绪`. Never invent earnings dates, analyst names, partnership details, or headlines.
7. Numbers, company / ticker / bank / index names, and headlines stay in English (e.g. `$3.4T`, `+12.4%`, `Goldman Sachs upgrade`, `8-K`).

## The 4 sections (in this exact order)

```
### 主催化剂

1–2 句中文。点名最可能的事件类型 + 一句话的"为什么是这个事件"。

### 证据

2–3 条最权威的英文源，每条独占一行，格式：
- [<source>: <headline>](url) — 1 句话摘要

如果实在没有可信源，写一行 `- 无可信英文源`。

### 分类

从下列封闭标签集中选 1–3 个，以 ` · ` 分隔：
**财报** / **评级** / **合作** / **收购** / **FDA** / **政策** / **股东** / **散户驱动** / **其他**

例：`**财报** · **评级**`。不要发明新标签。

### 提示

1 句中文。给出后续观察点：催化剂强度判断、是否财报后第 N 个交易日、盘前已反映多少、需要确认的下一步信号。
```

## What goes in each section — examples

**主催化剂 (good):**
> 公司昨晚收盘后发布 Q3 财报，EPS $2.14 远超共识 $1.87，营收同比 +47%。盘前跳空主要由业绩超预期 + 全年指引上调驱动。

**主催化剂 (bad — fabricated):**
> 公司被 Goldman Sachs 上调评级（注：未在搜索中确认）。

**证据 (good):**
> - [Reuters: Acme beats Q3 EPS, raises FY guide](https://reuters.com/...) — EPS $2.14 vs $1.87 cons; FY26 sales guide raised to $7.03B from $6.85B.
> - [CNBC: Acme shares jump 12% pre-market on Q3 beat](https://cnbc.com/...) — pre-market session up 12.4% after earnings release.

**分类 — when to pick what:**
- `财报` = quarterly earnings release within the last 24 hours.
- `评级` = analyst upgrade / downgrade / target raise from a covered bank.
- `合作` = partnership / commercial deal announcement.
- `收购` = M&A — either side (acquirer or target).
- `FDA` = regulatory approval / phase results / black-box warning.
- `政策` = government action — tariffs, subsidies, sanctions, executive orders.
- `股东` = insider buying / activist stake / buyback / dividend change.
- `散户驱动` = no fundamental news but heavy retail / social activity (WSB, Stocktwits trending).
- `其他` = catch-all, including "信息不足".
```

- [ ] **Step 2: Commit**

```bash
git add prompts/morning_gap_catalyst_system.md
git commit -m "feat(prompts): add morning-gap catalyst system prompt"
```

---

### Task 2: Snapshot sidecar I/O

**Files:**
- Create: `report/morning.py` (initial skeleton)
- Create: `tests/test_report_morning.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_morning.py`:

```python
"""Tests for the pre-market catalyst report (report/morning.py)."""
from __future__ import annotations

import json
from pathlib import Path

from report.morning import SnapshotEntry, read_snapshot, write_snapshot


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    entries = [
        SnapshotEntry(
            ticker="NVDA",
            company_name="NVIDIA Corp",
            gap_pct=12.4,
            last_price=138.20,
            market_cap=3.4e12,
            first_seen_offset_minutes=-20,
        ),
        SnapshotEntry(
            ticker="TWLO",
            company_name=None,
            gap_pct=19.5,
            last_price=82.10,
            market_cap=1.3e10,
            first_seen_offset_minutes=-20,
        ),
    ]
    path = tmp_path / "snap.json"
    write_snapshot(path, entries)
    assert path.exists()
    loaded = read_snapshot(path)
    assert loaded == entries


def test_snapshot_read_missing_fields_defaults_to_none(tmp_path: Path) -> None:
    path = tmp_path / "snap.json"
    path.write_text(json.dumps([{"ticker": "AAPL"}]), encoding="utf-8")
    loaded = read_snapshot(path)
    assert loaded == [
        SnapshotEntry(
            ticker="AAPL",
            company_name=None,
            gap_pct=None,
            last_price=None,
            market_cap=None,
            first_seen_offset_minutes=None,
        )
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_report_morning.py::test_snapshot_roundtrip -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report.morning'`

- [ ] **Step 3: Create the module with snapshot I/O**

Create `report/morning.py`:

```python
"""Pre-market catalyst report — CLI entrypoint.

Triggered by main.py morning-gap path as a detached subprocess. Reads a
snapshot JSON sidecar (per-ticker gap%, price, market cap, first-seen
offset), fans out DeepSeek + Tavily catalyst analysis per ticker, appends
to output/Reports/<date>_us_premarket.md, then pushes ntfy."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_report_morning.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add report/morning.py tests/test_report_morning.py
git commit -m "feat(report): add snapshot sidecar I/O for morning catalyst report"
```

---

### Task 3: Per-ticker catalyst analysis with placeholder

**Files:**
- Modify: `report/morning.py` (add `analyze_catalyst`)
- Modify: `tests/test_report_morning.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_morning.py`:

```python
import asyncio
from unittest.mock import AsyncMock

import anthropic

from report.morning import (
    CATALYST_FAILURE_PLACEHOLDER,
    analyze_catalyst,
    build_user_message,
)


def test_build_user_message_includes_ticker_and_snapshot() -> None:
    entry = SnapshotEntry(
        ticker="NVDA",
        company_name="NVIDIA Corp",
        gap_pct=12.4,
        last_price=138.20,
        market_cap=3.4e12,
        first_seen_offset_minutes=-20,
    )
    msg = build_user_message(entry)
    assert "NVDA" in msg
    assert "NVIDIA Corp" in msg
    assert "12.4" in msg
    assert "-20" in msg


async def test_analyze_catalyst_returns_backend_text_on_success() -> None:
    backend = AsyncMock()
    backend.analyze = AsyncMock(return_value="### 主催化剂\n\nOK.")
    sem = asyncio.Semaphore(1)
    entry = SnapshotEntry(ticker="NVDA")
    result = await analyze_catalyst(backend, "<sys>", entry, sem)
    assert result.startswith("### 主催化剂")


async def test_analyze_catalyst_returns_placeholder_on_repeated_failure() -> None:
    backend = AsyncMock()
    backend.analyze = AsyncMock(
        side_effect=anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    )
    sem = asyncio.Semaphore(1)
    entry = SnapshotEntry(ticker="NVDA")
    result = await analyze_catalyst(backend, "<sys>", entry, sem)
    assert result == CATALYST_FAILURE_PLACEHOLDER.format(exc_type="APIConnectionError")


async def test_analyze_catalyst_returns_placeholder_on_empty_response() -> None:
    backend = AsyncMock()
    backend.analyze = AsyncMock(return_value="")
    sem = asyncio.Semaphore(1)
    entry = SnapshotEntry(ticker="NVDA")
    result = await analyze_catalyst(backend, "<sys>", entry, sem)
    assert result == CATALYST_FAILURE_PLACEHOLDER.format(exc_type="RuntimeError")
```

Note: `pyproject.toml` sets `asyncio_mode = "auto"`, so plain `async def test_...` works — no `pytestmark` marker needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_report_morning.py -v`
Expected: FAIL (`ImportError: cannot import name 'analyze_catalyst'`).

- [ ] **Step 3: Add catalyst analysis to `report/morning.py`**

Append to `report/morning.py`:

```python
import asyncio
from typing import Any

import anthropic

from report.llm import LLMBackend

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_report_morning.py -v`
Expected: PASS (5 tests).

If the timeout test (`...empty_response`) hangs, the retry sleep is the culprit. Patch `RETRY_BACKOFF_SECONDS` to `0` in the test using `monkeypatch.setattr`:

```python
async def test_analyze_catalyst_returns_placeholder_on_empty_response(monkeypatch) -> None:
    from report import morning
    monkeypatch.setattr(morning, "RETRY_BACKOFF_SECONDS", 0)
    ...
```

Apply the same patch to the `APIConnectionError` test.

- [ ] **Step 5: Commit**

```bash
git add report/morning.py tests/test_report_morning.py
git commit -m "feat(report): per-ticker catalyst analysis with retry + placeholder"
```

---

### Task 4: Catalyst renderer (initial document)

**Files:**
- Create: `report/morning_renderer.py`
- Create: `tests/test_report_morning_renderer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_morning_renderer.py`:

```python
"""Tests for report/morning_renderer.py."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from report.morning import SnapshotEntry
from report.morning_renderer import (
    render_append_section,
    render_initial_document,
)


def _entry() -> SnapshotEntry:
    return SnapshotEntry(
        ticker="NVDA",
        company_name="NVIDIA Corp",
        gap_pct=12.4,
        last_price=138.20,
        market_cap=3.4e12,
        first_seen_offset_minutes=-20,
    )


def test_render_initial_document_has_header_and_ticker_block() -> None:
    md = render_initial_document(
        date_iso="2026-06-03",
        offset_min=-20,
        entries=[_entry()],
        sections=["### 主催化剂\n\n爆款财报。\n\n### 证据\n\n- [Reuters: x](u)\n\n### 分类\n\n**财报**\n\n### 提示\n\n第 1 个交易日。\n"],
        model_label="deepseek-v4-pro (DeepSeek)",
        generated_at=datetime(2026, 6, 3, 9, 12, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        skipped_count=0,
    )
    assert "# Pre-market Catalyst Report — 2026-06-03 (US)" in md
    assert "## NVDA — NVIDIA Corp" in md
    assert "+12.4%" in md
    assert "$138.20" in md
    assert "$3.40T" in md  # market cap formatting
    assert "-20m" in md
    assert "**财报**" in md
    assert "deepseek-v4-pro (DeepSeek)" in md


def test_render_initial_document_includes_truncation_note_when_skipped() -> None:
    md = render_initial_document(
        date_iso="2026-06-03",
        offset_min=-20,
        entries=[_entry()],
        sections=["### 主催化剂\n\nx\n"],
        model_label="x (DeepSeek)",
        generated_at=datetime(2026, 6, 3, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        skipped_count=3,
    )
    assert "截断: 3 票按 gap% 排序后被丢弃" in md


def test_render_append_section_emits_divider_and_blocks() -> None:
    md = render_append_section(
        offset_min=-10,
        et_time_hhmm="09:20",
        entries=[_entry()],
        sections=["### 主催化剂\n\nx\n"],
    )
    assert "## 增补 (-10min, 09:20 ET)" in md
    assert "### NVDA — NVIDIA Corp" in md  # appends as H3 under the divider H2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_report_morning_renderer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report.morning_renderer'`.

- [ ] **Step 3: Implement the renderer**

Create `report/morning_renderer.py`:

```python
"""Markdown rendering for the pre-market catalyst report.

Two entry points: `render_initial_document` writes the full file
(header + per-ticker blocks + footer) for the first scan of the day;
`render_append_section` returns only the divider + per-ticker blocks for
later scans (appended byte-wise by the caller)."""
from __future__ import annotations

from datetime import datetime

from report.morning import SnapshotEntry


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _fmt_price(v: float | None) -> str:
    return "—" if v is None else f"${v:.2f}"


def _fmt_mcap(v: float | None) -> str:
    if v is None:
        return "—"
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if v >= scale:
            return f"${v / scale:.2f}{unit}"
    return f"${v:.0f}"


def _fmt_offset(v: int | None) -> str:
    return "?" if v is None else f"{v}m"


def _company(entry: SnapshotEntry) -> str:
    return entry.company_name or entry.ticker


def _snapshot_row(entry: SnapshotEntry) -> str:
    return (
        f"| {_fmt_pct(entry.gap_pct)} "
        f"| {_fmt_price(entry.last_price)} "
        f"| {_fmt_mcap(entry.market_cap)} "
        f"| {_fmt_offset(entry.first_seen_offset_minutes)} |"
    )


def _ticker_block_initial(entry: SnapshotEntry, section: str) -> str:
    """First-scan block: H2 ticker header + snapshot table + 4 sections."""
    return (
        f"## {entry.ticker} — {_company(entry)}  "
        f"(Pre-market {_fmt_pct(entry.gap_pct)} · {_fmt_offset(entry.first_seen_offset_minutes)})\n\n"
        "| Gap | Price | Mkt Cap | First seen |\n"
        "|---|---|---|---|\n"
        f"{_snapshot_row(entry)}\n\n"
        f"{section.strip()}\n"
    )


def _ticker_block_append(entry: SnapshotEntry, section: str) -> str:
    """Append-scan block: nested under the divider H2, so ticker header is H3."""
    return (
        f"### {entry.ticker} — {_company(entry)}  "
        f"(Pre-market {_fmt_pct(entry.gap_pct)} · {_fmt_offset(entry.first_seen_offset_minutes)})\n\n"
        "| Gap | Price | Mkt Cap | First seen |\n"
        "|---|---|---|---|\n"
        f"{_snapshot_row(entry)}\n\n"
        f"{section.strip()}\n"
    )


def render_initial_document(
    *,
    date_iso: str,
    offset_min: int,
    entries: list[SnapshotEntry],
    sections: list[str],
    model_label: str,
    generated_at: datetime,
    skipped_count: int,
) -> str:
    """Render a complete report document for the first scan of the day."""
    assert len(entries) == len(sections), "entries and sections must align"
    header = (
        f"# Pre-market Catalyst Report — {date_iso} (US)\n\n"
        f"Scan: {offset_min}min · Analyzed: {len(entries)}\n"
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M')} HKT\n\n"
    )
    if skipped_count > 0:
        header += f"> 截断: {skipped_count} 票按 gap% 排序后被丢弃\n\n"
    header += "---\n\n"
    body = "\n---\n\n".join(
        _ticker_block_initial(e, s) for e, s in zip(entries, sections)
    )
    footer = f"\n---\n\n_Model: {model_label}_\n"
    return header + body + footer


def render_append_section(
    *,
    offset_min: int,
    et_time_hhmm: str,
    entries: list[SnapshotEntry],
    sections: list[str],
) -> str:
    """Render just the append section for a follow-up scan. Caller is
    responsible for byte-appending this to the existing file."""
    assert len(entries) == len(sections), "entries and sections must align"
    divider = f"\n\n---\n\n## 增补 ({offset_min}min, {et_time_hhmm} ET)\n\n"
    body = "\n\n".join(
        _ticker_block_append(e, s) for e, s in zip(entries, sections)
    )
    return divider + body + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_report_morning_renderer.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add report/morning_renderer.py tests/test_report_morning_renderer.py
git commit -m "feat(report): catalyst report renderer (initial + append)"
```

---

### Task 5: Append-vs-create file logic

**Files:**
- Modify: `report/morning.py` (add `write_report` orchestration helper)
- Modify: `tests/test_report_morning.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_morning.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from report.morning import write_report


def test_write_report_creates_file_when_missing(tmp_path: Path) -> None:
    out = tmp_path / "2026_06_03_us_premarket.md"
    write_report(
        out_path=out,
        date_iso="2026-06-03",
        offset_min=-20,
        entries=[SnapshotEntry(ticker="NVDA", company_name="NVIDIA", gap_pct=12.4)],
        sections=["### 主催化剂\n\nx\n### 证据\n\n—\n### 分类\n\n**财报**\n### 提示\n\n—\n"],
        model_label="m (DeepSeek)",
        generated_at=datetime(2026, 6, 3, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        skipped_count=0,
        et_time_hhmm="09:10",
    )
    text = out.read_text(encoding="utf-8")
    assert "# Pre-market Catalyst Report" in text
    assert "## NVDA — NVIDIA" in text


def test_write_report_appends_when_file_exists(tmp_path: Path) -> None:
    out = tmp_path / "2026_06_03_us_premarket.md"
    out.write_text("# Pre-market Catalyst Report — 2026-06-03 (US)\n\nexisting content\n", encoding="utf-8")
    write_report(
        out_path=out,
        date_iso="2026-06-03",
        offset_min=-10,
        entries=[SnapshotEntry(ticker="TWLO", company_name="Twilio", gap_pct=8.0)],
        sections=["### 主催化剂\n\nx\n"],
        model_label="m (DeepSeek)",
        generated_at=datetime(2026, 6, 3, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        skipped_count=0,
        et_time_hhmm="09:20",
    )
    text = out.read_text(encoding="utf-8")
    assert "existing content" in text
    assert "## 增补 (-10min, 09:20 ET)" in text
    assert "### TWLO — Twilio" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_report_morning.py -v -k write_report`
Expected: FAIL (`cannot import name 'write_report'`).

- [ ] **Step 3: Add `write_report` to `report/morning.py`**

Append to `report/morning.py`:

```python
from datetime import datetime

from report.morning_renderer import (
    render_append_section,
    render_initial_document,
)


def write_report(
    *,
    out_path: Path,
    date_iso: str,
    offset_min: int,
    entries: list[SnapshotEntry],
    sections: list[str],
    model_label: str,
    generated_at: datetime,
    skipped_count: int,
    et_time_hhmm: str,
) -> None:
    """Create or append the day's catalyst report. Byte-level append, no
    parsing of prior content. Missing-parent dirs are created."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        block = render_append_section(
            offset_min=offset_min,
            et_time_hhmm=et_time_hhmm,
            entries=entries,
            sections=sections,
        )
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(block)
    else:
        doc = render_initial_document(
            date_iso=date_iso,
            offset_min=offset_min,
            entries=entries,
            sections=sections,
            model_label=model_label,
            generated_at=generated_at,
            skipped_count=skipped_count,
        )
        out_path.write_text(doc, encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_report_morning.py -v`
Expected: PASS (all tests including the 2 new `write_report` ones).

- [ ] **Step 5: Commit**

```bash
git add report/morning.py tests/test_report_morning.py
git commit -m "feat(report): create-or-append write_report helper"
```

---

### Task 6: ntfy push for catalyst-ready

**Files:**
- Modify: `notify.py` (add `notify_morning_catalyst_ready`)
- Create: `tests/test_notify_catalyst.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_notify_catalyst.py`:

```python
"""Tests for notify_morning_catalyst_ready."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from notify import notify_morning_catalyst_ready


def test_notify_morning_catalyst_ready_posts_when_enabled() -> None:
    cfg = {"notify": {"enabled": True, "ntfy_topic": "topic-xyz"}}
    with patch("notify._ntfy_post") as mock_post:
        notify_morning_catalyst_ready(
            report_path=Path("/tmp/2026_06_03_us_premarket.md"),
            offset_min=-20,
            n_tickers=5,
            config=cfg,
        )
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args[0], mock_post.call_args[1]
    args = mock_post.call_args.args
    assert args[1] == "topic-xyz"  # topic
    assert "-20min" in args[2]      # title
    assert "5" in args[2]
    assert "2026_06_03_us_premarket.md" in args[3]  # body


def test_notify_morning_catalyst_ready_skipped_when_disabled() -> None:
    cfg = {"notify": {"enabled": False, "ntfy_topic": "t"}}
    with patch("notify._ntfy_post") as mock_post:
        notify_morning_catalyst_ready(
            report_path=Path("/tmp/x.md"),
            offset_min=-10,
            n_tickers=1,
            config=cfg,
        )
    mock_post.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_notify_catalyst.py -v`
Expected: FAIL (`cannot import name 'notify_morning_catalyst_ready'`).

- [ ] **Step 3: Add the function to `notify.py`**

Append to `notify.py`:

```python
from pathlib import Path


def notify_morning_catalyst_ready(
    *,
    report_path: Path,
    offset_min: int,
    n_tickers: int,
    config: dict,
) -> None:
    """Push the second-stage ntfy when the catalyst report is written.

    Independent from `notify_morning_gap` — that one fires immediately
    with the ticker list; this one fires a few minutes later with the
    report link. Same ntfy topic, different title.
    """
    notify_cfg = config.get("notify") or {}
    if not notify_cfg.get("enabled", False):
        return

    topic = notify_cfg.get("ntfy_topic")
    if not topic:
        logger.warning("[Notify] ntfy_topic missing for catalyst report")
        return

    server = notify_cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    sign = "" if offset_min < 0 else "+"
    title = f"Catalyst Report Ready ({sign}{offset_min}min, {n_tickers} tickers)"
    body = str(report_path)
    _ntfy_post(server, topic, title, body, priority="default")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_notify_catalyst.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add notify.py tests/test_notify_catalyst.py
git commit -m "feat(notify): notify_morning_catalyst_ready ntfy push"
```

---

### Task 7: CLI entrypoint (`python -m report.morning`)

**Files:**
- Modify: `report/morning.py` (add `_run_async`, `main`, `__main__` guard)
- Modify: `tests/test_report_morning.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_report_morning.py`:

```python
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch


async def test_run_async_writes_report_and_pushes_notification(
    tmp_path: Path, monkeypatch
) -> None:
    snap = tmp_path / "snap.json"
    write_snapshot(
        snap,
        [SnapshotEntry(ticker="NVDA", company_name="NVIDIA", gap_pct=12.4,
                       last_price=138.2, market_cap=3.4e12,
                       first_seen_offset_minutes=-20)],
    )
    out_dir = tmp_path / "Reports"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dsk-test")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    fake_backend = MagicMock()
    fake_backend.analyze = AsyncMock(return_value="### 主催化剂\n\nx\n")
    fake_backend.aclose = AsyncMock()
    fake_backend.model_label = MagicMock(return_value="dsv4 (DeepSeek)")

    from report import morning
    monkeypatch.setattr(morning, "_build_deepseek_backend", lambda cfg: fake_backend)
    monkeypatch.setattr(morning, "OUTPUT_REPORTS_DIR", out_dir)
    notify_calls: list[dict] = []
    monkeypatch.setattr(
        morning,
        "notify_morning_catalyst_ready",
        lambda **kw: notify_calls.append(kw),
    )

    rc = await morning._run_async(
        snapshot_path=snap, date_iso="2026-06-03", offset_min=-20
    )

    assert rc == 0
    report = out_dir / "2026_06_03_us_premarket.md"
    assert report.exists()
    assert "NVDA" in report.read_text(encoding="utf-8")
    assert notify_calls and notify_calls[0]["n_tickers"] == 1
    # Sidecar must be cleaned up.
    assert not snap.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_report_morning.py -v -k run_async`
Expected: FAIL (`cannot import name '_run_async'` or `cannot import OUTPUT_REPORTS_DIR`).

- [ ] **Step 3: Add the CLI orchestration to `report/morning.py`**

Append to `report/morning.py`:

```python
import argparse
import sys
import tomllib
from datetime import datetime
from zoneinfo import ZoneInfo

from report.llm import DeepSeekBackend
from report.state import (
    CONFIG_PATH,
    OUTPUT_REPORTS_DIR,
    load_dotenv,
)
from notify import notify_morning_catalyst_ready

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "morning_gap_catalyst_system.md"

DEFAULT_MAX_TICKERS = 10
DEFAULT_CONCURRENCY = 3
DEFAULT_MAX_SEARCH_CALLS = 3
DEFAULT_MODEL = "deepseek-v4-pro"


def _load_catalyst_cfg() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        with CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning(f"[morning] failed to read {CONFIG_PATH}: {e}")
        return {}
    return data.get("morning_gap_catalyst") or {}


def _build_deepseek_backend(cfg: dict[str, Any]) -> DeepSeekBackend:
    """Construct the DeepSeek backend from env vars + the catalyst config
    block. Raises RuntimeError on missing keys — caller logs + soft-fails."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    tavily_key = os.environ.get("TAVILY_API_KEY")
    missing = [
        n for n, v in (("DEEPSEEK_API_KEY", api_key), ("TAVILY_API_KEY", tavily_key)) if not v
    ]
    if missing:
        raise RuntimeError(f"missing env var(s): {', '.join(missing)}")
    return DeepSeekBackend(
        api_key=api_key,  # type: ignore[arg-type]
        tavily_api_key=tavily_key,  # type: ignore[arg-type]
        model=cfg.get("deepseek_model", DEFAULT_MODEL),
        max_search_calls=int(cfg.get("max_search_calls", DEFAULT_MAX_SEARCH_CALLS)),
    )


def _cap_and_sort(entries: list[SnapshotEntry], cap: int) -> tuple[list[SnapshotEntry], int]:
    """Sort by gap% desc, drop entries beyond `cap`. Returns (kept, skipped_count)."""
    sorted_entries = sorted(
        entries, key=lambda e: (e.gap_pct or 0.0), reverse=True
    )
    kept = sorted_entries[:cap]
    skipped = max(0, len(sorted_entries) - cap)
    return kept, skipped


async def _run_async(
    *, snapshot_path: Path, date_iso: str, offset_min: int
) -> int:
    load_dotenv()
    cfg = _load_catalyst_cfg()
    if not cfg.get("enabled", True):
        logger.info("[morning] catalyst report disabled in config")
        return 0

    try:
        entries = read_snapshot(snapshot_path)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"[morning] failed to read snapshot {snapshot_path}: {e}")
        return 0
    if not entries:
        logger.info("[morning] empty snapshot, nothing to do")
        snapshot_path.unlink(missing_ok=True)
        return 0

    cap = int(cfg.get("max_tickers_per_run", DEFAULT_MAX_TICKERS))
    entries, skipped = _cap_and_sort(entries, cap)

    if not PROMPT_PATH.is_file():
        logger.error(f"[morning] system prompt missing at {PROMPT_PATH}")
        snapshot_path.unlink(missing_ok=True)
        return 0
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    try:
        backend = _build_deepseek_backend(cfg)
    except RuntimeError as e:
        logger.warning(f"[morning] backend init failed; skipping: {e}")
        snapshot_path.unlink(missing_ok=True)
        return 0

    concurrency = int(cfg.get("concurrency", DEFAULT_CONCURRENCY))
    semaphore = asyncio.Semaphore(concurrency)
    try:
        sections = await asyncio.gather(
            *(analyze_catalyst(backend, system_prompt, e, semaphore) for e in entries)
        )
    finally:
        await backend.aclose()

    date_stem = date_iso.replace("-", "_")
    out_path = OUTPUT_REPORTS_DIR / f"{date_stem}_us_premarket.md"
    now_hkt = datetime.now(HKT)
    now_et = datetime.now(ET)
    write_report(
        out_path=out_path,
        date_iso=date_iso,
        offset_min=offset_min,
        entries=entries,
        sections=sections,
        model_label=backend.model_label(),
        generated_at=now_hkt,
        skipped_count=skipped,
        et_time_hhmm=now_et.strftime("%H:%M"),
    )
    logger.info(f"[morning] wrote {out_path}")

    # Reload full config for [notify] section.
    try:
        with CONFIG_PATH.open("rb") as fh:
            full_cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        full_cfg = {}
    notify_morning_catalyst_ready(
        report_path=out_path,
        offset_min=offset_min,
        n_tickers=len(entries),
        config=full_cfg,
    )
    snapshot_path.unlink(missing_ok=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m report.morning")
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--offset", required=True, type=int,
                        help="Minutes from market open (e.g. -20)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        return asyncio.run(
            _run_async(
                snapshot_path=args.snapshot,
                date_iso=args.date,
                offset_min=args.offset,
            )
        )
    except Exception as e:
        logger.exception(f"[morning] aborted: {e}")
        try:
            args.snapshot.unlink(missing_ok=True)
        except OSError:
            pass
        return 0  # soft-fail


if __name__ == "__main__":
    sys.exit(main())
```

Also add the missing `os` import near the top of `report/morning.py`:

```python
import os
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_report_morning.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add report/morning.py tests/test_report_morning.py
git commit -m "feat(report): CLI entrypoint for catalyst report subprocess"
```

---

### Task 8: Config block + env var setup

**Files:**
- Modify: `config.toml`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the `[morning_gap_catalyst]` section to `config.toml`**

Open `config.toml` and append (use Edit, do not paste over existing content):

```toml

[morning_gap_catalyst]
# Pre-market catalyst report. Runs as detached subprocess after the
# morning-gap scan finds fresh US pre-market gappers. Independent from
# the EOD [report] backend selection — always uses DeepSeek + Tavily.
enabled = true
max_tickers_per_run = 10
concurrency = 3
max_search_calls = 3
deepseek_model = "deepseek-v4-pro"
```

- [ ] **Step 2: Verify config still parses**

Run: `uv run python -c "import tomllib; tomllib.load(open('config.toml', 'rb'))"`
Expected: silent success (exit code 0).

- [ ] **Step 3: Add the invariant to CLAUDE.md**

Open `CLAUDE.md`. Find the "Invariants" section. Append a new bullet:

```markdown
- **Catalyst report (pre-market)** is a **detached subprocess** spawned
  from the morning-gap path; it MUST NOT block the morning-gap process.
  Always uses DeepSeek + Tavily regardless of `[report] backend`. Reads
  only the JSON snapshot sidecar — MUST NOT call Futu / yfinance. Output:
  `output/Reports/<date>_us_premarket.md`, appended across -20 / -10.
```

- [ ] **Step 4: Commit**

```bash
git add config.toml CLAUDE.md
git commit -m "chore(config): add [morning_gap_catalyst] section + CLAUDE.md invariant"
```

---

### Task 9: Hook into `main.py` morning-gap path

**Files:**
- Modify: `main.py` (after `notify_morning_gap` call, ~line 1983)

- [ ] **Step 1: Read the current hook site**

Run: `sed -n '1960,1992p' /Users/xue/momentum-scanner/main.py`
Confirm the current call is `notify_morning_gap(fresh, offset, len(sorted_tickers), config, promoted=promoted)` followed by `cleanup_old_outputs`.

- [ ] **Step 2: Add the snapshot-collector helper at the top of `main.py`**

Find the existing `_morning_gap_classify` definition. Just below it, add:

```python
def _spawn_catalyst_report(
    *,
    fresh: list[str],
    snapshot_rows: dict[str, dict[str, float | str | None]],
    today_iso: str,
    offset: int,
    config: dict,
) -> None:
    """Spawn the catalyst report subprocess (detached). Soft-fails — any
    exception logged + swallowed. Caller is responsible for `fresh`-only
    invariant (we don't re-filter here)."""
    cfg = config.get("morning_gap_catalyst") or {}
    if not cfg.get("enabled", False):
        return
    if not fresh:
        return
    try:
        import json
        import subprocess
        import tempfile

        entries = []
        for t in fresh:
            row = snapshot_rows.get(t, {})
            entries.append({
                "ticker": t,
                "company_name": row.get("name"),
                "gap_pct": row.get("gap_pct"),
                "last_price": row.get("last_price"),
                "market_cap": row.get("market_cap"),
                "first_seen_offset_minutes": offset,
            })
        fd, tmpname = tempfile.mkstemp(
            prefix="morning_snap_", suffix=".json", text=True
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False)
        logger.info(
            f"[Morning Gap] spawning catalyst report subprocess for "
            f"{len(fresh)} fresh tickers (snapshot at {tmpname})"
        )
        subprocess.Popen(
            [
                "uv", "run", "python", "-m", "report.morning",
                "--snapshot", tmpname,
                "--date", today_iso,
                "--offset", str(offset),
            ],
            cwd=str(Path(__file__).resolve().parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        logger.warning(f"[Morning Gap] catalyst report spawn failed: {e}")
```

If `os` or `Path` are not yet imported at the top of `main.py`, leave them — they almost certainly are. Run `grep -n "^import os\|^from pathlib" /Users/xue/momentum-scanner/main.py | head -3` to confirm.

- [ ] **Step 3: Plumb the snapshot dict through `run_morning_gap`**

`discover_morning_gap_candidates` currently returns `list[str]` only. We need the per-ticker snapshot row (name / gap% / price / market cap) too. Easiest path that does NOT churn the discovery API: do a second small `get_market_snapshot` call from `_spawn_catalyst_report`'s site against just the `fresh` set, before the spawn.

Add to the top of `main.py` (if not already imported):

```python
from futu_sync import _opend_reachable
```

Then in the morning-gap branch of `main()` (just before `cleanup_old_outputs`, inside the `if fresh or promoted:` block — but only when `is_pre and fresh`), add:

```python
        if is_pre and fresh:
            snapshot_rows = _fetch_snapshot_rows(
                fresh, futu_cfg=config.get("futu") or {}
            )
            today_iso = today_date.isoformat()
            _spawn_catalyst_report(
                fresh=fresh,
                snapshot_rows=snapshot_rows,
                today_iso=today_iso,
                offset=offset,
                config=config,
            )
```

And add this helper near `_spawn_catalyst_report`:

```python
def _fetch_snapshot_rows(
    tickers: list[str], *, futu_cfg: dict
) -> dict[str, dict[str, float | str | None]]:
    """One small `get_market_snapshot` call against just `tickers`. Returns
    `{ticker: {name, gap_pct, last_price, market_cap}}`. Soft-fail to {}
    on any error — the subprocess will still run with null fields."""
    rows: dict[str, dict[str, float | str | None]] = {}
    if not tickers:
        return rows
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError:
        return rows
    host = futu_cfg.get("host", "127.0.0.1")
    port = futu_cfg.get("port", 11111)
    if not _opend_reachable(host, port):
        return rows
    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        codes = [f"US.{t}" for t in tickers]
        ret, snap = ctx.get_market_snapshot(codes)
        if ret != RET_OK or snap is None:
            return rows
        for _, r in snap.iterrows():
            code = r.get("code", "")
            if not code.startswith("US."):
                continue
            t = code[3:]
            try:
                last = float(r.get("last_price", 0) or 0)
                prev = float(r.get("prev_close_price", 0) or 0)
                pre_chg = r.get("pre_change_rate")
                gap = float(pre_chg) if pre_chg is not None else (
                    (last - prev) / prev * 100 if prev else None
                )
                rows[t] = {
                    "name": r.get("stock_name") or None,
                    "gap_pct": gap,
                    "last_price": last or None,
                    "market_cap": float(r.get("total_market_val", 0) or 0) or None,
                }
            except (TypeError, ValueError):
                continue
    except Exception as e:
        logger.warning(f"[Morning Gap] snapshot fetch for catalyst report failed: {e}")
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
    return rows
```

- [ ] **Step 4: Smoke-check imports + arg-parse**

Run: `uv run python -c "from main import _spawn_catalyst_report, _fetch_snapshot_rows; print('ok')"`
Expected: `ok`.

Run: `uv run python -m report.morning --help`
Expected: argparse usage with `--snapshot --date --offset`.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(main): spawn catalyst report subprocess on fresh pre-market gappers"
```

---

### Task 10: Integration smoke test (manual, env-gated)

**Files:**
- Create: `tests/fixtures/morning_snapshot_smoke.json`
- Create: `scripts/smoke_morning_report.sh`

- [ ] **Step 1: Add a manual fixture**

Create `tests/fixtures/morning_snapshot_smoke.json`:

```json
[
  {
    "ticker": "NVDA",
    "company_name": "NVIDIA Corp",
    "gap_pct": 12.4,
    "last_price": 138.20,
    "market_cap": 3400000000000,
    "first_seen_offset_minutes": -20
  }
]
```

- [ ] **Step 2: Add the smoke script**

Create `scripts/smoke_morning_report.sh` and `chmod +x` it:

```bash
#!/usr/bin/env bash
# Manual smoke test for the catalyst report. Requires DEEPSEEK_API_KEY
# and TAVILY_API_KEY in the environment (or .env). Writes to
# output/Reports/<today>_us_premarket.md.
set -euo pipefail
cd "$(dirname "$0")/.."
TODAY="$(date +%Y-%m-%d)"
uv run python -m report.morning \
  --snapshot tests/fixtures/morning_snapshot_smoke.json \
  --date "$TODAY" \
  --offset -20
echo "--- report ---"
cat "output/Reports/${TODAY//-/_}_us_premarket.md"
```

Run: `chmod +x scripts/smoke_morning_report.sh`

- [ ] **Step 3: Run the smoke script (manual, optional)**

The smoke script hits real APIs. Skip unless verifying end-to-end. To run:

```bash
./scripts/smoke_morning_report.sh
```

Expected: 30–90s of stderr from DeepSeek + Tavily, then a printed catalyst report for NVDA with 4 sections.

**Note:** The fixture deliberately stays in the repo because the test path copies it (the CLI deletes the snapshot after use). For the smoke script we duplicate it to `/tmp` first to preserve the fixture:

Edit the script:

```bash
TMPSNAP="$(mktemp -t morning_snap.XXXXXX.json)"
cp tests/fixtures/morning_snapshot_smoke.json "$TMPSNAP"
uv run python -m report.morning \
  --snapshot "$TMPSNAP" \
  --date "$TODAY" \
  --offset -20
```

(The CLI deletes `$TMPSNAP` itself in the `finally` path.)

- [ ] **Step 4: Run the full pytest suite to catch regressions**

Run: `uv run python -m pytest tests/ -v --tb=short 2>&1 | tail -40`
Expected: all tests pass. The new modules contribute the tests added in Tasks 2–6 (~10 new tests).

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/morning_snapshot_smoke.json scripts/smoke_morning_report.sh
git commit -m "test(morning): smoke fixture + manual end-to-end script"
```

---

## Self-review checklist

**Spec coverage:**
- Trigger / pre-market only / fresh-set dedup → Task 9 (spawn guarded by `is_pre and fresh`)
- Detached subprocess + sidecar → Task 9 (`subprocess.Popen` + `start_new_session=True`, JSON via `tempfile.mkstemp`)
- DeepSeek + Tavily hardcoded → Task 7 (`_build_deepseek_backend` does not consult `[report] backend`)
- 4-section structure + closed tag set → Task 1 (prompt) + Task 4 (renderer accepts whatever the model emits)
- Append vs create + missing-file fallback → Task 5
- Cap behavior (gap% desc + truncation note) → Task 7 (`_cap_and_sort`) + Task 4 (`skipped_count` in renderer)
- Two-stage ntfy push → existing `notify_morning_gap` unchanged + Task 6 (`notify_morning_catalyst_ready`)
- Config block + invariant → Task 8
- Failure modes (missing key / Tavily failure / subprocess spawn) → Tasks 3 + 7 + 9 (soft-fail at each layer)
- Placeholder formatting → Task 3 (`CATALYST_FAILURE_PLACEHOLDER`)
- Smoke test → Task 10

**Type consistency:**
- `SnapshotEntry` fields used identically in Tasks 2, 3, 4, 5, 7.
- `write_report` keyword args match between Task 5 definition and Task 7 caller.
- `render_initial_document` / `render_append_section` signatures match between Task 4 and `write_report` in Task 5.
- `notify_morning_catalyst_ready` signature matches between Task 6 definition and Task 7 caller (`report_path`, `offset_min`, `n_tickers`, `config`).
