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
