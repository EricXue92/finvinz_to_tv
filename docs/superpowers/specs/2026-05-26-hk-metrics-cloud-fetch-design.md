# HK Long-side Metrics Frame — Cloud Fetch

**Date:** 2026-05-26
**Trigger:** The 20:00 HKT `hk-eod` run consistently produces empty HK
long-side `.txt` files. Investigation of the 2026-05-26 run log showed three
compounding causes, the first of which is a data-completeness defect:

1. **Local yfinance throttling.** The long-side metrics k-line fetch
   (`hk_eod.py:874`, `fetch_hk_klines_yf` over the ~2,400-ticker universe)
   gets rate-limited: `2427 → 1610` usable (`sparse=809`), so candidate
   *discovery* runs on only ~66% of the universe. (The RS *table* was already
   moved off-host on 2026-05-26, but the metrics frame is still fetched
   locally.)
2. Tight baseline (`price≥HK$20`, `$vol≥HK$100M` are the binding gates →
   only 13/1610 pass `baseline-AND`).
3. Cross-day master dedup suppresses the few survivors (first-sighting only).

This spec addresses **(1) only** — the data-completeness defect. (2) and (3)
are deliberate strategy/design choices and are explicitly out of scope for
this round (confirmed with the operator).

## Goals

1. Eliminate local yfinance throttling from HK long-side candidate discovery
   so the metrics frame covers the **full ~2,400-ticker universe** instead of
   the throttled ~1,600.
2. Reuse the k-lines the RS cloud workflow **already fetches** — zero extra
   GitHub fetch cost, no second throttle-prone fetch.
3. Preserve the project's soft-fail philosophy: a cloud-CSV miss must not empty
   HK output; fall back to the existing local fetch.

## Non-goals

- **Not** loosening the baseline (`price` / `$vol` / etc.) — out of scope.
- **Not** changing cross-day dedup or first-sighting behavior.
- **Not** moving market-cap sourcing off Futu. Caps stay local (one fast,
  reliable Futu snapshot per run); only the k-line-derived metrics move.
- **Not** touching US RS, HK RS, Shorts, or morning-gap paths.

## Design (Approach 1 of 3 considered)

The two rejected alternatives: **(2)** publish raw k-lines and compute metrics
locally — rejected, the ~2,400 × ~500-row OHLCV artifact is too heavy for git /
`raw.githubusercontent`; **(3)** a separate dedicated metrics workflow —
rejected, it duplicates the ~2,400-ticker fetch on GitHub and adds a second
failure surface. Approach 1 reuses the in-memory k-lines the RS run already has.

### Part 1: Cloud publishes a metrics CSV — `scripts/compute_hk_rs_cloud.py`

After the existing RS compute (where `klines` is already in memory, ~line 116),
add:

1. Import `build_metrics_frame` from `hk_eod`.
2. `metrics = build_metrics_frame(klines, market_caps={})` — `market_cap` comes
   out all-NaN; that column is **not published** (filled locally from Futu).
3. Write `data/hk_metrics/<today>.csv`, indexed by `code` (Futu format
   `HK.00700`), **dropping**:
   - `market_cap` (NaN — local-only).
   - `above_sma50` / `above_sma200` (bool columns — dropped to avoid
     bool↔CSV serialization fragility; recomputed locally from
     `last_price`/`sma50`/`sma200`).
   Published columns: `last_price, prev_close, gap_pct, rvol, avg_vol_20d,
   avg_dollar_vol_20d, adr_pct, sma50, sma200, perf_4w, perf_13w, perf_26w,
   perf_ytd, perf_52w, consecutive_up_days`.
4. Prune `data/hk_metrics/*.csv` older than 14 days (reuse the existing
   `_prune_old_files` logic, pointed at the new dir).

No new coverage guard: the metrics publish rides on the same `klines` as RS, so
if the existing `<50%` RS coverage guard fails the workflow, **neither** CSV is
committed and the local run falls back.

### Part 2: Workflow — `.github/workflows/update_hk_rs.yml`

Add `data/hk_metrics/` to the git-add/commit step so the new CSV is committed
alongside `data/hk_rs/`. No cron/schedule change (same 11:00 UTC / 19:00 HKT
run). Confirm `data/hk_metrics/` has a `.gitkeep` so the dir exists on a fresh
clone (mirror `data/hk_rs/`).

### Part 3: New module — `hk_metrics.py`

A thin HTTP fetcher in the same spirit as `us_rs_3m.build_3m_table` /
`hk_rs.build_hk_rs_tables`, but **with no stale walk-back** (see below):

```python
def build_hk_metrics_cloud(output_dir: Path, today: date) -> pd.DataFrame | None
```

Resolution order:
1. `output/state/hk_metrics_<date>.csv` same-day cache → return.
2. Fetch **today's** cloud CSV via
   `raw.githubusercontent.com/EricXue92/finvinz_to_tv/main/data/hk_metrics/<date>.csv`;
   on success, mirror to `output/state/hk_metrics_<date>.csv` and return.
3. Anything else → `None` (caller falls back to the local live fetch).

**Why no stale (≤3-day) walk-back — deliberately unlike the RS fetchers.** RS
*percentiles* drift slowly, so a 2–3-day-old RS table is "more honest than no
gate." A metrics frame is the opposite: `gap_pct`, `rvol`, and
`consecutive_up_days` are point-in-time signals — a 3-day-old `gap_pct` is
simply wrong and would surface phantom gap-ups. On a cloud miss the **local
live fetch** (Part 4) gives *correct today's* prices at partial (throttled)
coverage, which is strictly better than a complete-but-stale frame. So: today's
CSV or bust; otherwise fall through to the local fetch.

After loading the frame, **recompute the two bool columns** the downstream
filters expect:
`above_sma50 = (last_price > sma50) & sma50.notna()` (and `above_sma200`),
stored as Python `bool` (object dtype) to match `build_metrics_frame`'s
contract.

`build_metrics_frame` itself stays in `hk_eod.py` (it is the shared pure
reducer, imported by the cloud script and used on the local-fallback path).
The new fetcher goes in its own module to keep the already-large `hk_eod.py`
from growing and to isolate/unit-test the new responsibility — same split as
`us_rs_3m.py`.

### Part 4: Integration — `run_hk_eod` (`hk_eod.py`, ~864–934)

Replace the unconditional local fetch with a cloud-first branch:

```python
metrics_cloud = build_hk_metrics_cloud(output_dir, today_d)
if metrics_cloud is not None:
    # Full-universe coverage from the cloud. Join Futu caps locally.
    tv_codes = [_to_tv(c) for c in metrics_cloud.index]
    caps_by_tv = get_market_caps_futu(tv_codes, market="HK", host=host, port=port) or {}
    caps = {f"HK.{tv.replace('HKEX:', '').zfill(5)}": v for tv, v in caps_by_tv.items()}
    metrics_cloud["market_cap"] = metrics_cloud.index.map(lambda c: caps.get(c, float("nan")))
    metrics = metrics_cloud
    logger.info(f"[HK Longs] Using cloud metrics: {len(metrics)} tickers (local k-line fetch skipped)")
else:
    # Fallback: existing path — fetch_hk_klines_yf + pre-20:00 trim + build_metrics_frame.
    logger.warning("[HK Longs] Cloud metrics unavailable; falling back to local yfinance fetch")
    <existing lines 873–934 unchanged>
```

**Data-day / trim semantics.** Cloud metrics are always computed off settled
close (the workflow runs 19:00 HKT, after the 16:00 close). The production
20:00 local run wants today's settled close → matches. The pre-20:00
"trim today's incomplete bar → use yesterday's close" rule therefore lives
**only on the fallback branch**; when cloud metrics are used they reflect the
cloud-run date's settled close. This is an acceptable, deliberate refinement:
the cloud-metrics path is the production 20:00 slot; pre-20:00 ad-hoc/weekend
runs that need the trim will get it via the fallback (today's cloud CSV won't
exist yet before 19:00 HKT, so they fall through to the local fetch anyway).

The conditional-RS HSI-snapshot trigger and everything downstream of `metrics`
(RS gate, strategy filters, dedup, write) are **unchanged**.

### Part 5: Cleanup — `cleanup.py`

Add `hk_metrics_<date>.csv` to the dated-state cleanup glob (cleaned like
`hk_rs_rating_*`). The `eod_seen_*` masters / `edgar_cache/` / logs policy is
unchanged.

### Part 6: Tests

- **`tests/test_hk_metrics_fetcher.py`** (new, mirrors `test_hk_rs_fetcher.py`):
  same-day cache hit; today fetch + mirror-to-state; **today's CSV missing →
  `None` (no stale walk-back — an older dated CSV must NOT be used)**; `above_sma*`
  recompute correctness (incl. NaN sma → `False`).
- **`tests/test_compute_hk_rs_cloud.py`** (extend): assert the metrics CSV is
  written with the expected columns and **without** `market_cap` /
  `above_sma50` / `above_sma200`.
- **`run_hk_eod` integration** (in `tests/test_hk_eod.py` or a new case):
  cloud-hit path skips `fetch_hk_klines_yf` and joins caps; cloud-miss path
  falls back to the local fetch. Use monkeypatch/stubs (no network).

### Part 7: CLAUDE.md

The condensed CLAUDE.md currently says, under RS gating, that "the local HK
20:00 run still fetches ~2,400 k-lines for the *metrics* frame, so discovery is
still locally throttled." After this change that sentence is stale — update the
RS-gating / HK note to reflect that the metrics frame is now cloud-fetched
(`data/hk_metrics/`) with a local-fetch fallback, so discovery is no longer
locally throttled on the happy path.

## Files touched

- `scripts/compute_hk_rs_cloud.py` — +metrics compute & publish, +prune dir
- `.github/workflows/update_hk_rs.yml` — commit `data/hk_metrics/`
- `data/hk_metrics/.gitkeep` — new dir marker
- `hk_metrics.py` — new thin fetcher (~70 LOC)
- `hk_eod.py` — cloud-first branch in `run_hk_eod` (~15 LOC + fallback retained)
- `cleanup.py` — add `hk_metrics_<date>.csv` to dated-state glob
- `tests/test_hk_metrics_fetcher.py` — new
- `tests/test_compute_hk_rs_cloud.py` — extend
- `tests/test_hk_eod.py` — cloud-hit/miss integration case
- `CLAUDE.md` — refresh the "metrics frame still locally throttled" note
- `docs/superpowers/specs/2026-05-26-hk-metrics-cloud-fetch-design.md` — this file

## Verification

1. `uv run pytest tests/test_hk_metrics_fetcher.py tests/test_compute_hk_rs_cloud.py tests/test_hk_eod.py -v` passes.
2. Run `scripts/compute_hk_rs_cloud.py` locally (or inspect a CI run): confirm
   `data/hk_metrics/<date>.csv` is written with the expected columns and full
   coverage matching the RS row count.
3. With today's `data/hk_metrics/<date>.csv` present, `uv run main.py --mode
   hk-eod` logs `Using cloud metrics: N tickers (local k-line fetch skipped)`
   with N ≈ full universe (~2,400), and the baseline funnel `n=` is no longer
   ~1,600.
4. Fallback check: point the metrics URL at an invalid path (or delete the
   state cache and block network) → run logs `Cloud metrics unavailable;
   falling back to local yfinance fetch` and the run still completes.
5. No regression: HK output files are still first-sighting-only (dedup
   unchanged); the only observable change is a larger discovery universe.
