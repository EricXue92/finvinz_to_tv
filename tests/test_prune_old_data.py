"""Tests for scripts/prune_old_data.py — keeps the N most recent dated CSVs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import prune_old_data  # noqa: E402


def _touch(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_keeps_n_most_recent(tmp_path: Path) -> None:
    for d in ["2026-05-25", "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"]:
        _touch(tmp_path / f"{d}.csv")

    deleted = prune_old_data.prune_folder(tmp_path, keep=3)

    names = {p.name for p in deleted}
    assert names == {"2026-05-25.csv", "2026-05-26.csv"}
    surviving = sorted(p.name for p in tmp_path.iterdir())
    assert surviving == ["2026-05-27.csv", "2026-05-28.csv", "2026-05-29.csv"]


def test_leaves_readme_and_non_dated_files_alone(tmp_path: Path) -> None:
    _touch(tmp_path / "README.md", "# docs")
    _touch(tmp_path / ".gitkeep")
    _touch(tmp_path / "2026-05-29.csv")
    _touch(tmp_path / "2026-05-28.csv")
    _touch(tmp_path / "manual-notes.csv")  # non-conforming name
    _touch(tmp_path / "2026-05-32.csv")    # invalid calendar date

    prune_old_data.prune_folder(tmp_path, keep=1)

    surviving = sorted(p.name for p in tmp_path.iterdir())
    assert surviving == [
        ".gitkeep",
        "2026-05-29.csv",
        "2026-05-32.csv",
        "README.md",
        "manual-notes.csv",
    ]


def test_noop_when_fewer_than_keep(tmp_path: Path) -> None:
    _touch(tmp_path / "2026-05-29.csv")
    _touch(tmp_path / "README.md")

    deleted = prune_old_data.prune_folder(tmp_path, keep=4)

    assert deleted == []
    assert (tmp_path / "2026-05-29.csv").exists()
    assert (tmp_path / "README.md").exists()


def test_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        prune_old_data.prune_folder(tmp_path / "nope", keep=4)


def test_cli_keep_must_be_positive(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = prune_old_data.main([str(tmp_path), "--keep", "0"])
    assert rc == 2
    assert "refusing keep=0" in capsys.readouterr().err


def test_cli_prunes_multiple_folders(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    for d in ["2026-05-27", "2026-05-28", "2026-05-29"]:
        _touch(a / f"{d}.csv")
    _touch(b / "2026-05-29.csv")
    _touch(b / "README.md")

    rc = prune_old_data.main([str(a), str(b), "--keep", "2"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "deleted 1 old CSV" in out  # only `a` had something to prune
    assert "nothing to delete" in out  # `b` was a no-op
    assert sorted(p.name for p in a.iterdir()) == ["2026-05-28.csv", "2026-05-29.csv"]
