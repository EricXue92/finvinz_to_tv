# IPO List

## Problem

Tickers that pass a Finviz long-side screener but are dropped by the yfinance step (insufficient daily history, no data, or processing error) are silently lost. These are almost always **fresh IPOs** — they qualify on Finviz's price/volume signals but lack the 20+ daily bars yfinance needs to compute dollar volume, ADR%, or relative volume.

Today these names disappear into a `WARNING` log line. We want to surface them as a separate watchlist so they can be tracked while they age into the regular long-side groups.

## Scope

**Long-side only.** The IPO collector is wired through:
- 5 Longs splits (EarningsGap / HighVolume / GapUp / NewHigh52W / TopGainers)
- Leaders
- RS (when it runs)

Excluded: Shorts, HK Shorts, MorningGap, MorningGapPre. Shorts are by definition not IPO candidates (parabolic blow-offs need history). Morning-gap scans use a different (Futu-snapshot) discovery path.

## What counts as an "IPO drop"

Any of the three drop messages emitted by long-side yfinance filters:
- `insufficient data` — yfinance returned fewer than N bars
- `insufficient volume data` — same, in `filter_relative_volume`
- `insufficient daily bars for ADR%` — same, in `_filter_adr_percent`
- `failed to process` — `KeyError`/`TypeError` from a missing column (typical when yfinance returns nothing for a ticker)

If a ticker is dropped by **any** long-side filter for any of these reasons, it goes into the IPO list.

A ticker that survives one group's filters but is dropped by another's still counts — the detection signal ("yfinance can't model this name yet") is what matters.

## Cross-day dedup

Separate master file `output/state/eod_seen_IPO.txt`, modeled exactly on `eod_seen_US.txt`. A ticker shows up in the IPO file the first day it's detected; on subsequent runs it's suppressed.

This master is **independent** of `eod_seen_US.txt`. When the IPO eventually has enough yfinance history and qualifies for a real long-side group, the long-side master has not seen it, so it lands normally in (e.g.) GapUp on its first qualifying day.

The IPO master is reset by deleting the file manually, same convention as the others.

## Outputs

Per run, after long-side groups have been written:

1. `output/TV/US/<date>_IPO.txt` — comma-separated, primary artifact
2. `output/Webull/US/<date>_IPO.txt` — newline mirror
3. Futu group `IPO` — append-only; one DEL-skipped sync call

If `ipo_drops` is empty, no file is written? **No** — match existing behavior: every run writes a dated file even if empty (0-byte). Futu sync is skipped on empty (existing `_futu_sync` early-return).

## Implementation

### Filter signature changes

Three functions gain an optional `ipo_drops: set[str] | None = None` parameter:
- `_filter_dollar_volume_from_data` — add to set on `insufficient data` and `failed to process`
- `_filter_adr_percent` — add to set on `insufficient daily bars for ADR%` (NOT on `ADR% < min` — that's a real filter, not a data issue)
- `filter_relative_volume` — add to set on `insufficient volume data` and `failed to process`

The wrapper `filter_dollar_volume_and_adr_yf` also gains the parameter and forwards it.

### Pipeline plumbing

`main()` creates one `ipo_drops: set[str] = set()` at the top of the EOD branch (US only). Each long-side filter call passes it. The set accumulates across all 7 long-side groups.

After RS finishes, before the IPO master/write step:
```python
sorted_ipo = sorted(ipo_drops)
sorted_ipo = _dedup_seen("[IPO]", sorted_ipo, ipo_seen, ipo_seen_path)
dated = us_output_dir / f"{today}_IPO.txt"
write_watchlist(sorted_ipo, dated, fmt)
_write_webull(sorted_ipo, dated, output_dir)
_futu_sync(config, "ipo", sorted_ipo, "US")
```

`ipo_seen_path = _eod_seen_path(output_dir, "IPO")` reuses the existing helper — it just generates `state/eod_seen_IPO.txt`.

### Config additions

```toml
append_only_groups = [..., "IPO"]

[futu.groups]
ipo = "IPO"
```

### Manual operator step

User must create the `IPO` custom watchlist group in the Futu PC client before the next run, same as the existing 9 groups. Documented in CLAUDE.md.

## Non-goals

- No retry/recovery: if yfinance has insufficient data, we accept the drop and capture it for the IPO list. We don't try harder to find data.
- No price/volume gating on the IPO list: by definition these tickers don't have enough data to compute those filters. The Finviz pre-filter (price > $20, avg vol > 500K, etc.) is the only gate.
- No automatic graduation: a ticker stays in the IPO master forever. When it qualifies for a long-side group, the long-side master picks it up independently. This means the IPO master grows monotonically and is only ever cleared by hand. That's fine — IPO drops are rare.
