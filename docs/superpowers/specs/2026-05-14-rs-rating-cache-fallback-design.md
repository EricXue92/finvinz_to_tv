# RS Rating Cache Fallback + Earlier pmset Wake

**Date:** 2026-05-14
**Trigger:** On 2026-05-14 the 10:00 HKT US EOD run hit DNS resolution failures
on all 3 retry attempts (`urlopen error [Errno 8] nodename nor servname
provided, or not known`, spread across ~40s starting at 10:04:53). The Mac
had just woken from a 9:59 `pmset` trigger and the WiFi/DNS stack wasn't
ready. With the IBD RS table unavailable, every long-side group fell back
to the configured no-op (CLAUDE.md mandates soft-fail) and Leaders.txt
admitted low-RS tickers like BRKR that the RS ≥ 90 gate would normally
have dropped.

## Goals

1. Reduce the chance of repeat DNS-on-wake failures by giving the network
   stack more headroom before the launchd job fires.
2. When the RS fetch DOES fail despite that, degrade to the most recent
   *local* cache instead of skipping the gate outright — but only if the
   fallback is fresh enough that the percentiles still meaningfully reflect
   today's market.

## Non-goals

- Not changing retry count/intervals (`(10s, 30s)` + initial = 3 attempts
  across ~40s). Once DNS is up the existing schedule works fine; the
  fix is to make sure DNS is up by 10:00.
- Not distinguishing DNS errors from other network errors. The fallback
  applies to any post-retry failure — the operator doesn't care *why* the
  fetch failed, only that today's data is unavailable.
- Not touching `hk_rs.py`. HK RS is computed in-process from yfinance
  k-lines, no GitHub dependency.

## Design

### Part 1: pmset wake 9:59 → 9:57

```bash
sudo pmset repeat wakepoweron TWRFS 9:57:00
```

User-executed (requires sudo). The launchd plist still fires at 10:00 HKT;
this just buys 3 minutes for macOS to bring WiFi + DNS up before the
finviz-to-tv binary starts. No code/plist change.

### Part 2: 3-day local cache fallback in `rs_rating.py`

In `fetch_rs_table`, after the existing 3-attempt retry loop fails (the
point where today's code logs "Fetch failed after N attempts" and returns
`None`), insert a fallback step:

1. Glob `output_dir / "state" / "rs_rating_*.csv"`.
2. Exclude today's cache path itself (it's the file we just failed to
   populate; if it existed and was readable we'd already be past the
   fetch block via the existing same-day cache short-circuit).
3. Parse the date from each filename. Convention is
   `rs_rating_YYYY_MM_DD.csv` (underscores, matches the `today` string
   passed by callers). Skip files whose stem doesn't parse.
4. Pick the file with the **smallest absolute day delta** from today.
   Tie-break by preferring older over newer (defensive — a future-dated
   file would indicate clock skew).
5. If delta `> 3` days, give up: log
   `[RS Rating] No fallback within 3 days; RS filter will be skipped`,
   return `None`.
6. Otherwise read it, parse it through the existing csv.DictReader path,
   and log
   `[RS Rating] Using stale fallback cache: rs_rating_<date>.csv (N day(s) old)`.

**Why no copy-to-today's-path:** if the operator manually re-runs the EOD
later in the day after the network recovers, we want the second attempt
to hit GitHub for fresh data, not silently re-read the stale fallback.
The fallback is per-run, not sticky.

**Why 3 days:** US EOD runs Tue–Sat. A Monday US holiday + Tuesday DNS
failure is the worst realistic gap a single fallback needs to cover (≤2
days). 3 gives one day of safety margin. Beyond that, RS percentiles
have drifted enough that "no gate" is more honest than "wrong gate".

### Part 3: Unit test

Add `tests/test_rs_rating_fallback.py`:

- Use `tmp_path` to construct `output_dir/state/` with three fixture
  CSVs: 2 days old (valid percentile rows), 5 days old, and an
  unparseable stem.
- Monkey-patch `urllib.request.urlopen` to raise `URLError` on every
  call (simulates today's DNS failure).
- Call `fetch_rs_table(tmp_path, today_str)`:
  - Asserts the returned dict matches the 2-day-old fixture's contents
    (i.e. the 2-day file was chosen, the 5-day file was rejected, the
    unparseable file was skipped).
- Add a second case where only the 5-day file exists → asserts `None`
  is returned and the "no fallback within 3 days" log fires.

Today's `tests/` directory has no RS tests, so this also fills a small
coverage gap.

### Part 4: CLAUDE.md update

In the `## IBD Relative Strength Rating > US` section, the "Failure
mode" bullet currently reads:

> If the fetch fails (network, GitHub outage, schema change) the loaded
> table is None and every filter_by_rs call becomes a one-line warning
> + passthrough.

Append one sentence:

> Before giving up the fetcher first looks for the most recent
> `output/state/rs_rating_*.csv` within 3 days of today and uses it if
> present (logged as a stale-fallback warning); only if no acceptable
> cache exists does the table go to None and the gate degrade to a
> no-op.

## Files touched

- `rs_rating.py` — add ~25 LOC fallback block after retry loop
- `tests/test_rs_rating_fallback.py` — new, ~60 LOC
- `CLAUDE.md` — one sentence appended to the US RS Failure-mode line
- `docs/superpowers/specs/2026-05-14-rs-rating-cache-fallback-design.md`
  — this file

No changes to `main.py`, plists, or wrapper scripts. The pmset change is
operator action only.

## Verification

After the change:

1. `uv run pytest tests/test_rs_rating_fallback.py -v` passes.
2. Delete `output/state/rs_rating_2026_05_14.csv`, set
   `RS_CSV_URL` to an invalid URL in a one-off sandbox, run
   `uv run main.py --mode us-eod` — confirm Leaders log shows
   `RS table loaded ... (stale fallback)` and Leaders.txt contains
   only RS ≥ 90 tickers from the fallback file.
3. Verify no regression on the happy path: today's `rs_rating_2026_05_14.csv`
   exists in state/, normal run still uses it via the cached-CSV
   short-circuit at the top of `fetch_rs_table`.
