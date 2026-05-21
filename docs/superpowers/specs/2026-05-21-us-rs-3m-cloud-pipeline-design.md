# US 3M RS — Cloud Pipeline Design

**Date:** 2026-05-21
**Author:** XUE (with Claude)
**Status:** Approved, ready for implementation plan

## Problem

Computing the US 3M RS table locally requires ~6000 yfinance ticker downloads
per run. Yahoo Finance applies rolling, IP-cumulative rate limiting that bites
after ~1500-2000 tickers. The home-IP runs at 19:11 / 20:53 / 21:10 HKT on
2026-05-21 each got further than the last (curl_cffi browser fingerprinting
helped — see `commit 821ea5d`) but all three eventually hit the IP ceiling
mid-loop:

- 19:11: scored 1301/5878 (22%), SPY also failed
- 20:53: scored ~1400 before throttle cascade
- 21:10: same pattern; coverage guard refused to cache (working as designed)

Defensive layers A/B/C/D + curl_cffi (commit `821ea5d`) materially improved
the failure shape but cannot fix the root cause: **a single home IP cannot
sustain 5000+ Yahoo requests in tight succession without throttling**.

Fred6725's [relative-strength](https://github.com/Fred6725/relative-strength)
repo solves this by running on GitHub Actions — each `ubuntu-latest` runner
gets a fresh Azure-pool IP, so cumulative throttle never builds up. Their
workflow takes ~3 hours for 6800 tickers (per their own
`timeout-minutes: 360` budget comment) and succeeds reliably.

## Goal

Move the US 3M RS computation to GitHub Actions, mirroring Fred6725's pattern.
Local pipeline becomes a thin fetcher that reads a daily-published CSV via
`raw.githubusercontent.com` — exactly how `rs_rating.py` already consumes
Fred6725's 12M CSV.

## Non-goals

- **Not changing the 12M flow.** `rs_rating.py` continues to pull Fred6725's
  CSV as before.
- **Not changing the HK 3M flow.** HK universe is ~2400 tickers and stays
  within Yahoo's single-IP tolerance; `hk_rs.compute_rs_table` keeps running
  locally inside `hk_eod`.
- **Not changing call sites.** `main.py:1537` and downstream filtering at
  `main.py:1632/1677/1713` use the same `build_3m_table(...)` signature.
- **Not adding a new external service.** No third-party hosting, no S3, no
  separate companion repo. The data lives in `finvinz_to_tv` itself.
- **Not building a Slack/ntfy alert on cloud failure.** GitHub's built-in
  "workflow failed" email is sufficient.

## Architecture

### Module split

| File | Role | Status |
|------|------|--------|
| `us_rs_3m.py` — pure compute (`_score_from_kline`, `compute_us_rs_3m_table`, `filter_by_rs`), cache I/O (`cache_path`, `save_cache`, `load_cache`), yfinance helpers (`fetch_us_klines_yf`, `_fetch_spy_kline`) | shared by cloud + local | unchanged |
| `us_rs_3m.build_3m_table` | local orchestrator | **rewritten** — was compute, becomes HTTP fetcher |
| `scripts/compute_us_rs_3m_cloud.py` | GH Actions entrypoint | new |
| `.github/workflows/update_us_rs_3m.yml` | weekday cron workflow | new |
| `data/us_rs_3m/` | published CSVs (one per weekday) | new directory |

### Repo file layout

```
data/
  us_rs_3m/
    README.md              # explains schema + provenance
    .gitkeep               # keeps directory in fresh clones
    2026-05-22.csv         # daily files: ticker,raw_score,rs_percentile
    2026-05-23.csv
    ...                    # retention: last 14 days, older auto-pruned by workflow
```

`data/` is **not** in `.gitignore` (verified). Single commit per weekday with
message `chore(us_rs_3m): publish <YYYY-MM-DD> table`.

### Data flow

```
(daily at 01:00 UTC weekdays, on GH Actions runner)
  Fred6725 rs_stocks.csv      ──┐
  yfinance batch (6000 ticker)──┼─> compute_us_rs_3m_table ─> data/us_rs_3m/<date>.csv ─> git push
  yfinance SPY (1 ticker)     ──┘                                                            │
                                                                                              v
(daily at 10:00 HKT = 02:00 UTC, on user's Mac via launchd)                    raw.githubusercontent.com
  main.py --mode us-eod                                                                       │
    └─> us_rs_3m.build_3m_table  <─────────────────── HTTP GET <──────────────────────────────┘
          (today → 1d stale → 2d stale → 3d stale → passthrough)
```

## Component specs

### 1. Cloud script — `scripts/compute_us_rs_3m_cloud.py`

Standalone entrypoint, ~50 lines. **No `main.py` dependency** (`main.py` pulls
in Finviz, Futu, ntfy, etc. — none needed here).

**Flow:**

1. Fetch Fred6725 12M CSV via existing `rs_rating.fetch_rs_table(today=date.today(), output_dir=Path("/tmp"))`. Reuses retry/cache logic for free; the `/tmp` output dir is throwaway since the CI runner is ephemeral.
2. `universe = sorted(rs_table_12m.keys())` — typically ~5878 tickers.
3. `spy_kline = us_rs_3m._fetch_spy_kline(period="6mo")` — fetched first, mirrors local fix B.
4. `klines = us_rs_3m.fetch_us_klines_yf(universe, period="6mo")` — 60 batches × 100 tickers with `threads=True` + 5s inter-batch sleep + 30s throttle cooldown (existing logic). curl_cffi auto-active via `import curl_cffi` in environment.
5. `table = us_rs_3m.compute_us_rs_3m_table(klines, spy_kline)`.
6. **Coverage guard** (mirror of local D-guard, moved here):
   ```python
   coverage = len(table) / len(universe)
   if coverage < 0.5:
       print(f"FAIL: coverage {coverage:.1%} below 50% threshold", file=sys.stderr)
       sys.exit(1)
   ```
   Hard exit non-zero — better to fail the workflow loudly than commit a bad CSV that downstream consumers (just us, for now) would read.
7. Write `data/us_rs_3m/<today.isoformat()>.csv` via `table.to_csv(path, index_label="ticker")`. Columns: `ticker, raw_score, rs_percentile`.
8. **Prune** files older than 14 days: `for f in Path("data/us_rs_3m").glob("*.csv"): if iso_date < today - 14d: f.unlink()`. Idempotent — re-running same day re-prunes nothing.
9. Print summary: `Built {N} tickers ({pct}% coverage), pruned {M} old files`.

**Reuses (zero new compute logic):**
- `rs_rating.fetch_rs_table`
- `us_rs_3m._fetch_spy_kline`
- `us_rs_3m.fetch_us_klines_yf`
- `us_rs_3m.compute_us_rs_3m_table`

**Adds:**
- Universe extraction (one line: `sorted(rs_table_12m.keys())`)
- Coverage guard with `sys.exit(1)` (replaces returning None)
- 14-day prune loop
- Direct `to_csv` to `data/us_rs_3m/` (not `output/state/`)

### 2. Workflow — `.github/workflows/update_us_rs_3m.yml`

```yaml
name: Update US 3M RS Table

on:
  schedule:
    - cron: '0 1 * * 1-5'      # 01:00 UTC Mon-Fri = 09:00 HKT
  workflow_dispatch:           # manual trigger for debugging + first seed

permissions:
  contents: write              # commit the CSV back to main

jobs:
  build-3m-table:
    runs-on: ubuntu-latest
    timeout-minutes: 60        # Fred6725 budget is 90 for 6800; ours is ~5900

    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v3
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

**Properties:**
- `uv sync --frozen` uses the existing lockfile → same curl_cffi/yfinance versions as local. No drift.
- `git diff --staged --quiet || git commit` → if the script failed earlier (no new CSV written), no-op commit. Workflow exits 0 with email containing the failed step.
- `GITHUB_TOKEN` (default) suffices for same-repo push to main. No PAT needed.
- Default GH cron concurrency is single-run → no race on commit.

### 3. Local fetcher — `us_rs_3m.build_3m_table` (rewritten)

Same signature, new internals. Call sites unchanged.

```python
_CLOUD_CSV_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/EricXue92/finvinz_to_tv/main/"
    "data/us_rs_3m/{date}.csv"
)
_FALLBACK_MAX_AGE_DAYS = 3  # mirrors rs_rating._FALLBACK_MAX_AGE_DAYS


def build_3m_table(
    output_dir: Path,
    today: date,
    rs_table_12m: dict[str, int] | None = None,  # kept for sig compat; unused
) -> pd.DataFrame | None:
    # 1. Local cache hit (same-day rerun) — short-circuit.
    cached = load_cache(today, output_dir)
    if cached is not None and not cached.empty:
        logger.info(f"[US RS 3M] Using cached table: {len(cached)} tickers")
        return cached

    # 2. Cloud URL: today → 1d → 2d → 3d stale.
    for delta in range(_FALLBACK_MAX_AGE_DAYS + 1):
        target_date = today - timedelta(days=delta)
        url = _CLOUD_CSV_URL_TEMPLATE.format(date=target_date.isoformat())
        table = _fetch_cloud_csv(url, timeout=30)
        if table is not None and not table.empty:
            if delta > 0:
                logger.warning(
                    f"[US RS 3M] Cloud CSV for {today} not available; "
                    f"using stale fallback from {target_date} ({delta} day(s) old)"
                )
            else:
                logger.info(f"[US RS 3M] Fetched cloud CSV: {len(table)} tickers")
                save_cache(table, today, output_dir)  # local mirror for rerun short-circuit
            return table

    # 3. All fetches failed — passthrough (kept-as-missing in filter_by_rs).
    logger.warning(
        "[US RS 3M] No cloud CSV within 3 days; 3M layer will passthrough "
        "(check https://github.com/EricXue92/finvinz_to_tv/actions)"
    )
    return None
```

**New helper `_fetch_cloud_csv`** (~15 lines), stdlib `urllib.request` (no new dependency, mirrors `rs_rating.py:_fetch_csv`):

```python
def _fetch_cloud_csv(url: str, timeout: int = 30) -> pd.DataFrame | None:
    """Fetch a 3M CSV from raw.githubusercontent. Returns None on 404,
    network error, or parse failure — never raises."""
    try:
        req = Request(url, headers={"User-Agent": "finviz_to_tv/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return pd.read_csv(resp, index_col="ticker")
    except HTTPError as e:
        if e.code == 404:
            return None  # today's not published yet — caller walks back
        logger.warning(f"[US RS 3M] HTTP {e.code} fetching {url}")
        return None
    except (URLError, pd.errors.ParserError, Exception) as e:
        logger.warning(f"[US RS 3M] Failed to fetch {url}: {type(e).__name__}: {e}")
        return None
```

**Dead code disposition:**
- `fetch_us_klines_yf`, `_fetch_spy_kline`, `_yf_download_with_retry` proxy, `_retry_sparse_in_batch` proxy: **stay** in `us_rs_3m.py` — the cloud script imports them. The A/B/C/D defensive layers stay in place to protect the cloud script if a GH runner has a bad-IP day.
- Old `build_3m_table` D-guard (coverage < 50% → None): **removed locally**, **moved to** `scripts/compute_us_rs_3m_cloud.py` as `sys.exit(1)`.

**Local cache mirror policy:**
- Mirror **only today's** file (delta == 0) to `output/state/rs_rating_3m_<date>.csv` — this lets same-day reruns short-circuit at step 1, and the existing `cleanup.py` retention rule already covers this filename.
- **Don't mirror stale fallbacks** — they're already on disk if they were ever today's-file; if not, writing today's path with stale data would mask the staleness on the next run.

### 4. `data/us_rs_3m/README.md`

Brief (10-15 lines): what the directory is, how to interpret a row, how the
files are produced (link to workflow), where it gets consumed in the local
code, retention policy. No prose dump — just enough for a future reader to
trace the data flow without digging through commits.

## Tests

| Layer | Test approach |
|-------|---------------|
| `_fetch_cloud_csv` | Unit test with `monkeypatch` on `urllib.request.urlopen`: 200/parse-success → DataFrame, 404 → None, network error → None, malformed CSV → None. Pattern: `tests/test_rs_rating_fallback.py`. |
| `build_3m_table` fetcher | Monkeypatch `_fetch_cloud_csv` to simulate: (a) today's table → returns table + writes local cache; (b) None for today, table for yesterday → returns yesterday's with stale warning, no local cache write; (c) None for all 4 days → returns None with passthrough warning. |
| Same-day cache short-circuit | `save_cache(table, today)` first, then call `build_3m_table` with a monkeypatched `_fetch_cloud_csv` that calls `pytest.fail()` — verifies the local cache is consulted before any HTTP fetch. |
| Cloud script | Smoke test in `tests/test_compute_us_rs_3m_cloud.py`: monkeypatch `fetch_us_klines_yf`, `_fetch_spy_kline`, and `rs_rating.fetch_rs_table` to return tiny synthetic data (3 tickers); run `main()`; assert CSV exists at `data/us_rs_3m/<today>.csv` with correct schema; assert coverage-guard exits non-zero when synthetic data covers < 50% of universe. **No live network calls in any test.** |
| Workflow YAML | First manual `workflow_dispatch` is the integration test. If it produces a valid CSV + commit cleanly, no further automation needed. |

## Migration plan

Three independently reviewable commits:

### Commit 1 — Cloud infrastructure (no runtime impact on existing code)
- `data/us_rs_3m/README.md` + `.gitkeep`
- `scripts/compute_us_rs_3m_cloud.py` + unit tests
- `.github/workflows/update_us_rs_3m.yml`
- Manually trigger via `workflow_dispatch` after merge to seed the first CSV
- **Verification:** today's file appears in `data/us_rs_3m/` on main; schema correct

### Commit 2 — Local fetcher swap
- `us_rs_3m.py`: add constants + `_fetch_cloud_csv`, replace `build_3m_table` body
- Rewrite three tests in `tests/test_us_rs_3m.py`:
  - `test_build_3m_table_orchestration` → now tests the fetcher path
  - `test_build_3m_table_refuses_to_cache_sparse_coverage` → **deleted** (D-guard moved to cloud script; new test for that lives in `test_compute_us_rs_3m_cloud.py`)
  - `test_build_3m_table_uses_cache` → unchanged in intent (same-day cache short-circuit still applies)
- **Verification:** `uv run main.py --mode us-eod` shows no yfinance batch lines in log; instead one `[US RS 3M] Fetched cloud CSV: N tickers` line

### Commit 3 — Documentation
- `CLAUDE.md`: rewrite the "US 3M layer" section under "IBD Relative Strength Rating" to describe the cloud-driven flow + workflow
- `README.md`: brief mention in the architecture section if a 3M reference exists

**Rollback safety:** between commits 1 and 2, the old local compute path is
still active. If the first `workflow_dispatch` fails, commit 2 is paused
indefinitely — local pipeline keeps working with the throttle-prone but
functional local compute. Zero-downtime migration.

## Scope summary

- **New code:** ~150 lines (cloud script ~50, fetcher ~40, workflow ~30, tests ~30)
- **Removed code:** ~30 lines (old compute orchestration in `build_3m_table`)
- **Files:** 4 new (`data/us_rs_3m/README.md`, `scripts/compute_us_rs_3m_cloud.py`, `tests/test_compute_us_rs_3m_cloud.py`, `.github/workflows/update_us_rs_3m.yml`); 2 modified (`us_rs_3m.py`, `tests/test_us_rs_3m.py`); 2 doc-only (`CLAUDE.md`, optionally `README.md`)
- **No new runtime dependencies.** `curl_cffi`, `yfinance`, `pandas`, `requests` already in `pyproject.toml`.

## Open questions

None — all decisions resolved during brainstorm.

## References

- Fred6725 workflow: https://github.com/Fred6725/relative-strength/blob/main/.github/workflows/output.yml
- Fred6725 12M CSV publication target: https://github.com/Fred6725/rs-log/blob/main/output/rs_stocks.csv
- Existing local 12M fetcher (pattern we mirror): `rs_rating.py:_fetch_csv` and `rs_rating.fetch_rs_table`
- Today's defensive-layer commit: `821ea5d` (`fix(us_rs_3m): harden yfinance fetch against Yahoo rate-limiting`)
