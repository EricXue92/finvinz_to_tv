# RS scan: skip both within-day priority dedup and cross-day master dedup

**Date:** 2026-05-16
**Scope:** US RS (`main.py`) and HK RS (`hk_eod.py`)

## Problem

The conditional weak-market RS scan currently shares the long-side
cross-day master (`eod_seen_US.txt` / `eod_seen_HK.txt`) and the
within-day Longs > Leaders > RS priority dedup with the other long-side
groups. That treatment makes sense for "first sighting" groups (Longs,
Leaders, IPO) — once a ticker has shown a setup we don't want to repeat
the same alert on subsequent days.

RS is semantically different. It fires only when the market itself is
weak (SPY+QQQ both down >1.5% for US; HSI down >1.5% for HK). The whole
point of the scan is to surface names that hold up *while the index is
dumping*. A ticker that landed in Leaders three weeks ago and is still
holding up on a weak-market day is exactly the signal RS is meant to
produce — but the current dedup hides it.

Same logic applies within a single run: if AAPL fires Leaders today AND
also clears the RS scan because the market collapsed, that's two
independent signals, not redundant noise.

## Decision

RS becomes a re-detectable, market-condition-triggered scan, semantically
in the same bucket as Shorts (different trigger — index-level vs
per-ticker setup — but the same "re-detection is meaningful" property).

- **No within-day priority dedup against Longs/Leaders.** RS keeps every
  ticker that clears its own filters.
- **No cross-day master dedup.** RS does not consult or update
  `eod_seen_US.txt` / `eod_seen_HK.txt`.
- Other groups' behavior is unchanged. Longs > Leaders within-day
  priority still applies (Leaders still subtracts Longs). Cross-day
  master dedup still applies to Longs/Leaders/IPO on both markets.

## Changes

### `main.py` (US RS)

1. **Within-day dedup (lines ~1630-1639)**
   - Keep: `leaders_tickers -= longs_tickers`
   - Remove: `rs_tickers -= longs_tickers | leaders_tickers`
   - Logging: drop the "removed N from RS" portion of the
     `[Dedup] Priority Longs > Leaders > RS` line. Leave the Leaders
     count.

2. **RS write block (lines ~1665-1673)**
   - Remove the `_dedup_seen("[RS]", sorted_rs, us_seen, us_seen_path)`
     call.
   - `sorted_rs = sorted(rs_tickers)` flows straight into
     `write_watchlist` / `_write_webull` / `_futu_sync`.
   - The `us_seen` set is not mutated by RS as a side effect (because
     `_dedup_seen` is no longer called). This is the intended behavior:
     an RS hit does NOT suppress a future Longs/Leaders detection of the
     same ticker.

### `hk_eod.py` (HK RS)

1. **Within-day priority dedup (around line 887)**
   - Call `dedup_by_priority(raw, priority=["EarningsGap", "HighVolume",
     "GapUp", "Leaders"])` (RS excluded).
   - After the call: `dedup["RS"] = raw["RS"]` — RS goes in untouched.
   - Update the dedup log line so RS shows `pre→pre` (no change) and the
     other 4 strategies still show their dedup result.

2. **Cross-day master dedup loop (lines ~896-902)**
   - Inside the `for name, codes_list in dedup.items()` loop, branch on
     `name == "RS"`: build the TV-format sorted list and assign to
     `final["RS"]` directly, **without** calling `dedup_seen` and
     **without** mutating the shared `seen` set. Other strategies
     continue to call `dedup_seen` as before.

### Documentation

- `CLAUDE.md`: in the "Cross-day master dedup" paragraph, move RS from
  the "applied to" list to the "Excluded" list alongside Shorts /
  HK Shorts / Morning Gap. Add a short rationale: "RS fires only on
  weak-market days; re-detecting a previously-collected ticker that's
  still holding up is the entire point of the scan."
- The "Cross-group dedup (Longs/Leaders/RS)" bullet needs an edit:
  within-day priority is now `Longs > Leaders` only; RS is independent.

### Unchanged

- `[rs]` and `[hk_rs]` Futu groups stay in `[futu] append_only_groups`.
  The Futu group keeps growing monotonically. `sync_to_futu` is
  diff-based so re-adding a ticker already in the group is a no-op; a
  ticker that already exists in another group (e.g. EarningsGap) but
  has not yet been added to RS will be ADDed to RS, which is desired.
- `eod_seen_IPO.txt` and `eod_seen_HKIPO.txt` are untouched — RS never
  consulted them.
- US Shorts / HK Shorts behavior unchanged (already excluded).
- Morning Gap behavior unchanged (uses its own per-day per-phase seen
  files).

## Testing

- **New unit test** in `tests/test_hk_eod.py`: feed `dedup_by_priority`
  a raw dict where the same code appears in both Leaders and RS; call
  the function with `priority=["EarningsGap", "HighVolume", "GapUp",
  "Leaders"]` and verify that (a) Leaders contains the code, and
  (b) RS is not in the returned dict at all (caller is responsible for
  splicing it back in). Also test the empty-RS case.
- **Manual verification:** run `uv run main.py --mode us-eod` after the
  change. Confirm in the logs:
  - The `[Dedup] Priority Longs > Leaders > RS` line no longer mentions
    `removed N from RS`.
  - The `[RS]` block no longer logs `cross-day dedup: N already in
    master`.
  - The dated `<date>_RS.txt` and Webull mirror contain every RS
    survivor (including tickers also in `<date>_Leaders.txt`).
- **Existing tests:** `uv run pytest tests/ -v` should pass unchanged.

## Risks / Non-goals

- This change can make RS group on Futu grow faster than before (no
  more dedup against Longs/Leaders means more frequent additions).
  Acceptable — append-only behavior is intentional and the user can
  prune the group manually in the Futu client when it gets crowded.
- This is not an attempt to redesign the broader "what should re-detect
  and what shouldn't" question. It applies the existing
  Shorts-style exclusion to RS, nothing more.
- Not changing any RS *filter* (the RS percentile gate, the index-drop
  threshold, etc.). Filter behavior is identical.
