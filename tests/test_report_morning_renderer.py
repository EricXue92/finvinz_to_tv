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
