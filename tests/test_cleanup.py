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
