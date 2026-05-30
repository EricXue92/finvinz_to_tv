#!/usr/bin/env python3
"""Prune dated CSVs in cloud-published data folders, keeping the N most recent.

Called by the GH Actions publish workflows right after the compute step and
before `git add`, so each daily publish commit also drops anything older than
N days. Local consumers walk back ≤3 days of stale cache (per CLAUDE.md), so
keep=4 covers today + the 3-day fallback window with one day of slack.

Only touches files whose names match `YYYY-MM-DD.csv` — README.md and
anything else in the folder is left untouched. Designed to be safe to run
when the compute step failed: if no new dated CSV was written today, prune
still keeps the N most recent, so a single missed publish does not cascade
into a forced refetch.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date as _date
from pathlib import Path

DATED_CSV_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$")


def prune_folder(folder: Path, keep: int) -> list[Path]:
    """Delete all dated CSVs in `folder` except the `keep` most recent.

    Returns the paths that were deleted (empty list if folder had ≤ keep
    dated CSVs). Raises FileNotFoundError if folder doesn't exist.
    """
    if not folder.is_dir():
        raise FileNotFoundError(f"Not a directory: {folder}")
    dated: list[tuple[_date, Path]] = []
    for entry in folder.iterdir():
        match = DATED_CSV_RE.match(entry.name)
        if not match:
            continue
        try:
            d = _date.fromisoformat(match.group(1))
        except ValueError:
            continue
        dated.append((d, entry))
    dated.sort(key=lambda t: t[0], reverse=True)
    to_delete = [p for _, p in dated[keep:]]
    for path in to_delete:
        path.unlink()
    return to_delete


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "folders", type=Path, nargs="+",
        help="One or more data folders to prune (e.g. data/us_rs_3m)",
    )
    ap.add_argument(
        "--keep", type=int, default=4,
        help="Number of most recent dated CSVs to keep (default: 4)",
    )
    args = ap.parse_args(argv)
    if args.keep < 1:
        print(f"[prune] refusing keep={args.keep} (must be ≥ 1)", file=sys.stderr)
        return 2
    for folder in args.folders:
        deleted = prune_folder(folder, args.keep)
        if deleted:
            names = ", ".join(p.name for p in deleted)
            print(f"[prune] {folder}: deleted {len(deleted)} old CSV(s): {names}")
        else:
            print(f"[prune] {folder}: nothing to delete (≤{args.keep} dated CSVs present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
