from datetime import date
from pathlib import Path

import pytest

from cleanup import cleanup_old_outputs


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")


@pytest.fixture
def output_tree(tmp_path: Path) -> Path:
    """Build a synthetic output/ tree spanning 2026-05-09..2026-05-15."""
    out = tmp_path / "output"
    for d in ("TV/US", "TV/HK", "Webull/US", "Webull/HK", "Reports", "state"):
        (out / d).mkdir(parents=True)
    return out


def test_today_and_yesterday_survive(output_tree: Path) -> None:
    _touch(output_tree / "TV/US/2026_05_15_Leaders.txt")
    _touch(output_tree / "TV/US/2026_05_14_Leaders.txt")
    cleanup_old_outputs(output_tree, date(2026, 5, 15))
    assert (output_tree / "TV/US/2026_05_15_Leaders.txt").exists()
    assert (output_tree / "TV/US/2026_05_14_Leaders.txt").exists()


def test_files_older_than_two_days_deleted(output_tree: Path) -> None:
    _touch(output_tree / "TV/US/2026_05_15_Leaders.txt")
    _touch(output_tree / "TV/US/2026_05_14_Leaders.txt")
    _touch(output_tree / "TV/US/2026_05_13_Leaders.txt")
    _touch(output_tree / "TV/US/2026_05_09_Leaders.txt")

    _touch(output_tree / "TV/HK/2026_05_13_Shorts.txt")
    _touch(output_tree / "Webull/US/2026_05_12_GapUp.txt")
    _touch(output_tree / "Webull/HK/2026_05_11_RS.txt")
    _touch(output_tree / "Reports/2026_05_13_us.md")
    _touch(output_tree / "Reports/2026_05_13_hk.html")

    cleanup_old_outputs(output_tree, date(2026, 5, 15))

    assert (output_tree / "TV/US/2026_05_15_Leaders.txt").exists()
    assert (output_tree / "TV/US/2026_05_14_Leaders.txt").exists()
    assert not (output_tree / "TV/US/2026_05_13_Leaders.txt").exists()
    assert not (output_tree / "TV/US/2026_05_09_Leaders.txt").exists()
    assert not (output_tree / "TV/HK/2026_05_13_Shorts.txt").exists()
    assert not (output_tree / "Webull/US/2026_05_12_GapUp.txt").exists()
    assert not (output_tree / "Webull/HK/2026_05_11_RS.txt").exists()
    assert not (output_tree / "Reports/2026_05_13_us.md").exists()
    assert not (output_tree / "Reports/2026_05_13_hk.html").exists()


def test_rs_rating_uses_four_day_window(output_tree: Path) -> None:
    # rs_rating_*.csv survives for 4 days; today is 2026-05-15, cutoff =
    # 2026-05-12, so 05_12..05_15 survive, 05_11 and earlier go.
    for d in ("2026_05_15", "2026_05_14", "2026_05_13",
              "2026_05_12", "2026_05_11", "2026_05_09"):
        _touch(output_tree / f"state/rs_rating_{d}.csv")

    # hk_rs_rating_*.csv is on the standard 2-day rule despite living
    # next to rs_rating_*.csv. Today = 15, cutoff = 14.
    for d in ("2026-05-15", "2026-05-14", "2026-05-13", "2026-05-12"):
        _touch(output_tree / f"state/hk_rs_rating_{d}.csv")

    cleanup_old_outputs(output_tree, date(2026, 5, 15))

    assert (output_tree / "state/rs_rating_2026_05_15.csv").exists()
    assert (output_tree / "state/rs_rating_2026_05_14.csv").exists()
    assert (output_tree / "state/rs_rating_2026_05_13.csv").exists()
    assert (output_tree / "state/rs_rating_2026_05_12.csv").exists()
    assert not (output_tree / "state/rs_rating_2026_05_11.csv").exists()
    assert not (output_tree / "state/rs_rating_2026_05_09.csv").exists()

    assert (output_tree / "state/hk_rs_rating_2026-05-15.csv").exists()
    assert (output_tree / "state/hk_rs_rating_2026-05-14.csv").exists()
    assert not (output_tree / "state/hk_rs_rating_2026-05-13.csv").exists()
    assert not (output_tree / "state/hk_rs_rating_2026-05-12.csv").exists()


def test_survivors_are_preserved(output_tree: Path) -> None:
    # The state files we explicitly never want to delete.
    _touch(output_tree / "state/eod_seen_US.txt")
    _touch(output_tree / "state/eod_seen_HK.txt")
    _touch(output_tree / "state/eod_seen_IPO.txt")
    _touch(output_tree / "state/eod_seen_HKIPO.txt")
    _touch(output_tree / "state/ntfy_last_seen.txt")
    _touch(output_tree / "state/edgar_cache/AAPL.json")
    _touch(output_tree / "launchd_US.log")
    _touch(output_tree / "launchd_HK.log")

    # Non-dated rogue file in a watched directory.
    _touch(output_tree / "TV/US/notes.txt")
    # Reports cover preview (not dated).
    _touch(output_tree / "Reports/_cover_preview.html")

    # Mix in something old to ensure cleanup actually ran.
    _touch(output_tree / "TV/US/2026_05_01_Leaders.txt")

    cleanup_old_outputs(output_tree, date(2026, 5, 15))

    assert (output_tree / "state/eod_seen_US.txt").exists()
    assert (output_tree / "state/eod_seen_HK.txt").exists()
    assert (output_tree / "state/eod_seen_IPO.txt").exists()
    assert (output_tree / "state/eod_seen_HKIPO.txt").exists()
    assert (output_tree / "state/ntfy_last_seen.txt").exists()
    assert (output_tree / "state/edgar_cache/AAPL.json").exists()
    assert (output_tree / "launchd_US.log").exists()
    assert (output_tree / "launchd_HK.log").exists()
    assert (output_tree / "TV/US/notes.txt").exists()
    assert (output_tree / "Reports/_cover_preview.html").exists()
    # ...and the old dated file was actually deleted.
    assert not (output_tree / "TV/US/2026_05_01_Leaders.txt").exists()
