# Morning-Gap Futu-Snapshot Discovery — Design

**Date:** 2026-05-01
**Status:** Draft (awaiting user review)

## Problem

The morning-gap scan uses `signal = "ta_topgainers"` on the Finviz screener as
its pre-market candidate source (`config.toml:161`, `main.py:630-636`). The code
comment claims `ta_topgainers` "reflects current change and surfaces today's
pre-market movers" — empirically it does not. On 2026-05-01 the screener
returned 67 tickers, all with `Change = 0.00%`, ranked by recent regular-session
performance ("Perf Week"). TWLO, gapping +19.5% in pre-market on earnings
(prev close $148.06 → pre-market $177), was absent from the candidate set and
therefore never reached any downstream filter, even though it would have passed
every one (cap, price, avg vol, SMA50/SMA200, dollar vol, ADR%, gap%).

The post-open path (`offsets +10 … +30`) using `ta_gap_u5` works correctly —
Finviz's Gap field is computed off today's regular-session open after 09:30 ET.
But for symmetry and a single discovery code path we replace both.

## Goal

Replace Finviz screener-based discovery for the morning-gap scan with a Futu
snapshot-based bulk scan over a curated US universe. All downstream filters
remain.

## Scope

In scope:
- `[morning_gap]` config section.
- `run_morning_gap()` Phase 1 in `main.py`.
- New helper in `futu_sync.py`.

Out of scope:
- EOD pipeline (Longs / Leaders / RS / Shorts / HK Shorts).
- HK markets (no morning_gap scan exists for HK).
- Universe caching / persistence.

## Architecture

```
[Futu basicinfo (US, STOCK)]   <- one call, ~12.8k rows
        │
        │  filter exchange_type ∈ {US_NASDAQ, US_NYSE, US_AMEX}
        │  drop suspension=True, delisting=True
        ▼
[~5–6k US codes]
        │
        │  get_market_snapshot in batches of 400 (≈15 calls)
        ▼
[Per-ticker snapshot fields]
        │
        │  filter:
        │    total_market_val ≥ min_market_cap   (default 300M USD)
        │    last_price       ≥ min_price        (default 10 USD)
        │    if pre-market:  pre_volume > 0 AND pre_change_rate ≥ min_gap_pct
        │    if post-open:   change_rate ≥ min_gap_pct
        ▼
[~10–80 candidates] ──▶ enters existing run_morning_gap pipeline:
                         yfinance daily (1y) → SMA50/SMA200 trend gate
                         → 20d avg vol ≥ 500K → dollar vol ≥ $100M
                         → ADR% ≥ 4%
                         → (post-open only) cumulative RTH vol ≥ 20d avg
```

## New component — `futu_sync.py::discover_morning_gap_candidates`

```python
def discover_morning_gap_candidates(
    min_gap_pct: float,
    min_market_cap: float,    # USD
    min_price: float,         # USD
    pre_market: bool,         # True ↔ offset < 0
    exchanges: list[str],     # e.g. ["US_NASDAQ", "US_NYSE", "US_AMEX"]
    host: str = "127.0.0.1",
    port: int = 11111,
) -> list[str] | None:
```

Behavior:
1. TCP probe via `_opend_reachable`. Open `OpenQuoteContext`.
2. `get_stock_basicinfo(market=Market.US, stock_type=SecurityType.STOCK)`.
3. Filter rows: `exchange_type ∈ exchanges`, `suspension == False`,
   `delisting == False`.
4. Snapshot in chunks of 400. For each row read
   `last_price, total_market_val, pre_change_rate, pre_volume, change_rate`.
5. Apply filters; collect surviving codes.
6. Strip `US.` prefix and return plain tickers (e.g. `"TWLO"`).

Error handling: returns `None` on any failure (TCP probe miss, basicinfo error,
snapshot error mid-loop, unexpected exception). Mirrors the existing
`pre_market_gap_futu` / `intraday_cumulative_volume_futu` contract. Logs a
single warning per failure mode.

Note: pre-market field semantics in `get_market_snapshot` are validated against
this account's US Lv1 BBO permission (memory note 2026-04-29).

## Changes to `run_morning_gap` (`main.py:609`)

### Phase 1 — discovery (replaces `main.py:625-641`)

```python
discovery = discover_morning_gap_candidates(
    min_gap_pct=config.get("min_gap_percent", 5.0),
    min_market_cap=config.get("min_market_cap", 300_000_000),
    min_price=config.get("min_price", 10.0),
    pre_market=(offset < 0),
    exchanges=config.get("exchanges", ["US_NASDAQ", "US_NYSE", "US_AMEX"]),
    host=futu_cfg.get("host", "127.0.0.1") if futu_cfg else "127.0.0.1",
    port=futu_cfg.get("port", 11111) if futu_cfg else 11111,
)
if discovery is None:
    logger.warning("[Morning Gap] Futu discovery failed, skipping run")
    return offset, []
tickers = discovery
logger.info(f"  Found {len(tickers)} tickers from Futu snapshot discovery")
if not tickers:
    return offset, []
```

No fallback to Finviz — falling back to the broken `ta_topgainers` path
defeats the purpose.

### Phase 2 — daily download window (`main.py:644-647`)

Change `period="2mo"` → `period="1y"` so SMA200 has enough samples (≥200
trading days).

### Phase 3c — SMA trend gate (new, between ADR% and pre-market revalidation)

Implement `_filter_sma_trend(tickers, daily_data, today_et)`:
- For each ticker, compute SMA50 and SMA200 from `Close` series with today's
  partial bar trimmed (use `_trim_today` already in `main.py`).
- Drop tickers where `last_close < SMA50` OR `last_close < SMA200`.
- Drop tickers with insufficient history (< 200 trading days).
- Log final survivor count.

### Phase 3d — 20-day average volume gate (new, after SMA)

Implement `_filter_avg_volume(tickers, daily_data, min_avg_vol, days, today_et)`:
- Compute 20-day mean Volume per ticker (trim today's partial bar).
- Drop tickers where `mean(Volume[-days:]) < min_avg_vol`.
- Log final survivor count.

This replaces the dropped Finviz `sh_avgvol_o500` filter.

### Phase pre-market revalidation (`main.py:681-705`)

**Remove entirely.** Discovery enforces `pre_change_rate ≥ min_gap_pct` from
the same snapshot, so re-validating is redundant. The
`pre_market_gap_futu` helper stays in `futu_sync.py` for one release as a
safety net, then can be pruned.

### Phase 4 / 5 (post-open avg-vol map + cumulative volume)

Unchanged. The 20-day avg-volume map is still computed (used by cumulative
volume gate). The cumulative volume phase still uses `intraday_cumulative_volume_futu`
with yfinance fallback.

## Config changes (`config.toml [morning_gap]`)

### Removed

```toml
filters = [...]
pre_market_filters = [...]
pre_market_signal = "ta_topgainers"
min_pre_market_gap_percent = 5.0
```

### Added

```toml
min_gap_percent = 5.0                # threshold for pre/post discovery
min_market_cap = 300_000_000         # USD
min_price = 10.0                     # USD
min_avg_volume = 500_000             # 20d avg shares/day
exchanges = ["US_NASDAQ", "US_NYSE", "US_AMEX"]
```

### Kept

```toml
name, scan_offsets, offset_tolerance_minutes,
min_dollar_volume, min_adr_percent (inherited from [settings]),
avg_volume_days
```

The `[notify]` section is unchanged.

## Failure modes

| Failure | Behavior |
|---|---|
| OpenD unreachable / Futu API error during discovery | Discovery returns `None`. `run_morning_gap` logs warning, returns `(offset, [])`. The dated `MorningGap*.txt` is written empty (consistent with existing empty-write contract). Futu sync is skipped (existing `_futu_sync` early-return on empty list). |
| `get_stock_basicinfo` returns 0 rows | Discovery returns `None`. Same as above. |
| Snapshot `RET_ERR` on a single batch | Discovery returns `None` (do not partial-return — better to skip and retry on the next offset window than ship a half-empty discovery). |
| Empty post-filter result | Discovery returns `[]` (not `None`). `run_morning_gap` returns `(offset, [])` cleanly. |
| yfinance daily download fails (Phase 2) | Existing behavior: warn + return `(offset, [])`. |
| Individual ticker missing from snapshot DataFrame | Silently skipped. |

## Testing & verification

1. **TWLO regression test (2026-05-01).** Re-run morning-gap discovery for
   the −20 / −10 offsets manually with today's snapshot data; confirm `TWLO`
   appears in the candidate set after Phase 1 and survives all downstream
   filters. Capture pre-snapshot logs.
2. **Existing-output stability (2026-04-30).** Re-run discovery for the
   same offsets that previously produced `MXL, VIAV, WDC`; confirm those
   tickers still appear (subject to today's snapshot being available — if
   not, document that pre-market data isn't time-machineable and verify
   only forward-looking).
3. **Universe-size telemetry.** Add `logger.info` lines for:
   - basicinfo row count
   - row count after exchange/suspension/delisting filter
   - row count after snapshot field filters
   Expect roughly 12.8k → 5–6k → 10–80 (varies with day).
4. **OpenD-down test.** Set `[futu] port` to a closed port (e.g. 11112);
   run morning_gap; assert log warning, empty `.txt` write, no exception.
5. **Schema sanity.** A one-shot script that calls
   `get_market_snapshot` on a known liquid ticker (e.g. `AAPL`) and asserts
   the expected fields (`pre_change_rate`, `pre_volume`, `change_rate`,
   `total_market_val`, `last_price`) exist. Run once before merging.

## Migration notes

- Removing `filters`/`signal`/`pre_market_filters`/`pre_market_signal` from
  `[morning_gap]` is a breaking config change. Bump nothing — single-user
  project, manual config edit on the same commit.
- The new discovery path **requires** `[futu] enabled = true` and OpenD
  running. Without OpenD the morning-gap pipeline produces empty outputs.
  Document this in `CLAUDE.md` under the Futu section.

## Open questions

None — all four design questions resolved during brainstorming on 2026-05-01.
