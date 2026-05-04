# RS Gate — Two-Tier Design (Longs/RS at 80, Leaders at 90)

**Status:** Approved (design)
**Date:** 2026-05-04

## Goal

Extend the IBD Relative Strength gate beyond Leaders. Add a second, more permissive tier (RS ≥ 80) that applies to the Longs splits and the conditional RS group, while the existing Leaders gate stays at RS ≥ 90.

## Behavior

| Group | Setting | Threshold |
|---|---|---|
| Leaders (5 strategies) | `min_rs_percentile` | ≥ 90 (unchanged) |
| Longs — `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers` | `min_rs_percentile_longs` | ≥ 80 (NEW) |
| RS (conditional weak-market) | `min_rs_percentile_longs` | ≥ 80 (NEW) |
| Shorts, HK Shorts, Morning Gap, IPO | — | not gated (unchanged) |

Both gates run **right after `run_screener`**, before any yfinance batch download (dollar-volume / ADR), so the cheap RS lookup cuts roughly an order of magnitude before expensive work.

**Missing tickers** (recent IPOs without 12 months of history, foreign listings) are KEPT — same as today's Leaders behavior. No change to `rs_rating.filter_by_rs`.

**Disable**: set either knob to `0` independently. The RS table fetch is skipped only when **both** knobs are 0.

**Failure mode** (unchanged from today): if `fetch_rs_table` fails, every `filter_by_rs` call logs a one-line warning and passes through. Not a hard failure — the `.txt` files remain the primary artifact.

**IPO sidecar interaction**: an RS-gate drop is *not* an "insufficient yfinance data" drop, so RS-rejected long-side tickers do NOT land in `<date>_IPO.txt`. This matches the existing Leaders behavior.

## Config

`config.toml` — one new key under `[settings]`:

```toml
[settings]
min_rs_percentile = 90          # Leaders (unchanged)
min_rs_percentile_longs = 80    # Longs (5 splits) + RS group (NEW)
```

No per-strategy overrides. Both tiers are global.

## Code changes (`main.py`)

Three edits, all modeled on the existing Leaders gate at `main.py:1509-1511`.

**1. Read the new setting** (near line 1410, alongside `min_rs_percentile`):

```python
min_rs_percentile = settings.get("min_rs_percentile", 0)
min_rs_percentile_longs = settings.get("min_rs_percentile_longs", 0)
```

**2. Update the RS table fetch trigger** (line 1438):

```python
rs_table = (
    fetch_rs_table(output_dir, today)
    if max(min_rs_percentile, min_rs_percentile_longs) > 0
    else None
)
```

**3. Apply the gate at two new call sites:**

- **Longs**: inside the per-strategy loop, right after `run_screener`, before any other filter. Each split logs `[Longs/<name>]`.
- **RS group**: inside the conditional RS branch, right after `run_screener`, before dollar-volume / ADR. Logs `[RS]`.

Both reuse the existing `filter_by_rs(tickers, rs_table, min_rs_percentile_longs, label)`. No change to `rs_rating.py`.

**Ordering invariants**:
- RS gate fires **before** yfinance work for performance.
- RS gate fires **before** the IPO sidecar collector — so RS-rejected tickers are silently dropped, not collected as IPO drops. Matches Leaders today.
- RS gate fires **before** the cross-group `Longs > Leaders > RS` priority dedup — the dedup operates on the RS-gated survivors of each group.

## Documentation updates

**CLAUDE.md** ("IBD Relative Strength Rating" section):
- Rewrite the lead paragraph: replace "applied to Leaders" with the two-tier split.
- Rewrite the "Scope" bullet to list both tiers and which groups belong to each.
- Soften the "Longs are intentionally NOT RS-gated" rationale into "Longs use 80 not 90 because they're catalyst-driven (gap-ups, earnings) where the trigger qualifies the name; a 90+ gate would prune fresh breakouts that haven't built a 12-month track record."
- Update the `[settings]` config snippet.

**README.md** ("Global gates (long-side)" table):
- Split the IBD RS row into two rows (Leaders ≥ 90 / Longs + RS ≥ 80).
- Rename the "Why RS is Leaders-only" paragraph to "Why two RS tiers" and explain the 90 vs 80 split.

## Testing (operational, no automated tests in repo)

- **Smoke run** with both gates active: confirm logs show
  - `[Longs/EarningsGap] N → M (RS≥80)`, `[Longs/HighVolume] ...`, etc. for all 5 splits
  - `[Leaders/<name>] N → M (RS≥90)` for all 5 Leaders strategies
  - `[RS] N → M (RS≥80)` if SPY *and* QQQ are both down > 1.5%
- **Ablation — Longs disabled**: set `min_rs_percentile_longs = 0`, re-run, confirm Longs splits skip the RS step entirely (no `[Longs/...] ... (RS≥80)` line). Leaders still gated at 90.
- **Ablation — both disabled**: set both to 0, re-run, confirm `output/state/rs_rating_<date>.csv` is absent (no HTTP fetch).
- **Failure injection**: rename today's cached RS CSV mid-run or temporarily break the URL in `rs_rating.py`. Confirm a single warning per call site (Leaders × 5, Longs × 5, RS × 1) and full passthrough — no exception, all `.txt` files still written.

## Out of scope

- Per-strategy thresholds inside Longs (one global Longs threshold).
- Gating Shorts / HK Shorts / Morning Gap.
- Changing the missing-tickers-KEPT behavior in `filter_by_rs`.
- Any change to the RS table source, schema, or caching.
