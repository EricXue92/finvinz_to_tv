"""Empty scan results must not produce .txt files.

Regression target: output/TV+Webull filled with 0-byte dated files on
no-signal days. Empty list => no file created; an existing dated file is
left alone (a manual us-eod rerun dedups today's names to 0 — deleting
would destroy the morning run's legitimate output).

Morning-gap files are per-scan snapshots (`MorningGap5`, `MorningGapPre20`,
`HKMorningGap10`), so a 0-ticker scan simply leaves no file for that offset
— no stale-overwrite concern.
"""
from __future__ import annotations

from pathlib import Path

from main import _gap_scan_stem, _write_webull, write_watchlist


# --- write_watchlist ---

def test_empty_list_creates_no_file(tmp_path: Path) -> None:
    out = tmp_path / "2026_08_28_Leaders.txt"
    write_watchlist([], out)
    assert not out.exists()


def test_empty_list_keeps_existing_file(tmp_path: Path) -> None:
    """Rerun protection: dedup-to-0 must not wipe the earlier run's output."""
    out = tmp_path / "2026_08_28_Leaders.txt"
    out.write_text("AAPL\n")
    write_watchlist([], out)
    assert out.read_text() == "AAPL\n"


def test_nonempty_comma_write_unchanged(tmp_path: Path) -> None:
    out = tmp_path / "2026_08_28_Leaders.txt"
    write_watchlist(["AAPL", "MSFT"], out)
    assert out.read_text() == "AAPL,MSFT\n"


def test_nonempty_newline_write_unchanged(tmp_path: Path) -> None:
    out = tmp_path / "x.txt"
    write_watchlist(["AAPL", "MSFT"], out, fmt="newline")
    assert out.read_text() == "AAPL\nMSFT\n"


# --- Webull mirror ---

def test_webull_mirror_skips_empty(tmp_path: Path) -> None:
    dated = tmp_path / "TV" / "US" / "2026_08_28_Leaders.txt"
    dated.parent.mkdir(parents=True)
    _write_webull([], dated, tmp_path)
    assert not (tmp_path / "Webull" / "US" / dated.name).exists()


def test_webull_mirror_writes_nonempty(tmp_path: Path) -> None:
    dated = tmp_path / "TV" / "US" / "2026_08_28_Leaders.txt"
    dated.parent.mkdir(parents=True)
    _write_webull(["AAPL"], dated, tmp_path)
    assert (tmp_path / "Webull" / "US" / dated.name).read_text() == "AAPL\n"


# --- per-scan morning-gap stems ---

def test_us_premarket_stem() -> None:
    assert _gap_scan_stem(-20) == "MorningGapPre20"
    assert _gap_scan_stem(-5) == "MorningGapPre5"


def test_us_postopen_stem() -> None:
    assert _gap_scan_stem(5) == "MorningGap5"
    assert _gap_scan_stem(30) == "MorningGap30"


def test_hk_stem() -> None:
    assert _gap_scan_stem(10, hk=True) == "HKMorningGap10"
    assert _gap_scan_stem(60, hk=True) == "HKMorningGap60"
