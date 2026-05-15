# Output Retention Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-delete dated output artifacts older than yesterday at the end of each scan run, keeping only today + yesterday on disk (with a 4-day exception for `rs_rating_*.csv`).

**Architecture:** New `cleanup.py` module at project root exposing one function `cleanup_old_outputs(output_dir, today)`. Driven by a constant table of `(directory, filename regex, date format, keep_days)` tuples — glob-driven, not directory-sweep-driven, so unrecognised filenames survive untouched. Wired into `main.py` at each successful mode-completion point with `try/except` soft-fail (matches the existing Futu sync / report generation pattern).

**Tech Stack:** Python 3, `pathlib`, `re`, `datetime`, `pytest` + `tmp_path` for tests. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-15-output-retention-cleanup-design.md`

---

## File Structure

- **Create:** `cleanup.py` — one public function `cleanup_old_outputs`, one private constant `_RETENTION_RULES`, one private helper `_clean_one_rule`. ~80 lines total.
- **Create:** `tests/test_cleanup.py` — pure-logic tests using `tmp_path`. No network, no Futu, no yfinance.
- **Modify:** `main.py` — add 4 call sites (one per mode success-return path) plus the import. ~25 lines added.

---

## Task 1: Create cleanup.py skeleton with retention table

**Files:**
- Create: `cleanup.py`

- [ ] **Step 1: Write the module skeleton**

Create `/Users/xue/finviz_to_tv/cleanup.py`:

```python
"""Retention cleanup for dated output artifacts.

Deletes dated files older than the per-rule retention window. Driven by an
explicit table of (directory, filename regex, date format, keep_days)
tuples so unrecognised filenames are never touched.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Rule:
    subdir: str           # relative to output_dir; "" = output_dir itself
    pattern: re.Pattern   # must contain one group capturing the date
    date_fmt: str         # strptime format for the captured group
    keep_days: int        # 2 = today + yesterday; 4 = today + 3 prior


# YYYY_MM_DD is the project-wide convention; hk_rs_rating uses YYYY-MM-DD.
_DATE_U = r"(\d{4}_\d{2}_\d{2})"   # underscores
_DATE_D = r"(\d{4}-\d{2}-\d{2})"   # dashes

_RETENTION_RULES: tuple[_Rule, ...] = (
    # Dated scan outputs — 2-day retention.
    _Rule("TV/US",     re.compile(rf"^{_DATE_U}_.+\.txt$"),  "%Y_%m_%d", 2),
    _Rule("TV/HK",     re.compile(rf"^{_DATE_U}_.+\.txt$"),  "%Y_%m_%d", 2),
    _Rule("Webull/US", re.compile(rf"^{_DATE_U}_.+\.txt$"),  "%Y_%m_%d", 2),
    _Rule("Webull/HK", re.compile(rf"^{_DATE_U}_.+\.txt$"),  "%Y_%m_%d", 2),
    _Rule("Reports",   re.compile(rf"^{_DATE_U}_(us|hk)\.(md|html)$"), "%Y_%m_%d", 2),
    # Per-day state caches — 2-day retention.
    _Rule("state", re.compile(rf"^morning_gap_seen_(?:pre|post)_{_DATE_U}\.txt$"),
          "%Y_%m_%d", 2),
    _Rule("state", re.compile(rf"^hk_morning_gap_seen_post_{_DATE_U}\.txt$"),
          "%Y_%m_%d", 2),
    _Rule("state", re.compile(rf"^hk_rs_rating_{_DATE_D}\.csv$"),
          "%Y-%m-%d", 2),
    # rs_rating_*.csv: 4-day window preserves the documented 3-day GitHub
    # fetch fallback in rs_rating.py (_FALLBACK_MAX_AGE_DAYS = 3).
    _Rule("state", re.compile(rf"^rs_rating_{_DATE_U}\.csv$"),
          "%Y_%m_%d", 4),
)


def cleanup_old_outputs(output_dir: Path, today: date) -> None:
    """Delete dated artifacts under output_dir older than each rule's window.

    Soft-fails: per-file IO errors are caught and logged; the function
    never raises. Files whose names don't match any rule are not touched.
    """
    total_deleted = 0
    for rule in _RETENTION_RULES:
        total_deleted += _clean_one_rule(output_dir, rule, today)
    if total_deleted:
        logger.info(f"[cleanup] Removed {total_deleted} stale output file(s)")


def _clean_one_rule(output_dir: Path, rule: _Rule, today: date) -> int:
    target_dir = output_dir / rule.subdir if rule.subdir else output_dir
    if not target_dir.is_dir():
        return 0
    cutoff = today - timedelta(days=rule.keep_days - 1)
    deleted = 0
    for entry in target_dir.iterdir():
        if not entry.is_file():
            continue
        m = rule.pattern.match(entry.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), rule.date_fmt).date()
        except ValueError:
            logger.warning(f"[cleanup] Skipping malformed date in {entry.name}")
            continue
        if file_date >= cutoff:
            continue
        try:
            entry.unlink()
            deleted += 1
        except FileNotFoundError:
            pass  # raced with another process; fine
        except OSError as e:
            logger.warning(f"[cleanup] Failed to delete {entry}: {e}")
    return deleted
```

- [ ] **Step 2: Verify the module imports**

Run: `uv run python -c "from cleanup import cleanup_old_outputs; print('ok')"`
Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add cleanup.py
git commit -m "feat(cleanup): add cleanup_old_outputs module skeleton

Retention table covers TV/Webull scan outputs, daily Reports, and the
dated state files. rs_rating_*.csv gets a 4-day window to preserve the
3-day GitHub fetch fallback documented in rs_rating.py.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Test — files newer than cutoff survive

**Files:**
- Create: `tests/test_cleanup.py`

- [ ] **Step 1: Write the test**

Create `/Users/xue/finviz_to_tv/tests/test_cleanup.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_cleanup.py::test_today_and_yesterday_survive -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cleanup.py
git commit -m "test(cleanup): verify today + yesterday files survive

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Test — files older than cutoff are deleted (2-day rule)

**Files:**
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_cleanup.py`:

```python
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
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_cleanup.py::test_files_older_than_two_days_deleted -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cleanup.py
git commit -m "test(cleanup): verify 2-day rule deletes older dated files

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Test — rs_rating_*.csv has 4-day window, others do not

**Files:**
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_cleanup.py`:

```python
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
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_cleanup.py::test_rs_rating_uses_four_day_window -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cleanup.py
git commit -m "test(cleanup): verify rs_rating 4-day exception

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Test — survivor allow-list is preserved

**Files:**
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_cleanup.py`:

```python
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
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_cleanup.py::test_survivors_are_preserved -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cleanup.py
git commit -m "test(cleanup): verify survivor allow-list untouched

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Test — per-day morning-gap state files

**Files:**
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Add the test**

Append to `tests/test_cleanup.py`:

```python
def test_morning_gap_state_files_cleaned(output_tree: Path) -> None:
    _touch(output_tree / "state/morning_gap_seen_pre_2026_05_15.txt")
    _touch(output_tree / "state/morning_gap_seen_post_2026_05_15.txt")
    _touch(output_tree / "state/morning_gap_seen_pre_2026_05_14.txt")
    _touch(output_tree / "state/morning_gap_seen_pre_2026_05_13.txt")
    _touch(output_tree / "state/morning_gap_seen_post_2026_05_12.txt")
    _touch(output_tree / "state/hk_morning_gap_seen_post_2026_05_15.txt")
    _touch(output_tree / "state/hk_morning_gap_seen_post_2026_05_13.txt")

    cleanup_old_outputs(output_tree, date(2026, 5, 15))

    assert (output_tree / "state/morning_gap_seen_pre_2026_05_15.txt").exists()
    assert (output_tree / "state/morning_gap_seen_post_2026_05_15.txt").exists()
    assert (output_tree / "state/morning_gap_seen_pre_2026_05_14.txt").exists()
    assert not (output_tree / "state/morning_gap_seen_pre_2026_05_13.txt").exists()
    assert not (output_tree / "state/morning_gap_seen_post_2026_05_12.txt").exists()
    assert (output_tree / "state/hk_morning_gap_seen_post_2026_05_15.txt").exists()
    assert not (output_tree / "state/hk_morning_gap_seen_post_2026_05_13.txt").exists()
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_cleanup.py::test_morning_gap_state_files_cleaned -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cleanup.py
git commit -m "test(cleanup): verify per-day morning-gap state pruning

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Test — edge cases (empty dir, malformed date, missing subdir)

**Files:**
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Add the tests**

Append to `tests/test_cleanup.py`:

```python
def test_empty_state_dir_does_not_crash(output_tree: Path) -> None:
    # state/ exists but contains nothing matching the rules.
    cleanup_old_outputs(output_tree, date(2026, 5, 15))


def test_missing_subdir_is_skipped(tmp_path: Path) -> None:
    # output/ exists but none of the expected subdirs do.
    out = tmp_path / "output"
    out.mkdir()
    cleanup_old_outputs(out, date(2026, 5, 15))


def test_malformed_date_in_filename_is_skipped(
    output_tree: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Feb 30 doesn't exist — file must NOT be deleted, and a warning logged.
    _touch(output_tree / "TV/US/2026_02_30_Leaders.txt")
    with caplog.at_level("WARNING"):
        cleanup_old_outputs(output_tree, date(2026, 5, 15))
    assert (output_tree / "TV/US/2026_02_30_Leaders.txt").exists()
    assert any("malformed date" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_cleanup.py -v`
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cleanup.py
git commit -m "test(cleanup): cover empty dir, missing subdir, malformed date

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Wire cleanup into main.py at all four success-return sites

**Files:**
- Modify: `/Users/xue/finviz_to_tv/main.py` (4 call sites + 1 import)

- [ ] **Step 1: Add the import**

Find the existing imports near the top of `main.py`. Add `from cleanup import cleanup_old_outputs` next to the other project-local imports (look for `from notify import ...` or similar — group them).

The exact insertion: find the block that has `from futu_sync import sync_to_futu` (or similar local imports) and add the line right after it. If you can't pinpoint the block, place it just before `logger = logging.getLogger(__name__)`.

- [ ] **Step 2: Add a small helper near the top of main()**

In `main.py`, the `today` variable is already defined as a `YYYY_MM_DD` string at line ~1401 (`today = date.today().strftime("%Y_%m_%d")`). We need a `date` object for the cleanup call. Right below that line, add:

```python
    today_date = date.today()
```

(`date` is already imported — it's used on the line above.)

- [ ] **Step 3: Add the cleanup call to the hk-eod mode**

In `main.py`, find this block (around line 1432-1436):

```python
        except Exception as e:
            logger.warning(f"[HK EOD] Pipeline failed: {e}")
        logger.info("Done.")
        return 0
```

Change it to:

```python
        except Exception as e:
            logger.warning(f"[HK EOD] Pipeline failed: {e}")
        try:
            cleanup_old_outputs(output_dir, today_date)
        except Exception as e:
            logger.warning(f"[cleanup] Failed: {e}")
        logger.info("Done.")
        return 0
```

- [ ] **Step 4: Add the cleanup call to the eod / us-eod mode**

In `main.py`, find this block (around line 1716-1719):

```python
            except Exception as e:
                logger.warning(f"[HK EOD] Pipeline failed: {e}")

        logger.info("Done.")
        return 0
```

Change it to:

```python
            except Exception as e:
                logger.warning(f"[HK EOD] Pipeline failed: {e}")

        try:
            cleanup_old_outputs(output_dir, today_date)
        except Exception as e:
            logger.warning(f"[cleanup] Failed: {e}")
        logger.info("Done.")
        return 0
```

- [ ] **Step 5: Add the cleanup call to the morning-gap mode**

In `main.py`, find this block (around line 1762-1765):

```python
        if fresh or promoted:
            notify_morning_gap(
                fresh, offset, len(sorted_tickers), config, promoted=promoted
            )

        logger.info("Done.")
        return 0
```

Change it to:

```python
        if fresh or promoted:
            notify_morning_gap(
                fresh, offset, len(sorted_tickers), config, promoted=promoted
            )

        try:
            cleanup_old_outputs(output_dir, today_date)
        except Exception as e:
            logger.warning(f"[cleanup] Failed: {e}")
        logger.info("Done.")
        return 0
```

- [ ] **Step 6: Add the cleanup call to the hk-morning-gap mode**

In `main.py`, find this block (around line 1807-1814):

```python
        if fresh:
            notify_morning_gap(
                fresh, offset, len(tv_tickers), config,
                promoted=[], market="HK",
            )

        logger.info("Done.")
        return 0
```

Change it to:

```python
        if fresh:
            notify_morning_gap(
                fresh, offset, len(tv_tickers), config,
                promoted=[], market="HK",
            )

        try:
            cleanup_old_outputs(output_dir, today_date)
        except Exception as e:
            logger.warning(f"[cleanup] Failed: {e}")
        logger.info("Done.")
        return 0
```

- [ ] **Step 7: Smoke-test the wiring**

Run: `uv run python -c "import main; print('import ok')"`
Expected: `import ok` (no SyntaxError, no ImportError).

Then run the existing test suite to confirm nothing else broke:

Run: `uv run pytest tests/ -v`
Expected: all tests PASS (including the 7 new ones from Tasks 2-7).

- [ ] **Step 8: Commit**

```bash
git add main.py
git commit -m "feat(cleanup): run cleanup_old_outputs at end of each scan mode

Hooks the retention cleanup into the four success-return points
(hk-eod, eod/us-eod, morning-gap, hk-morning-gap) right before
'Done.'. Each call is wrapped in try/except so a cleanup failure
never affects the exit code — matches the existing soft-fail
pattern for Futu sync and the daily report.

Outside-window early-exits in morning-gap modes intentionally skip
cleanup (nothing was written).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: Manual dry-run against the real output/ directory

**Files:** none modified.

- [ ] **Step 1: Verify what would be deleted before any real run**

Note today's date and the current contents:

```bash
date
ls /Users/xue/finviz_to_tv/output/TV/US/ | head -30
ls /Users/xue/finviz_to_tv/output/state/ | head -30
```

Note any files dated `< today - 1`. Those are the ones that the next scan run will delete.

- [ ] **Step 2: Trigger a low-cost mode to exercise the hook**

The morning-gap modes auto-exit outside their scan window without writing anything, so they're the cheapest sanity check. But they ALSO skip cleanup outside-window by design — the hook only runs on the in-window success path. To force a real exercise of the hook without waiting for market hours, run the HK EOD mode (it's idempotent w.r.t. cleanup; it just writes today's files again):

```bash
cd /Users/xue/finviz_to_tv
uv run main.py --mode hk-eod
```

If OpenD is not running this will warn-and-continue (HK long-side writes empty .txt files) but cleanup still runs.

- [ ] **Step 3: Inspect the result**

```bash
ls /Users/xue/finviz_to_tv/output/TV/US/
ls /Users/xue/finviz_to_tv/output/TV/HK/
ls /Users/xue/finviz_to_tv/output/Webull/US/
ls /Users/xue/finviz_to_tv/output/Webull/HK/
ls /Users/xue/finviz_to_tv/output/Reports/
ls /Users/xue/finviz_to_tv/output/state/
```

Expected:
- Only `<today>_*` and `<yesterday>_*` files remain in the four TV/Webull dirs and Reports.
- `rs_rating_*.csv` keeps up to 4 days worth.
- `eod_seen_*.txt`, `ntfy_last_seen.txt`, `edgar_cache/`, `launchd_*.log` all untouched.
- Log line `[cleanup] Removed N stale output file(s)` (if N > 0) or no cleanup log line (if nothing matched the cutoff).

If anything that should have survived is missing, **stop and investigate** — this is the last gate before the next scheduled run does it for real.

- [ ] **Step 4: Update CLAUDE.md**

Document the new behavior so future-you doesn't get surprised by missing old files. Add a paragraph under "Key mechanisms" in CLAUDE.md:

```markdown
- **Retention cleanup**: After each successful scan run (`us-eod`, `hk-eod`,
  `morning-gap`, `hk-morning-gap`), `cleanup.cleanup_old_outputs` deletes
  dated output artifacts older than yesterday. Covers
  `output/TV/{US,HK}/`, `output/Webull/{US,HK}/`, `output/Reports/`, and
  the dated state files (`morning_gap_seen_*`, `hk_morning_gap_seen_*`,
  `hk_rs_rating_*.csv`). `rs_rating_*.csv` keeps a 4-day window to
  preserve the documented 3-day fetch fallback in `rs_rating.py`. The
  cross-day master dedup files (`eod_seen_*.txt`), `ntfy_last_seen.txt`,
  `edgar_cache/`, and `launchd_*.log` are **never** touched. Cleanup is
  glob-driven (unrecognised filenames are preserved) and soft-fails — a
  cleanup error logs a warning and the run still exits 0.
```

- [ ] **Step 5: Commit doc update**

```bash
git add CLAUDE.md
git commit -m "docs: document the new output retention cleanup behavior

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Spec coverage:** Every section of the spec is mapped to at least one task. Date parsing (Task 1 + Tasks 2-7 collectively), cutoff math (Tasks 2-4), module layout (Task 1), integration points (Task 8), failure modes (Task 7 + the soft-fail wrappers in Task 8), testing (Tasks 2-7), survivor allow-list (Task 5).
- **Placeholder scan:** No TBDs, no "add validation", no "similar to Task N". Every code step contains the full code.
- **Type/name consistency:** `cleanup_old_outputs(output_dir: Path, today: date)` is called identically in all four wiring sites in Task 8. The helper name `today_date` is introduced in Task 8 Step 2 and used in Steps 3-6.
