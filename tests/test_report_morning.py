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
