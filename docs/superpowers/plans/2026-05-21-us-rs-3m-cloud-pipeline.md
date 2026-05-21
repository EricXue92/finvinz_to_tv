# US 3M RS Cloud Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the US 3M RS table compute from the user's home Mac (where Yahoo throttles its IP after ~2000 tickers) to GitHub Actions (ephemeral runners with fresh IPs each run). Local pipeline becomes a thin HTTP fetcher.

**Architecture:** A new daily workflow (cron `0 1 * * 1-5`) runs `scripts/compute_us_rs_3m_cloud.py` on `ubuntu-latest`, which calls the existing pure-compute logic in `us_rs_3m.py`, writes `data/us_rs_3m/<date>.csv`, and commits to main. The rewritten `us_rs_3m.build_3m_table` becomes an HTTP fetcher pointed at `raw.githubusercontent.com/EricXue92/finvinz_to_tv/main/data/us_rs_3m/<date>.csv` — mirroring the proven Fred6725 12M CSV pattern in `rs_rating.py`.

**Tech Stack:** Python 3.12, `uv`, `pandas`, `yfinance` + `curl_cffi`, GitHub Actions (`ubuntu-latest`, `astral-sh/setup-uv@v3`), stdlib `urllib.request`.

**Spec reference:** `docs/superpowers/specs/2026-05-21-us-rs-3m-cloud-pipeline-design.md`

---

## File Structure

**New files (4):**
- `data/us_rs_3m/README.md` — directory documentation
- `data/us_rs_3m/.gitkeep` — keeps the empty directory in fresh clones
- `scripts/compute_us_rs_3m_cloud.py` — GH Actions Python entrypoint (~80 lines)
- `tests/test_compute_us_rs_3m_cloud.py` — unit tests for the cloud script (~120 lines)
- `.github/workflows/update_us_rs_3m.yml` — workflow definition (~40 lines)
- `tests/test_us_rs_3m_fetcher.py` — unit tests for the new HTTP fetcher (~130 lines)

**Modified files (3):**
- `us_rs_3m.py` — add `_fetch_cloud_csv` + `_CLOUD_CSV_URL_TEMPLATE` + `_FALLBACK_MAX_AGE_DAYS`; rewrite `build_3m_table`
- `tests/test_us_rs_3m.py` — delete 3 outdated `test_build_3m_table_*` tests (moved to the new test files)
- `CLAUDE.md` — rewrite the "US 12M + 3M" section under the IBD Relative Strength heading

**Commit boundaries (3 commits, each independently reviewable + rollbackable):**
- **Commit A** (Tasks 1-6): Cloud infrastructure only. Zero runtime impact on existing local code.
- **Commit B** (Tasks 7-11): Local fetcher swap. Cuts over local pipeline to read from cloud.
- **Commit C** (Task 12): Documentation update in `CLAUDE.md`.

---

## Task 1: Create `data/us_rs_3m/` directory with documentation

**Files:**
- Create: `/Users/xue/finviz_to_tv/data/us_rs_3m/.gitkeep`
- Create: `/Users/xue/finviz_to_tv/data/us_rs_3m/README.md`

- [ ] **Step 1: Create the directory and `.gitkeep`**

```bash
mkdir -p /Users/xue/finviz_to_tv/data/us_rs_3m
touch /Users/xue/finviz_to_tv/data/us_rs_3m/.gitkeep
```

- [ ] **Step 2: Write the README**

`/Users/xue/finviz_to_tv/data/us_rs_3m/README.md`:

````markdown
# US 3M Relative Strength — Published Tables

Daily IBD-style 3-month RS percentiles for the US universe (~5878 tickers), published by `.github/workflows/update_us_rs_3m.yml` every weekday at 01:00 UTC.

## Why this directory exists

Computing the full 3M RS table locally on a home IP gets throttled by Yahoo Finance after ~2000 tickers (rolling IP-cumulative limit, not solvable by `curl_cffi` browser fingerprinting alone). GitHub Actions runners get fresh Azure-pool IPs per run, so the compute runs reliably there.

This mirrors how we already consume Fred6725's 12-month RS CSV — local pipeline reads, cloud pipeline writes.

## Schema

One CSV per weekday, named `<YYYY-MM-DD>.csv` (ISO date, dashes).

| Column | Type | Meaning |
|--------|------|---------|
| `ticker` | string (index) | NASDAQ/NYSE symbol, uppercase |
| `raw_score` | float | `Σ wᵢ·Rᵢ - SPY_score` where `WEIGHTS_3M = [(1,0.5),(2,0.3),(3,0.2)]` |
| `rs_percentile` | int (0-99) | rank of `raw_score` across the universe |

The `raw_score` column is preserved so the IPO ladder (`us_ipo.py`) can `np.searchsorted` against the full distribution to score out-of-universe tickers.

## Retention

The workflow prunes files older than 14 days on every run. To extend or shorten, edit `scripts/compute_us_rs_3m_cloud.py` (`_RETENTION_DAYS` constant).

## Consumed by

- `us_rs_3m.build_3m_table` (local fetcher, via `raw.githubusercontent.com`)
- `main.py` (US EOD pipeline, 10:00 HKT launchd run)

## Manual trigger

If today's CSV is missing (workflow failure), trigger manually:

```bash
gh workflow run update_us_rs_3m.yml
```
````

- [ ] **Step 3: Verify files**

Run: `ls -la /Users/xue/finviz_to_tv/data/us_rs_3m/`
Expected: `.gitkeep` and `README.md` both present.

- [ ] **Step 4: Stage (do not commit yet — bundled with Task 6)**

```bash
git add /Users/xue/finviz_to_tv/data/us_rs_3m/.gitkeep /Users/xue/finviz_to_tv/data/us_rs_3m/README.md
```

---

## Task 2: Failing test for cloud script — coverage guard

**Files:**
- Create: `/Users/xue/finviz_to_tv/tests/test_compute_us_rs_3m_cloud.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for scripts/compute_us_rs_3m_cloud.py — the GH Actions entrypoint.

These tests fully monkeypatch out the external dependencies (Fred6725 CSV
fetch, yfinance batch download, SPY fetch) so the unit tests are fast and
deterministic. The script itself is small — its real integration test is
the workflow's first `workflow_dispatch` run.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

# scripts/ is not a package; tests need a path hack to import.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import compute_us_rs_3m_cloud as cloud  # noqa: E402


def _flat_then_jump(start: float, jump_pct: float, n: int = 70) -> pd.DataFrame:
    closes = [start] * (n - 1) + [start * (1 + jump_pct / 100)]
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=n, freq="B"),
        "close": closes,
    })


def test_main_writes_csv_and_exits_zero_when_coverage_above_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path: 3/3 universe scored → CSV written → exit 0."""
    monkeypatch.setattr(cloud, "fetch_rs_table", lambda *a, **kw: {"AAA": 99, "BBB": 90, "CCC": 50})
    monkeypatch.setattr(cloud, "_fetch_spy_kline", lambda **kw: _flat_then_jump(400.0, 0))
    monkeypatch.setattr(
        cloud, "fetch_us_klines_yf",
        lambda tickers, **kw: {t: _flat_then_jump(100.0, jump_pct=5 + i * 5) for i, t in enumerate(tickers)},
    )
    monkeypatch.setattr(cloud, "_DATA_DIR", tmp_path / "data" / "us_rs_3m")
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 0
    csv = tmp_path / "data" / "us_rs_3m" / "2026-05-22.csv"
    assert csv.exists()
    df = pd.read_csv(csv, index_col="ticker")
    assert set(df.index) == {"AAA", "BBB", "CCC"}
    assert "raw_score" in df.columns
    assert "rs_percentile" in df.columns


def test_main_exits_nonzero_when_coverage_below_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """1 of 5 universe tickers scored = 20% coverage → exit 1, no CSV."""
    monkeypatch.setattr(cloud, "fetch_rs_table", lambda *a, **kw: {"A": 99, "B": 90, "C": 50, "D": 30, "E": 10})
    monkeypatch.setattr(cloud, "_fetch_spy_kline", lambda **kw: _flat_then_jump(400.0, 0))
    monkeypatch.setattr(
        cloud, "fetch_us_klines_yf",
        lambda tickers, **kw: {"A": _flat_then_jump(100.0, 5)},  # 1/5 only
    )
    monkeypatch.setattr(cloud, "_DATA_DIR", tmp_path / "data" / "us_rs_3m")
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 1
    csv = tmp_path / "data" / "us_rs_3m" / "2026-05-22.csv"
    assert not csv.exists(), "no CSV should be written when coverage guard trips"


def test_main_exits_nonzero_when_fred6725_fetch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fred6725 CSV unavailable → exit 1, no compute attempted."""
    monkeypatch.setattr(cloud, "fetch_rs_table", lambda *a, **kw: None)
    monkeypatch.setattr(cloud, "_DATA_DIR", tmp_path / "data" / "us_rs_3m")
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    # If main() doesn't short-circuit on None universe, the test would also
    # need to mock _fetch_spy_kline / fetch_us_klines_yf. The assertion that
    # exit_code == 1 with NO further mocks means the short-circuit works.
    exit_code = cloud.main()

    assert exit_code == 1
    csv = tmp_path / "data" / "us_rs_3m" / "2026-05-22.csv"
    assert not csv.exists()


def test_main_prunes_files_older_than_retention_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a successful write, files older than 14 days are deleted."""
    data_dir = tmp_path / "data" / "us_rs_3m"
    data_dir.mkdir(parents=True)
    # 13 days old → keep; 15 days old → prune; non-date filename → keep.
    (data_dir / "2026-05-09.csv").write_text("ticker,raw_score,rs_percentile\n")  # 13 days old
    (data_dir / "2026-05-07.csv").write_text("ticker,raw_score,rs_percentile\n")  # 15 days old
    (data_dir / "README.md").write_text("docs")  # non-date, keep

    monkeypatch.setattr(cloud, "fetch_rs_table", lambda *a, **kw: {"AAA": 99, "BBB": 90, "CCC": 50})
    monkeypatch.setattr(cloud, "_fetch_spy_kline", lambda **kw: _flat_then_jump(400.0, 0))
    monkeypatch.setattr(
        cloud, "fetch_us_klines_yf",
        lambda tickers, **kw: {t: _flat_then_jump(100.0, 5 + i * 5) for i, t in enumerate(tickers)},
    )
    monkeypatch.setattr(cloud, "_DATA_DIR", data_dir)
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 0
    assert (data_dir / "2026-05-22.csv").exists(), "today's file written"
    assert (data_dir / "2026-05-09.csv").exists(), "13-day-old file kept"
    assert not (data_dir / "2026-05-07.csv").exists(), "15-day-old file pruned"
    assert (data_dir / "README.md").exists(), "non-date file untouched"
```

- [ ] **Step 2: Run tests — expect them to fail (script doesn't exist)**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/test_compute_us_rs_3m_cloud.py -v`
Expected: All 4 tests fail with `ModuleNotFoundError: No module named 'compute_us_rs_3m_cloud'`.

---

## Task 3: Implement `scripts/compute_us_rs_3m_cloud.py`

**Files:**
- Create: `/Users/xue/finviz_to_tv/scripts/compute_us_rs_3m_cloud.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Cloud-side US 3M RS table compute — runs in GitHub Actions weekday cron.

Pulls Fred6725's 12M CSV for the universe, fetches 6mo of yfinance closes
for each ticker (curl_cffi auto-active), computes IBD-style 3M RS via
us_rs_3m.compute_us_rs_3m_table, writes data/us_rs_3m/<today>.csv, and
prunes files older than 14 days.

Exits 1 on:
  - Fred6725 CSV unavailable (no universe to score)
  - Coverage < 50% of universe (Yahoo throttle on the runner — better
    to fail loudly than commit a warped distribution)
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Make repo root importable so we can use `us_rs_3m` and `rs_rating` as
# top-level modules (mirrors how main.py imports them).
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from rs_rating import fetch_rs_table  # noqa: E402
from us_rs_3m import (  # noqa: E402
    _fetch_spy_kline,
    compute_us_rs_3m_table,
    fetch_us_klines_yf,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("compute_us_rs_3m_cloud")

_DATA_DIR = _REPO_ROOT / "data" / "us_rs_3m"
_RETENTION_DAYS = 14
_COVERAGE_THRESHOLD = 0.5


def _today() -> date:
    """Indirection for tests to monkeypatch today's date."""
    return date.today()


def _prune_old_files(today: date) -> int:
    """Delete <YYYY-MM-DD>.csv files in _DATA_DIR older than _RETENTION_DAYS.

    Returns the count of pruned files. Non-date filenames (README.md,
    .gitkeep) are left untouched.
    """
    if not _DATA_DIR.exists():
        return 0
    cutoff = today - timedelta(days=_RETENTION_DAYS)
    pruned = 0
    for p in _DATA_DIR.glob("*.csv"):
        try:
            file_date = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue  # non-date filename, skip
        if file_date < cutoff:
            p.unlink()
            pruned += 1
    return pruned


def main() -> int:
    today = _today()
    logger.info(f"[Cloud RS 3M] Starting compute for {today.isoformat()}")

    # 1. Pull Fred6725 12M CSV for the universe.
    rs_table_12m = fetch_rs_table(Path("/tmp"), today.strftime("%Y_%m_%d"))
    if not rs_table_12m:
        logger.error("[Cloud RS 3M] Fred6725 12M CSV unavailable — no universe to score")
        return 1
    universe = sorted(rs_table_12m.keys())
    logger.info(f"[Cloud RS 3M] Universe: {len(universe)} tickers from Fred6725")

    # 2. Fetch SPY first (mirror local fix B — SPY-first ordering).
    spy_kline = _fetch_spy_kline(period="6mo")
    if spy_kline is None or spy_kline.empty:
        logger.warning("[Cloud RS 3M] SPY fetch failed; falling back to absolute scores")

    # 3. Fetch the full universe (curl_cffi auto-active in CI environment).
    klines = fetch_us_klines_yf(universe, period="6mo")
    if not klines:
        logger.error("[Cloud RS 3M] yfinance returned no klines")
        return 1

    # 4. Compute the table.
    import pandas as pd  # local import to keep top-of-file imports minimal
    spy_for_compute = spy_kline if spy_kline is not None else pd.DataFrame({"time_key": [], "close": []})
    table = compute_us_rs_3m_table(klines, spy_for_compute)

    # 5. Coverage guard.
    coverage = len(table) / len(universe) if universe else 0
    if coverage < _COVERAGE_THRESHOLD:
        logger.error(
            f"[Cloud RS 3M] Coverage {len(table)}/{len(universe)} "
            f"({coverage:.1%}) below {_COVERAGE_THRESHOLD:.0%} threshold — failing"
        )
        return 1

    # 6. Write today's CSV.
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _DATA_DIR / f"{today.isoformat()}.csv"
    table.to_csv(out_path, index_label="ticker")
    logger.info(f"[Cloud RS 3M] Wrote {len(table)} tickers ({coverage:.1%} coverage) → {out_path}")

    # 7. Prune old files.
    pruned = _prune_old_files(today)
    logger.info(f"[Cloud RS 3M] Pruned {pruned} files older than {_RETENTION_DAYS} days")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the test suite — expect all 4 tests pass**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/test_compute_us_rs_3m_cloud.py -v`
Expected: 4 passed.

- [ ] **Step 3: Sanity-check the import path works**

Run: `cd /Users/xue/finviz_to_tv && uv run python -c "import sys; sys.path.insert(0, 'scripts'); import compute_us_rs_3m_cloud; print('OK,', compute_us_rs_3m_cloud._DATA_DIR)"`
Expected: `OK, /Users/xue/finviz_to_tv/data/us_rs_3m`

- [ ] **Step 4: Stage (do not commit yet — bundled with Task 6)**

```bash
git add /Users/xue/finviz_to_tv/scripts/compute_us_rs_3m_cloud.py /Users/xue/finviz_to_tv/tests/test_compute_us_rs_3m_cloud.py
```

---

## Task 4: Write the GitHub Actions workflow

**Files:**
- Create: `/Users/xue/finviz_to_tv/.github/workflows/update_us_rs_3m.yml`

- [ ] **Step 1: Write the workflow YAML**

```yaml
name: Update US 3M RS Table

on:
  schedule:
    - cron: '0 1 * * 1-5'      # 01:00 UTC Mon-Fri = 09:00 HKT (~1h before local 10:00 launchd run)
  workflow_dispatch:           # manual trigger for debugging + first seed

permissions:
  contents: write              # commit the daily CSV back to main

jobs:
  build-3m-table:
    runs-on: ubuntu-latest
    timeout-minutes: 60        # Fred6725 budget is 90 for 6800; ours is ~5900

    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --frozen

      - name: Verify curl_cffi active
        run: uv run python -c "import curl_cffi; import yfinance; print('curl_cffi', curl_cffi.__version__, '/ yfinance', yfinance.__version__)"

      - name: Compute 3M RS table
        run: uv run python scripts/compute_us_rs_3m_cloud.py

      - name: Commit and push
        run: |
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git config user.name "github-actions[bot]"
          git add data/us_rs_3m/
          git diff --staged --quiet || git commit -m "chore(us_rs_3m): publish $(date -u +%Y-%m-%d) table"
          git push
```

- [ ] **Step 2: Lint the YAML (catches indent/syntax errors before push)**

Run: `cd /Users/xue/finviz_to_tv && uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/update_us_rs_3m.yml'))"`
Expected: No output, exit 0.

- [ ] **Step 3: Stage (do not commit yet — bundled with Task 6)**

```bash
git add /Users/xue/finviz_to_tv/.github/workflows/update_us_rs_3m.yml
```

---

## Task 5: Manually verify the cloud script runs locally (smoke test before pushing the workflow)

**Files:** none modified — pure runtime check.

This task de-risks the first workflow run by catching any local-vs-CI environment mismatch before we publish a workflow that might fail visibly on GitHub.

- [ ] **Step 1: Run the cloud script locally with full live network**

⚠ **Yahoo throttle warning:** earlier runs today (19:11, 20:53, 21:10) likely exhausted today's IP-cumulative budget. Expect this local run to also fail with `coverage < 50%`. **That is the correct outcome and validates the coverage guard.** The real test is the CI workflow run in Task 6.

Run: `cd /Users/xue/finviz_to_tv && uv run python scripts/compute_us_rs_3m_cloud.py`
Expected: Either (a) clean exit 0 with a CSV in `data/us_rs_3m/`, OR (b) exit 1 with the coverage-guard log line. **Both are acceptable.**

- [ ] **Step 2: If a CSV was written, sanity-check its shape**

```bash
ls -la /Users/xue/finviz_to_tv/data/us_rs_3m/
head -3 /Users/xue/finviz_to_tv/data/us_rs_3m/*.csv 2>/dev/null | head -10
```
Expected (if exit 0): CSV file with columns `ticker,raw_score,rs_percentile` and ~5800 rows.

- [ ] **Step 3: Clean up local artifacts before commit (we want CI to write the first real file)**

```bash
rm -f /Users/xue/finviz_to_tv/data/us_rs_3m/2026-*.csv
ls /Users/xue/finviz_to_tv/data/us_rs_3m/
```
Expected: Only `.gitkeep` and `README.md` remain.

---

## Task 6: Commit A — push cloud infrastructure to main

**Files:** all 6 files staged in Tasks 1, 3, 4 (README, .gitkeep, cloud script, test, workflow YAML).

- [ ] **Step 1: Verify staged contents**

Run: `cd /Users/xue/finviz_to_tv && git status`
Expected: Six new files staged:
```
new file:   .github/workflows/update_us_rs_3m.yml
new file:   data/us_rs_3m/.gitkeep
new file:   data/us_rs_3m/README.md
new file:   scripts/compute_us_rs_3m_cloud.py
new file:   tests/test_compute_us_rs_3m_cloud.py
```

- [ ] **Step 2: Run the full test suite to confirm no regression**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/ -q`
Expected: All tests pass (current count was 247 + 4 new = 251).

- [ ] **Step 3: Commit**

```bash
cd /Users/xue/finviz_to_tv && git commit -m "$(cat <<'EOF'
feat(us_rs_3m): cloud infrastructure for daily 3M RS table

Adds the GitHub Actions side of the cloud-pipeline migration. The
local pipeline is NOT yet cut over — that's a separate commit.

- scripts/compute_us_rs_3m_cloud.py: standalone entrypoint that pulls
  Fred6725's 12M CSV for the universe, runs the existing
  compute_us_rs_3m_table on yfinance data, writes
  data/us_rs_3m/<date>.csv. Exits non-zero on coverage < 50% so the
  workflow fails loudly instead of committing a warped table.
- .github/workflows/update_us_rs_3m.yml: cron 0 1 * * 1-5 (01:00 UTC
  weekdays, ~1h before local launchd 10:00 HKT). Commits the new
  CSV back to main; default GITHUB_TOKEN suffices.
- data/us_rs_3m/{.gitkeep,README.md}: directory placeholder + docs.
- tests/test_compute_us_rs_3m_cloud.py: 4 monkeypatched unit tests
  for the coverage guard, write, prune, and no-universe paths.

This commit alone leaves the local pipeline behavior unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push and trigger the workflow manually to seed the first CSV**

```bash
cd /Users/xue/finviz_to_tv && git push
gh workflow run update_us_rs_3m.yml
```
Expected output for `git push`: `... main -> main`.
Expected output for `gh workflow run`: `✓ Created workflow_dispatch event for update_us_rs_3m.yml at main`.

- [ ] **Step 5: Watch the workflow run and verify it produces a CSV**

```bash
cd /Users/xue/finviz_to_tv && gh run watch
```

Wait for completion (5-30 minutes depending on Yahoo response time). Expected: workflow ends with status `completed success`, and a new commit appears on `main` titled `chore(us_rs_3m): publish <today> table`.

- [ ] **Step 6: Pull the new commit and verify the CSV**

```bash
cd /Users/xue/finviz_to_tv && git pull
ls -la data/us_rs_3m/
head -3 data/us_rs_3m/*.csv | head -10
```
Expected: A new `<YYYY-MM-DD>.csv` file with ~5800 rows, columns `ticker,raw_score,rs_percentile`.

- [ ] **Step 7: Verify the raw URL is reachable from the public internet**

```bash
TODAY=$(ls /Users/xue/finviz_to_tv/data/us_rs_3m/2026-*.csv | head -1 | xargs basename | sed 's/.csv//')
curl -sI "https://raw.githubusercontent.com/EricXue92/finvinz_to_tv/main/data/us_rs_3m/${TODAY}.csv" | head -2
```
Expected: `HTTP/2 200`.

**If Step 5 fails or Step 7 returns 404**, STOP. Do not proceed to Task 7. The local code still works with the old compute path; investigate the workflow failure first (check `gh run view <run-id> --log`).

---

## Task 7: Failing test for `_fetch_cloud_csv`

**Files:**
- Create: `/Users/xue/finviz_to_tv/tests/test_us_rs_3m_fetcher.py`

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for us_rs_3m._fetch_cloud_csv and the rewritten build_3m_table.

These cover the HTTP-fetcher path, the 4-day stale-fallback walk
(today → 1d → 2d → 3d), the local-cache short-circuit on same-day
reruns, and the final passthrough when all fetches fail.

The yfinance/Fred6725 dependencies are gone from build_3m_table after
the rewrite; only HTTP and disk I/O remain.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

import us_rs_3m


_FIXTURE_CSV = "ticker,raw_score,rs_percentile\nAAPL,0.12,95\nMSFT,0.08,80\n"


def _make_http_response(body: str, status: int = 200):
    """Build a context-manager-compatible response object for urlopen mock."""
    response = MagicMock()
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    response.read = MagicMock(return_value=body.encode("utf-8"))
    # pd.read_csv needs an iterable / file-like; provide via read() above.
    return response


# ── _fetch_cloud_csv ─────────────────────────────────────────────────────

def test_fetch_cloud_csv_success_returns_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_urlopen(req, timeout=30):
        # pd.read_csv reads the response object directly; we return one that
        # behaves like a file (StringIO works because pd handles it).
        return _wrap_for_read_csv(_FIXTURE_CSV)

    monkeypatch.setattr(us_rs_3m, "urlopen", _fake_urlopen)
    table = us_rs_3m._fetch_cloud_csv("https://example.invalid/today.csv")
    assert table is not None
    assert list(table.index) == ["AAPL", "MSFT"]
    assert table.loc["AAPL", "rs_percentile"] == 95


def test_fetch_cloud_csv_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_urlopen(req, timeout=30):
        raise HTTPError("https://example.invalid/missing.csv", 404, "Not Found", {}, None)

    monkeypatch.setattr(us_rs_3m, "urlopen", _fake_urlopen)
    assert us_rs_3m._fetch_cloud_csv("https://example.invalid/missing.csv") is None


def test_fetch_cloud_csv_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_urlopen(req, timeout=30):
        raise URLError("dns failure")

    monkeypatch.setattr(us_rs_3m, "urlopen", _fake_urlopen)
    assert us_rs_3m._fetch_cloud_csv("https://example.invalid/today.csv") is None


def test_fetch_cloud_csv_malformed_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_urlopen(req, timeout=30):
        return _wrap_for_read_csv("not,a,valid\ncsv\n\"unterminated")

    monkeypatch.setattr(us_rs_3m, "urlopen", _fake_urlopen)
    # Should swallow the pandas exception and return None, not raise.
    assert us_rs_3m._fetch_cloud_csv("https://example.invalid/bad.csv") is None


def _wrap_for_read_csv(body: str):
    """urlopen returns a context manager whose `.read()` gives bytes.
    pd.read_csv accepts a buffer that supports .read() — we provide that."""
    response = MagicMock()
    response.__enter__ = MagicMock(return_value=io.BytesIO(body.encode("utf-8")))
    response.__exit__ = MagicMock(return_value=False)
    return response


# ── build_3m_table (rewritten fetcher) ───────────────────────────────────

def test_build_3m_table_uses_today_cloud_csv_and_mirrors_to_local_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    fixture = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="ticker")

    def _fake_fetch(url, timeout=30):
        # Verify the URL targets TODAY (delta=0)
        assert "2026-05-22.csv" in url, url
        return fixture

    monkeypatch.setattr(us_rs_3m, "_fetch_cloud_csv", _fake_fetch)
    table = us_rs_3m.build_3m_table(tmp_path, today)
    assert table is not None
    assert list(table.index) == ["AAPL", "MSFT"]
    # Local cache mirror should exist for same-day rerun short-circuit
    cache = tmp_path / "state" / "rs_rating_3m_2026-05-22.csv"
    assert cache.exists()


def test_build_3m_table_walks_back_to_stale_fallback_and_does_not_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    fixture = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="ticker")
    calls: list[str] = []

    def _fake_fetch(url, timeout=30):
        calls.append(url)
        # Today, 1d, 2d all 404; 3d-old succeeds.
        if "2026-05-19" in url:
            return fixture
        return None

    monkeypatch.setattr(us_rs_3m, "_fetch_cloud_csv", _fake_fetch)
    table = us_rs_3m.build_3m_table(tmp_path, today)
    assert table is not None
    assert len(calls) == 4  # today, -1, -2, -3
    assert "2026-05-22" in calls[0]
    assert "2026-05-19" in calls[3]
    # Stale fallbacks must NOT write the local cache (would mask staleness on next rerun)
    cache = tmp_path / "state" / "rs_rating_3m_2026-05-22.csv"
    assert not cache.exists()


def test_build_3m_table_returns_none_when_all_fetches_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    monkeypatch.setattr(us_rs_3m, "_fetch_cloud_csv", lambda *a, **kw: None)
    table = us_rs_3m.build_3m_table(tmp_path, today)
    assert table is None


def test_build_3m_table_local_cache_short_circuit_skips_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If output/state/rs_rating_3m_<today>.csv exists, no HTTP at all."""
    today = date(2026, 5, 22)
    fixture = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="ticker")
    us_rs_3m.save_cache(fixture, today, tmp_path)

    def _fail_on_fetch(*a, **kw):
        pytest.fail("_fetch_cloud_csv should not be called when local cache hits")

    monkeypatch.setattr(us_rs_3m, "_fetch_cloud_csv", _fail_on_fetch)
    table = us_rs_3m.build_3m_table(tmp_path, today)
    assert table is not None
    assert list(table.index) == ["AAPL", "MSFT"]
```

- [ ] **Step 2: Run tests — expect failures**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/test_us_rs_3m_fetcher.py -v`
Expected: All 8 tests fail (some with `AttributeError: module 'us_rs_3m' has no attribute '_fetch_cloud_csv'`; the `build_3m_table` ones fail because the current implementation tries to call `fetch_universe_from_rs_csv` and `fetch_us_klines_yf` rather than `_fetch_cloud_csv`).

---

## Task 8: Implement `_fetch_cloud_csv` and rewrite `build_3m_table`

**Files:**
- Modify: `/Users/xue/finviz_to_tv/us_rs_3m.py`

- [ ] **Step 1: Add new imports at the top of `us_rs_3m.py`**

Open `/Users/xue/finviz_to_tv/us_rs_3m.py` and modify the import block (lines 13-19):

Replace:
```python
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd
```

With:
```python
from __future__ import annotations

import io
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
```

- [ ] **Step 2: Add module-level constants after `WEIGHTS_3M`**

Find line 23 (`WEIGHTS_3M: list[tuple[int, float]] = [(1, 0.5), (2, 0.3), (3, 0.2)]`) and add immediately after it:

```python

# Cloud-published CSV (written by .github/workflows/update_us_rs_3m.yml).
# Local pipeline reads from raw.githubusercontent.com instead of computing
# locally — the home-IP yfinance compute hits Yahoo's IP-cumulative rate
# limit after ~2000 tickers (see spec 2026-05-21-us-rs-3m-cloud-pipeline).
_CLOUD_CSV_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/EricXue92/finvinz_to_tv/main/"
    "data/us_rs_3m/{date}.csv"
)

# Walk back up to N days if today's CSV isn't published yet. Mirrors
# rs_rating._FALLBACK_MAX_AGE_DAYS — US EOD runs Mon-Fri so a Mon-failure
# + Tue-failure gap is at worst 2 days; 3 gives margin.
_FALLBACK_MAX_AGE_DAYS = 3
```

- [ ] **Step 3: Add `_fetch_cloud_csv` helper above `cache_path`**

Find line 127 (`def cache_path(today: date, output_dir: Path) -> Path:`) and insert above it:

```python
def _fetch_cloud_csv(url: str, timeout: int = 30) -> pd.DataFrame | None:
    """Fetch a 3M CSV from the cloud-published artifact.

    Returns the DataFrame indexed by ticker on success, or None on HTTP
    404 (today's file not yet published), network error, or parse failure.
    Never raises — callers walk back day-by-day on None.
    """
    try:
        req = Request(url, headers={"User-Agent": "finviz-to-tv/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return pd.read_csv(io.BytesIO(body), index_col="ticker")
    except HTTPError as e:
        if e.code == 404:
            return None  # today's file may not be published yet; caller falls back
        logger.warning(f"[US RS 3M] HTTP {e.code} fetching {url}")
        return None
    except (URLError, TimeoutError) as e:
        logger.warning(f"[US RS 3M] Network error fetching {url}: {e}")
        return None
    except Exception as e:
        # pd.read_csv can raise ParserError, EmptyDataError, etc. — all
        # treated as "this URL didn't yield a usable table".
        logger.warning(f"[US RS 3M] Failed to parse {url}: {type(e).__name__}: {e}")
        return None


```

- [ ] **Step 4: Replace `build_3m_table` body**

Find `def build_3m_table(` (around line 264) and replace the entire function (until the next top-level def or EOF) with:

```python
def build_3m_table(
    output_dir: Path,
    today: date,
    rs_table_12m: dict[str, int] | None = None,
) -> pd.DataFrame | None:
    """Load today's US 3M RS table from the cloud-published CSV.

    Strategy:
      1. Local cache hit (same-day rerun) → return immediately.
      2. HTTP fetch today's cloud CSV; walk back up to _FALLBACK_MAX_AGE_DAYS
         if not yet published.
      3. All fail → return None (callers degrade via filter_by_rs's
         missing→passthrough policy).

    The local compute path was removed in 2026-05-21 because home-IP
    runs were getting throttled mid-loop; compute now happens in
    .github/workflows/update_us_rs_3m.yml on ephemeral GH runners.

    ``rs_table_12m`` is retained for signature compatibility with the
    pre-cloud call sites but is no longer consulted.
    """
    del rs_table_12m  # unused; signature kept for back-compat

    cached = load_cache(today, output_dir)
    if cached is not None and not cached.empty:
        logger.info(f"[US RS 3M] Using cached table: {len(cached)} tickers")
        return cached

    for delta in range(_FALLBACK_MAX_AGE_DAYS + 1):
        target_date = today - timedelta(days=delta)
        url = _CLOUD_CSV_URL_TEMPLATE.format(date=target_date.isoformat())
        table = _fetch_cloud_csv(url)
        if table is None or table.empty:
            continue
        if delta == 0:
            logger.info(f"[US RS 3M] Fetched cloud CSV: {len(table)} tickers")
            save_cache(table, today, output_dir)
        else:
            logger.warning(
                f"[US RS 3M] Cloud CSV for {today.isoformat()} not available; "
                f"using stale fallback from {target_date.isoformat()} ({delta} day(s) old)"
            )
        return table

    logger.warning(
        f"[US RS 3M] No cloud CSV within {_FALLBACK_MAX_AGE_DAYS} days; "
        "3M layer will passthrough "
        "(check https://github.com/EricXue92/finvinz_to_tv/actions)"
    )
    return None
```

- [ ] **Step 5: Run the new fetcher tests**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/test_us_rs_3m_fetcher.py -v`
Expected: All 8 tests pass.

- [ ] **Step 6: Run the full test suite — some old `test_build_3m_table_*` tests will now fail**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/ -q`
Expected: 3 failures in `tests/test_us_rs_3m.py`:
- `test_build_3m_table_orchestration` (monkeypatches `fetch_us_klines_yf` / `_fetch_spy_kline` which `build_3m_table` no longer calls)
- `test_build_3m_table_refuses_to_cache_sparse_coverage` (D-guard moved to cloud script)
- `test_build_3m_table_uses_cache` (still works in principle, but is now duplicated by `test_build_3m_table_local_cache_short_circuit_skips_http`)

These get cleaned up in Task 9.

---

## Task 9: Remove obsolete tests from `tests/test_us_rs_3m.py`

**Files:**
- Modify: `/Users/xue/finviz_to_tv/tests/test_us_rs_3m.py`

- [ ] **Step 1: Delete the three obsolete `test_build_3m_table_*` tests**

Open `/Users/xue/finviz_to_tv/tests/test_us_rs_3m.py`. Find and delete (from the `def test_build_3m_table_orchestration(` line through the end of `def test_build_3m_table_uses_cache(`):

- `test_build_3m_table_orchestration` (line ~243)
- `test_build_3m_table_refuses_to_cache_sparse_coverage` (line ~271)
- `test_build_3m_table_uses_cache` (line ~301)

Use the Edit tool with three separate `Edit` calls (one per test, with enough context to make `old_string` unique).

Example for the first one — find this block and replace with empty (effectively deleting):

```python
def test_build_3m_table_orchestration(monkeypatch, tmp_path):
    """Verify build_3m_table composes universe→fetch→compute→cache."""
    import us_rs_3m

    def _fake_universe(rs_table_12m):
        return ["AAA", "BBB", "CCC"]
    ...
    assert cache.exists()


```
(Use the actual file contents — read the file first to confirm exact lines.)

- [ ] **Step 2: Confirm file structure after deletion**

Run: `grep "^def test_" /Users/xue/finviz_to_tv/tests/test_us_rs_3m.py | wc -l`
Expected: 19 (was 22; we removed 3).

Run: `grep "^def test_build_3m_table" /Users/xue/finviz_to_tv/tests/test_us_rs_3m.py`
Expected: no output (all three deleted).

- [ ] **Step 3: Run the full test suite**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/ -q`
Expected: All passing. Count = 247 (original) - 3 (deleted) + 4 (new cloud-script tests) + 8 (new fetcher tests) = **256 passed**.

---

## Task 10: End-to-end smoke test of the new local fetcher

**Files:** none modified — runtime check.

This validates that `main.py`'s call to `us_rs_3m.build_3m_table` actually reads from the cloud CSV pushed by Task 6's workflow run.

- [ ] **Step 1: Verify no stale local cache exists that would short-circuit the test**

```bash
ls /Users/xue/finviz_to_tv/output/state/rs_rating_3m_*.csv 2>/dev/null || echo "no local cache (good)"
```
If a file exists, delete it: `rm /Users/xue/finviz_to_tv/output/state/rs_rating_3m_*.csv`.

- [ ] **Step 2: Run the US EOD pipeline and watch the 3M log line**

```bash
cd /Users/xue/finviz_to_tv && uv run main.py --mode us-eod 2>&1 | grep -E "US RS 3M|Loaded.*tickers"
```

Expected: Log lines like
```
[RS Rating] Loaded 5878 tickers (IBD percentile 0-99)
[US RS 3M] Fetched cloud CSV: NNNN tickers
```

**Critically:** there should be **NO** `[US RS 3M] yfinance batch 1/N` log lines — those mean we're still on the old compute path.

- [ ] **Step 3: Verify the local cache was mirrored**

```bash
ls /Users/xue/finviz_to_tv/output/state/rs_rating_3m_*.csv
```
Expected: Today's file present.

- [ ] **Step 4: Run again and confirm same-day cache short-circuit**

```bash
cd /Users/xue/finviz_to_tv && uv run main.py --mode us-eod 2>&1 | grep "US RS 3M"
```
Expected: `[US RS 3M] Using cached table: NNNN tickers` (no HTTP fetch line).

---

## Task 11: Commit B — local fetcher swap

**Files modified in Tasks 7-10:**
- `us_rs_3m.py`
- `tests/test_us_rs_3m.py`
- `tests/test_us_rs_3m_fetcher.py` (new)

- [ ] **Step 1: Verify staged contents**

```bash
cd /Users/xue/finviz_to_tv && git status
```
Expected:
```
modified:   us_rs_3m.py
modified:   tests/test_us_rs_3m.py
new file:   tests/test_us_rs_3m_fetcher.py
```

- [ ] **Step 2: Run the full test suite one more time**

```bash
cd /Users/xue/finviz_to_tv && uv run pytest tests/ -q
```
Expected: 256 passed.

- [ ] **Step 3: Stage and commit**

```bash
cd /Users/xue/finviz_to_tv && git add us_rs_3m.py tests/test_us_rs_3m.py tests/test_us_rs_3m_fetcher.py
git commit -m "$(cat <<'EOF'
refactor(us_rs_3m): swap local compute for cloud-CSV fetcher

The home IP can't sustain the 6000-ticker yfinance pull without
Yahoo throttling mid-loop (curl_cffi browser fingerprint doesn't
fix IP-cumulative limits). After commit A added the GH-Actions
workflow that publishes data/us_rs_3m/<date>.csv on a fresh runner
IP each weekday, build_3m_table no longer needs to compute locally.

- us_rs_3m._fetch_cloud_csv: stdlib urlopen → pd.read_csv,
  returns None on 404 / network error / parse failure.
- us_rs_3m.build_3m_table: rewritten as local-cache → today's
  cloud CSV → walk back up to 3 days stale → passthrough. Mirrors
  the proven rs_rating fallback pattern. Local cache mirror is
  written ONLY for today's file (stale fallbacks read-through to
  preserve staleness signal on next rerun).
- tests/test_us_rs_3m_fetcher.py: 8 unit tests covering the HTTP
  fetcher, today/stale/all-fail paths, and same-day short-circuit.
- tests/test_us_rs_3m.py: removed 3 obsolete build_3m_table tests
  (old D-guard logic moved to the cloud script).

Total: 256 tests pass (was 247 + 8 fetcher + 4 cloud script - 3
obsolete = +9 net).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
cd /Users/xue/finviz_to_tv && git push
```

---

## Task 12: Commit C — update `CLAUDE.md` to document the new flow

**Files:**
- Modify: `/Users/xue/finviz_to_tv/CLAUDE.md`

- [ ] **Step 1: Find the relevant section**

Run: `grep -n "3M layer\|min_rs_percentile_3m\|US 3M" /Users/xue/finviz_to_tv/CLAUDE.md | head -20`

The main section is around line 95-112 (the "IBD Relative Strength Rating" heading and the "US: 12M (Fred6725 CSV, vs SPY) + 3M (Leaders/RS/Shorts only, vs SPY)" subsection).

- [ ] **Step 2: Read the current section to preserve context**

Run: `sed -n '93,115p' /Users/xue/finviz_to_tv/CLAUDE.md`

(Use the actual line numbers from Step 1's grep output.)

- [ ] **Step 3: Replace the "3M layer (added 2026-05-21)" paragraph**

Find the paragraph that currently begins:
```
**3M layer (added 2026-05-21).** `us_rs_3m.py` computes a second IBD-style percentile locally...
```

And replace its content. Use the Edit tool with this `old_string` (capture the entire paragraph between blank lines) and the following `new_string`:

```markdown
**3M layer (cloud-computed, added 2026-05-21).** The 3M IBD-style percentile (`WEIGHTS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63` vs SPY, universe = Fred6725 12M table's tickers) is computed daily on GitHub Actions and published as `data/us_rs_3m/<YYYY-MM-DD>.csv` in this repo. Workflow: `.github/workflows/update_us_rs_3m.yml`, cron `0 1 * * 1-5` (01:00 UTC weekdays = 09:00 HKT, ~1h before the local 10:00 launchd run). The script in `scripts/compute_us_rs_3m_cloud.py` reuses `us_rs_3m.compute_us_rs_3m_table` and exits non-zero (failing the workflow, no commit) when coverage < 50%.

Local `us_rs_3m.build_3m_table` is a thin HTTP fetcher: cache → today's cloud CSV → walk back up to 3 days stale → passthrough. The CSV is read via `raw.githubusercontent.com/EricXue92/finvinz_to_tv/main/data/us_rs_3m/<date>.csv` (public repo, no auth). Today's file is mirrored to `output/state/rs_rating_3m_<date>.csv` for same-day rerun short-circuit; stale fallbacks are NOT mirrored (would mask staleness).

Why moved off-host: the home-IP yfinance compute throttled after ~2000 tickers (Yahoo IP-cumulative rate limit, not solvable by curl_cffi alone — multi-run sessions on 2026-05-21 confirmed). GH Actions runners get fresh IPs per run; Fred6725 uses the same pattern at the same scale and runs reliably. The compute logic in `us_rs_3m.py` (`fetch_us_klines_yf`, `_fetch_spy_kline`, `compute_us_rs_3m_table`) is unchanged — only the orchestrator at the top moved.

The 3M gate applies to **Leaders + conditional RS group + Shorts** only (same as before). **Longs 5 splits stay 12M-only** (event filters make 3M over-restrictive). Both layers use threshold ≥ 90 by default (`[settings] min_rs_percentile_3m = 90`); set to 0 to disable the 3M layer entirely (skips the cloud-CSV fetch attempt).
```

(Use the Edit tool, specifying the existing paragraph as `old_string` with a few lines of surrounding context to make it unique.)

- [ ] **Step 4: Verify the edit**

Run: `grep -A3 "3M layer (cloud-computed" /Users/xue/finviz_to_tv/CLAUDE.md`
Expected: First 3 lines of the new paragraph visible.

- [ ] **Step 5: Commit and push**

```bash
cd /Users/xue/finviz_to_tv && git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(CLAUDE): describe cloud-computed US 3M RS pipeline

Updates the IBD Relative Strength Rating section to document the
post-2026-05-21 flow: compute on GH Actions (workflow
update_us_rs_3m.yml, weekday cron), publish to data/us_rs_3m/,
local pipeline fetches via raw.githubusercontent. Records why we
moved off-host (Yahoo IP-cumulative throttle that curl_cffi alone
couldn't fix) and the unchanged scope of the 3M gate.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push
```

---

## Self-Review

**Spec coverage** (skimmed `2026-05-21-us-rs-3m-cloud-pipeline-design.md`):

| Spec section | Plan task |
|--------------|-----------|
| Module split / file layout | Task 1 (data dir), Task 3 (cloud script), Task 4 (workflow), Task 8 (us_rs_3m.py rewrite) |
| Cloud script flow (Fred6725 → universe → SPY first → fetch → compute → coverage guard → write → prune) | Task 3, all 7 numbered steps from the spec captured in the implementation |
| Workflow YAML (cron, permissions, timeout, steps) | Task 4, exactly the YAML from the spec |
| `_fetch_cloud_csv` + `build_3m_table` rewrite | Tasks 7 (tests), 8 (implementation) |
| Local cache mirror policy (today-only) | Task 8 Step 4 implementation explicitly does `if delta == 0: save_cache(...)`; Task 7 test `test_build_3m_table_walks_back_to_stale_fallback_and_does_not_cache` enforces |
| Tests for `_fetch_cloud_csv` (200/404/network/parse) | Task 7, 4 tests |
| Tests for `build_3m_table` (today / stale / all-fail / cache short-circuit) | Task 7, 4 tests |
| Cloud script tests (coverage guard / no universe / prune) | Task 2, 4 tests |
| Migration order (3 commits, zero-downtime rollback) | Task 6 (commit A), 11 (commit B), 12 (commit C) — explicit STOP gate at Task 6 Step 7 |
| CLAUDE.md update | Task 12 |

**Placeholder scan:** All code blocks contain complete, runnable code. All commands have expected output. No "TBD", "TODO", "similar to Task N", or vague references.

**Type consistency:** `build_3m_table(output_dir: Path, today: date, rs_table_12m: dict[str, int] | None = None) -> pd.DataFrame | None` — same signature in the spec, Task 7 tests (which only pass 2 args), and Task 8 implementation. `_fetch_cloud_csv(url: str, timeout: int = 30) -> pd.DataFrame | None` — consistent in spec, tests, and implementation. `_DATA_DIR` is a module-level `Path` in both the spec and the implementation; tests monkeypatch it.

**One minor self-correction inline:** Task 9 Step 1 uses the Edit tool rather than `sed -i` because the user's CLAUDE.md notes prefer Edit over sed for file modifications. Plan reflects this.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-21-us-rs-3m-cloud-pipeline.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each commit boundary (Tasks 6, 11, 12) is a natural review checkpoint.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Better if you want to watch every step.

**Which approach?**
