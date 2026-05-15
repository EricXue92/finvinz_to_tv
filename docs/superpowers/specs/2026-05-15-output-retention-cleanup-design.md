# Output Retention Cleanup — Design

**Date:** 2026-05-15
**Status:** Draft

## Goal

After each scan run completes, automatically delete dated output artifacts
older than yesterday so only today + yesterday remain on disk. Reduces
clutter in `output/TV/`, `output/Webull/`, `output/Reports/`, and
`output/state/` without touching persistent cross-day state.

## Scope

### Files cleaned (keep today + yesterday only, `keep_days=2`)

| Directory | Glob | Notes |
|---|---|---|
| `output/TV/US/` | `<YYYY_MM_DD>_*.txt` | Daily TV watchlists (US) |
| `output/TV/HK/` | `<YYYY_MM_DD>_*.txt` | Daily TV watchlists (HK) |
| `output/Webull/US/` | `<YYYY_MM_DD>_*.txt` | Webull mirrors (US) |
| `output/Webull/HK/` | `<YYYY_MM_DD>_*.txt` | Webull mirrors (HK) |
| `output/Reports/` | `<YYYY_MM_DD>_{us,hk}.{md,html}` | Daily CANSLIM reports |
| `output/state/` | `morning_gap_seen_{pre,post}_<YYYY_MM_DD>.txt` | Per-day MG dedup |
| `output/state/` | `hk_morning_gap_seen_post_<YYYY_MM_DD>.txt` | Per-day HK MG dedup |
| `output/state/` | `hk_rs_rating_<YYYY-MM-DD>.csv` | HK RS daily cache |

### Files cleaned with a longer retention (`keep_days=4`)

| Directory | Glob | Reason |
|---|---|---|
| `output/state/` | `rs_rating_<YYYY_MM_DD>.csv` | `rs_rating.py` falls back to caches up to 3 days old when the GitHub fetch fails (`_FALLBACK_MAX_AGE_DAYS = 3`). Keeping 4 days preserves the documented Mon-holiday + Tue-DNS-failure scenario. |

### Files NEVER deleted (explicit allow-list of survivors)

- `output/state/eod_seen_US.txt`, `eod_seen_HK.txt`, `eod_seen_IPO.txt`,
  `eod_seen_HKIPO.txt` — cross-day master dedup, monotonic growth. Losing
  these would resurface every previously-seen ticker on the next run.
- `output/state/ntfy_last_seen.txt` — ntfy subscriber position.
- `output/state/edgar_cache/` (directory) — SEC EDGAR responses with their
  own 7-day TTL; pruning is the cache layer's job, not ours.
- `output/launchd_*.log` — rotated daily by the wrapper scripts themselves.
- Anything in `output/` that doesn't match an explicit glob above.

The cleanup is **glob-driven, not directory-sweep-driven** — we only delete
paths that match a known dated pattern. New filename schemes will be
ignored until added to the glob list, which is the safe failure mode.

## Date parsing

Two filename date formats exist in the codebase:

- `YYYY_MM_DD` (underscores) — used by all TV/Webull/Reports outputs and
  most state files (`morning_gap_seen_*`, `rs_rating_*`).
- `YYYY-MM-DD` (dashes) — used by `hk_rs_rating_*.csv` only.

The cleanup function takes a compiled regex per glob entry and parses the
captured date via `datetime.strptime`. Files where the regex doesn't match
are skipped (not deleted). Files where the date parses but is invalid
(e.g. `2026_02_30`) are skipped + logged as `warning` — we don't want a
malformed filename to mask a real bug.

## Cutoff math

```
today_hkt = date.today()  # HKT — the project already uses this everywhere
cutoff    = today_hkt - timedelta(days=keep_days - 1)
# keep file if file_date >= cutoff, else delete
```

For `keep_days=2` and `today=2026-05-15`: cutoff = `2026-05-14`. Files
dated `2026-05-13` and earlier get deleted; `2026-05-14` and `2026-05-15`
survive.

Weekends/holidays don't need special handling — if no scan ran on a date,
no file exists for it; cleanup only acts on files that physically exist.

## Module layout

New file `cleanup.py` at project root, exposes:

```python
def cleanup_old_outputs(output_dir: Path, today: date) -> None:
    """Delete dated output artifacts older than the retention window.

    Soft-fails: any IOError / OSError is caught and logged as warning,
    never re-raised. The .txt scan artifacts are the primary product;
    cleanup is housekeeping.
    """
```

Internally it iterates a constant table of `(glob, date_regex, date_fmt,
keep_days)` tuples. One function, ~60 lines, no class needed.

## Integration points

Call `cleanup_old_outputs(output_dir, date.today())` immediately before
each `logger.info("Done.")` success-return in `main.py`:

- Line ~1435: `hk-eod` mode
- Line ~1718: `eod` / `us-eod` mode
- Line ~1764: `morning-gap` mode (in-window success path only)
- Line ~1813: `hk-morning-gap` mode (in-window success path only)

**Skipped paths** (intentional):
- Outside-window early-exits (lines 1739, 1788) — no files were written;
  running cleanup is harmless but pointless. Skip for clarity.
- `--mode report` (line 1413) — report mode reads files; the EOD modes
  that triggered it already ran cleanup.
- Hard config errors (line 1377, 1411) — bail before any work.

Wrap each call in `try / except Exception as e: logger.warning(...)` at
the call site, matching the existing soft-fail pattern for Futu sync and
report generation.

## Failure modes

| Scenario | Behavior |
|---|---|
| Permission denied on a file | Log warning with file path, continue with next file |
| File deleted between `glob` and `unlink` (race) | Catch `FileNotFoundError`, continue |
| Date regex doesn't match a filename | Skip (do not delete) |
| Date parses to invalid date | Log warning, skip |
| `output/state/` doesn't exist yet | `glob` returns empty list, no-op |
| Cleanup raises unexpectedly | Outer `try/except` in `main.py` swallows it, logs warning, run still exits 0 |

## Testing

Add `tests/test_cleanup.py` with `pytest` + `tmp_path`:

1. Create a synthetic `output/` tree with files spanning 5 days for each
   covered directory (TV/US, TV/HK, Webull/US, Webull/HK, Reports, state).
2. Include sentinels: `eod_seen_US.txt`, `ntfy_last_seen.txt`,
   `edgar_cache/some.json`, `launchd_US.log`, a non-dated rogue file
   `foo.txt`. Verify all survive.
3. Run `cleanup_old_outputs(tmp_path, date(2026, 5, 15))`.
4. Assert: only `2026_05_14_*` and `2026_05_15_*` files remain for the
   2-day retention table; only `rs_rating_2026_05_12.csv` ... `_15.csv`
   remain (4-day window for that file only).
5. Edge case: malformed filename `2026_02_30_Foo.txt` → log warning, file
   preserved (not deleted).
6. Edge case: empty state/ dir → no crash.

These are pure-logic helpers (no network, no Futu, no yfinance) so they
fit the existing `tests/` conventions.

## Out of scope

- Configurable retention via `config.toml` — YAGNI; 2 days is the user's
  explicit choice. If we ever need different retention per environment,
  add `[settings] output_retention_days` then.
- Cleanup of `edgar_cache/*.json` by mtime — EDGAR's TTL is its own
  problem and is already handled by the report module.
- Compression / archival of old files — user wants them gone, not stored.
- Cleanup at run start (vs run end) — running at end keeps the most-recent
  artifacts on disk longest if a future run fails before reaching its
  cleanup call. Same net retention, slightly safer.
