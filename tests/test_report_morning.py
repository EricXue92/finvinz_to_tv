"""Tests for the pre-market catalyst report (report/morning.py)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import anthropic

from report.morning import (
    CATALYST_FAILURE_PLACEHOLDER,
    SnapshotEntry,
    analyze_catalyst,
    build_user_message,
    read_snapshot,
    write_snapshot,
)


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


async def test_analyze_catalyst_returns_placeholder_on_repeated_failure(monkeypatch) -> None:
    from report import morning
    monkeypatch.setattr(morning, "RETRY_BACKOFF_SECONDS", 0)
    backend = AsyncMock()
    backend.analyze = AsyncMock(
        side_effect=anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    )
    sem = asyncio.Semaphore(1)
    entry = SnapshotEntry(ticker="NVDA")
    result = await analyze_catalyst(backend, "<sys>", entry, sem)
    assert result == CATALYST_FAILURE_PLACEHOLDER.format(exc_type="APIConnectionError")


async def test_analyze_catalyst_returns_placeholder_on_empty_response(monkeypatch) -> None:
    from report import morning
    monkeypatch.setattr(morning, "RETRY_BACKOFF_SECONDS", 0)
    backend = AsyncMock()
    backend.analyze = AsyncMock(return_value="")
    sem = asyncio.Semaphore(1)
    entry = SnapshotEntry(ticker="NVDA")
    result = await analyze_catalyst(backend, "<sys>", entry, sem)
    assert result == CATALYST_FAILURE_PLACEHOLDER.format(exc_type="RuntimeError")


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
