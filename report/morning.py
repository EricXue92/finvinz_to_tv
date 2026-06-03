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
