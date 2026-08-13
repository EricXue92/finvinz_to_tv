# Morning Gap — Live-Price Trend Gate + Big-Gap SMA50 Bypass — Design

**Date:** 2026-08-13
**Author:** XUE (with Claude)
**Status:** Draft, pending user review

## Problem

Morning-gap's SMA50/SMA200 trend gate (`_filter_sma_trend`, `main.py:808` US /
`main.py:994` HK) compares the **last completed daily close** against SMA50 and
SMA200. During an intraday scan, today's partial bar is trimmed by `_trim_today`
— by design, since half a bar must not pollute a moving average. But the same
trim also means the _comparison basis_ is **yesterday's close**, which is blind
to the very move that put the ticker in the candidate set.

The gate is by far the most destructive stage in the funnel. From the 2026-08-12
21:25 HKT pre-market scan:

```
Futu discovery        159
dollar volume ≥ $100M  91
ADR% ≥ 4%              85
SMA50/SMA200 gate      18   ← drops 67 of 85 (79%)
20d avg volume         18
```

And what it drops is systematically the trend-reversal setup:

| Ticker | prev close (8/11) | SMA50     | SMA200    | 8/12 close      |
| ------ | ----------------- | --------- | --------- | --------------- |
| NBIS   | 193.23            | 221.93 ❌ | 144.24 ✅ | 238.25 (+23.3%) |
| CRWV   | 90.32             | 91.37 ❌  | 93.31 ❌  | 106.46 (+17.9%) |

Both gapped hard and reclaimed their MAs _on the gap bar itself_ — the bar the
scan trims. NBIS was already well above SMA200 and only 13% under SMA50; CRWV
was $1.05 under SMA50 and $3.00 under SMA200. Neither was reachable under the
current rule, because a reversal's first day is by definition the day the
stock has not yet reclaimed its averages. Same-day casualties included ALAB,
ARM, KLAC, LRCX, CLS, IREN.

## Goal

Judge the trend gate against **the price the trader is actually looking at**
(pre-market print, or the live intraday price), and add a narrow escape hatch
for very large gaps that are still under SMA50.

Non-goals: EOD is untouched — `_filter_sma_trend` has exactly two callers, both
morning-gap.

## Key design decisions (resolved during brainstorm)

1. **Moving averages stay computed from completed bars.** Only the _comparison
   basis_ changes. Feeding a partial bar into a 50/200-day mean is the bug this
   design must not introduce.
2. **Both pre-market and post-open scans use the live basis.** Post-open is hit
   by the identical failure (CRWV/NBIS were dropped again at the 22:00 scan off
   the same stale 8/11 close). Having negative and positive offsets disagree
   about what "price" means would be a latent trap.
3. **The big-gap bypass exempts SMA50 only; SMA200 stays mandatory.** SMA200 is
   the long-term-trend floor that keeps out bottom-bounces in ugly downtrends.
   This targets the NBIS shape — long-term uptrend, mid-term pullback, event gap
   — and deliberately does not rescue the CRWV shape (under both MAs) via the
   bypass. CRWV is instead rescued by decision 1+2, on its own merits, once its
   pre-market print clears both lines.
4. **HK stays at parity with US.** HK morning-gap is post-open only, so it gets
   the `last_price` basis and the same bypass. Consistent with the earlier
   decision to align HK's gap threshold to 5%.
5. **All three new parameters default to `None` inside `_filter_sma_trend`,** so
   passing nothing reproduces today's behavior byte-for-byte. That is the
   rollback path.

## Design

### 1. Discovery returns price and gap, not just tickers

`discover_morning_gap_candidates` / `discover_hk_morning_gap_candidates`
currently return `list[str]`, so the trend gate has no access to the live price
or the gap. Both change to return `dict[str, GapQuote]`, where

```python
class GapQuote(NamedTuple):
    price: float   # the live comparison basis
    gap: float     # percent, as used by the discovery threshold
```

Dict insertion order preserves the existing ticker ordering.

| Path          | `price`                                                | `gap`                                          |
| ------------- | ------------------------------------------------------ | ---------------------------------------------- |
| US pre-market | `pre_price`, falling back to `last_price` if absent/≤0 | `pre_change_rate`                              |
| US post-open  | `last_price`                                           | `(last_price - prev_close) / prev_close × 100` |
| HK post-open  | `last_price`                                           | same as US post-open                           |

`None` still signals failure and `{}` still signals "no candidates", so the
callers' existing `if discovery is None` and `if not tickers` branches keep
their meaning. Callers derive `tickers = list(quotes)`.

### 2. `_filter_sma_trend` gains three optional parameters

```python
def _filter_sma_trend(
    tickers, daily_data, today_date,
    sma_short=50, sma_long=200, market_open=True, single=None,
    live_prices: dict[str, float] | None = None,
    gaps: dict[str, float] | None = None,
    bypass_gap_pct: float | None = None,
) -> list[str]:
```

Per ticker:

1. Compute `sma_s` / `sma_l` from the trimmed completed closes, unchanged.
2. `ref = live_prices.get(ticker)` when that is a positive float; otherwise fall
   back to `closes.iloc[-1]` (yesterday's close — today's behavior).
3. Require `ref >= sma_l`. Additionally require `ref >= sma_s` **unless**
   `bypass_gap_pct` is set and `gaps.get(ticker) >= bypass_gap_pct`.
4. Log the basis actually used and whether the bypass fired.

### 3. Config

Added to both `[morning_gap]` and `[hk_morning_gap]`:

```toml
sma_use_live_price = true       # trend gate compares the pre-market / live price, not prev close
sma_bypass_gap_percent = 10.0   # gap >= this exempts SMA50 (SMA200 still enforced); 0 = off
```

`sma_use_live_price = false` restores the prev-close basis without a code
change; `sma_bypass_gap_percent = 0` disables the bypass.

### 4. Error handling — soft fallback throughout

`pre_price` is documented to return `"N/A"` outside the pre-auction session, so
the price chain is `pre_price → last_price → prev close`, never a hard failure.
A ticker missing from `gaps` simply does not get the bypass and takes the strict
path. This matches the project's standing rule that fetch degradation warns and
falls back rather than raising.

## Consequences

The gate now drifts intraday: a ticker can clear it at +10min and fail at
+20min. This is safe because morning-gap's output model is not the EOD one:
morning-gap never calls `_dedup_seen` / touches `eod_seen_{US,HK}` — that
cross-day master is wired only into the `us-eod`/`hk-eod` dispatch. Instead,
each phase's dated `.txt` (`MorningGapPre`, `MorningGap`, `HKMorningGap`) is
fully **overwritten** by every scan of that phase, so a ticker that clears the
gate at -10min and fails it at -5min simply is not in that day's file — it does
not persist. The only per-day state is
`morning_gap_seen_{pre,post}_<date>.txt` (`hk_morning_gap_seen_*` for HK), and
it gates only the one-time ntfy notification and the catalyst-report spawn,
not the watchlist contents. So the worst case from drift is a stray
notification for a ticker that later drops back out of the list — a far
milder failure than a list silently omitting a ticker forever. Accepted
deliberately; a "must hold above the MA for two consecutive scans" rule was
considered and rejected as unnecessary complexity for v1.

## Testing

New `tests/test_morning_gap_sma.py` — `_filter_sma_trend` currently has zero
coverage. Cases, using the 2026-08-12 figures as fixtures:

| Case                                                    | Expected                                      |
| ------------------------------------------------------- | --------------------------------------------- |
| NBIS shape: prev 193.23 under SMA50, live 238 over both | kept                                          |
| CRWV shape: prev 90.32 under both, live 106 over both   | kept                                          |
| `live_prices=None`                                      | falls back to prev close; matches today's log |
| gap 12%, live over SMA200 but under SMA50               | kept (bypass fires)                           |
| gap 12%, live **under** SMA200                          | dropped (SMA200 not exemptible)               |
| gap 8%, live under SMA50                                | dropped                                       |
| `bypass_gap_pct=0`                                      | bypass disabled                               |
| fewer than 200 daily bars                               | dropped, unchanged                            |
