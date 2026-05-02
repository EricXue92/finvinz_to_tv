# Finviz to TradingView

Automated stock screener that runs custom Finviz scans (US) and HKEX + yfinance scans (Hong Kong), exporting results as TradingView- and Webull-importable watchlists and auto-syncing to Futu (富途牛牛) custom watchlist groups via OpenAPI.

## Screening Criteria

> **Stocks-only universe:** All Finviz-based scans (Longs / Leaders / Shorts / RS) include the `ind_stocksonly` filter to exclude ETFs, ETNs, and other non-stock instruments. HK Shorts (sourced from HKEX's equity list) and Morning Gap (sourced from Futu `get_stock_basicinfo` with `stock_type=STOCK`) are stock-only by construction.

### Longs (5 strategies, each written to its own file)

Based on **Oliver Kell**'s momentum/breakout methodology. Each strategy outputs to its own `.txt` file and Futu group; tickers are mutually exclusive across the 5 strategies (priority order shown below — earlier wins).

| Priority | Strategy (file stem) | Key Filters |
|----|-----------------------|-------------|
| 1 | `EarningsGap` | Small Cap+, Earnings Today, Avg Vol > 500K, Price > $20, Rel Vol > 1.5 (Finviz), Gap Up 5%+, Above SMA50 & SMA200 |
| 2 | `HighVolume` | Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200, Rel Vol > 3x 20-day avg (via yfinance) |
| 3 | `GapUp` | Small Cap+, Avg Vol > 500K, Price > $20, Gap Up 3%+, Above SMA50 & SMA200 |
| 4 | `NewHigh52W` | Small Cap+, Avg Vol > 500K, Price > $20, New 52W High, Above SMA50 & SMA200 |
| 5 | `TopGainers` | Small Cap+, Avg Vol > 500K, Price > $20, Above SMA50 & SMA200, Signal: Top Gainers |

All longs strategies also require **Dollar Volume >= $100M** (Price × 20-day avg volume, via yfinance) and **ADR% >= 4.0%** (mean of `(High − Low) / Close` over the last 20 completed daily bars × 100, via yfinance). They additionally pass through an **IBD Relative Strength gate (Percentile >= 90)** — see [IBD Relative Strength Rating Gate](#ibd-relative-strength-rating-gate) below. The "Avg Vol" filters above are Finviz pre-filters using Finviz's 3-month average to reduce result count before post-processing.

### Leaders (5 strategies, merged & deduplicated)

Long-term trend leaders trading above both SMA50 and SMA200. The five strategies share the same base filters but differ in the performance-window threshold:

**Shared base filters:** Small Cap+, Avg Vol > 500K, Price > $20, Above SMA50, Above SMA200, Dollar Volume >= $100M (20-day avg, via yfinance), ADR% >= 4.0% (20-day, via yfinance), IBD RS Percentile >= 90 (see [IBD Relative Strength Rating Gate](#ibd-relative-strength-rating-gate)).

| Strategy | Performance Threshold |
|----------|-----------------------|
| Leaders 4W +30% | 4-week performance >= 30% |
| Leaders 13W +50% | 13-week performance >= 50% |
| Leaders 26W +100% | 26-week performance >= 100% |
| Leaders YTD +100% | YTD performance >= 100% |
| Leaders 52W +150% | 52-week performance >= 150% |

### IBD Relative Strength Rating Gate

Long-side strategies (every Longs split + Leaders) pass through a daily IBD-style RS percentile gate before any expensive yfinance work. Only tickers with **Percentile >= 90** (top 10% momentum names by the IBD weighted-quarter formula) survive; anything below is dropped, and tickers missing from the table — typically recent IPOs without 12 months of history — are KEPT to avoid silently pruning new momentum names.

| | |
|---|---|
| Source | `Fred6725/rs-log/output/rs_stocks.csv` (the published artifact of the [Fred6725/relative-strength](https://github.com/Fred6725/relative-strength) GitHub Action) |
| Algorithm | `RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` normalised against SPY's same-formula score, then percentile-ranked across ~6100 NYSE/NASDAQ stocks |
| Refresh cadence | Weekday ~01:30 UTC (~09:30 HKT) — the EOD pipeline now runs at 10:00 AM HKT specifically to land after this commit |
| Local cache | `output/state/rs_rating_<date>.csv` (one fetch per run; same-day re-runs are offline) |
| Failure mode | If the fetch fails (network, GitHub outage, schema change), every filter call becomes a one-line warning + passthrough — the pipeline still produces output, it just skips the RS gate for the day |
| Scope | Longs (all 5 splits) + Leaders only. Shorts (parabolic blow-offs are by definition high-RS), HK Shorts, the conditional `[rs]` group, and Morning Gap are NOT filtered |
| Config | `[settings] min_rs_percentile = 90` — set to `0` to disable entirely |

The gate is applied **right after `run_screener`**, before yfinance dollar-volume / ADR / relative-volume — so a 90+ cut typically eliminates 80–90% of Finviz hits before any batch download, materially shortening the run.

### Dedup layers

**Within-day, within Longs** — the 5 strategies are mutually exclusive in priority order `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers` (earlier wins).

**Within-day, across long-side groups** — the union of all 5 Longs strategies is deduped against Leaders and RS with priority `Longs(union) > Leaders > RS`. Each ticker appears in at most one of the 7 long-side groups per day.

**Cross-day master ("seen") dedup** — applied only to the **long-side** EOD groups (5 Longs splits + Leaders + RS) after the within-day dedup. A per-market master file at `output/state/eod_seen_{US,HK}.txt` lists every long-side ticker the EOD pipeline has ever written. Each day, each long-side group's output is filtered: only tickers **not in the master** are written to the date-stamped `.txt`, mirrored to Webull, and pushed to Futu (append-only). The new tickers are then added to the master. **Net effect: every long-side ticker enters exactly one of those 7 groups on its first sighting and never re-appears in any long-side output afterward.** The master grows monotonically — reset by deleting the file when you want a fresh universe.

The long-side Futu groups are configured as append-only, so the cumulative ticker set lives in Futu while the daily `.txt` records "today's NEW additions" only.

**Shorts and HK Shorts are intentionally excluded** from this cross-day flow. Short setups (parabolic blow-off, multi-week extension) are time-sensitive and the same ticker re-qualifying weeks later is a meaningful new signal, not duplicate noise. They neither read from nor write to the master, so today's `.txt` contains every Finviz/HKEX-detected short that survives Phase 2 — including re-detections — and a Shorts hit doesn't suppress a future Longs hit on the same ticker. The Shorts/HKShorts Futu groups are still append-only on the Futu side (the group accumulates), but the daily `.txt` reflects today's full hit list.

Morning Gap is excluded too — it has its own per-day seen file (`output/state/morning_gap_seen_<date>.txt`) that auto-resets daily.

When a long-side group's output is empty, `launchd.log` says **why**: `INFO  [Longs/TopGainers] 0 candidates after upstream filters` (screener / yfinance filters returned nothing) versus `WARNING  [Longs/TopGainers] cross-day dedup: ALL N candidate(s) already in master — output will be empty (reset by deleting eod_seen_US.txt)` (everything got filtered as already-seen).

### US Shorts (1 strategy, multi-phase filtering)

Based on **Kristjan Kullamägi**'s short-selling criteria:

**Phase 1 — Finviz filters:**

| Filter | Criteria |
|--------|----------|
| SMA20 | Price 20%+ above 20-day moving average |
| SMA50 | Price above 50-day moving average |
| Avg Volume | > 1M shares (Finviz 3-month avg, pre-filter) |
| Market Cap | > $300M (small cap and above) |

**Phase 2 — Post-processing:**

| Filter | Criteria | Data source |
|--------|----------|-------------|
| Market Cap (for perf bucketing) | Per-ticker live USD value used to pick the perf threshold | **Futu** snapshot (`total_market_val`, one batch call) → Finviz Ownership cap per-ticker fallback |
| Dollar Volume | Price × 20-day avg volume >= $100M | yfinance daily |
| ADR% | mean((High − Low) / Close) over last 20 daily bars × 100 >= 4.0% | yfinance daily |
| Performance (Large Cap ≥ $10B) | Up 50%+ over 2, 3, or 4 weeks | yfinance daily |
| Performance (Mid Cap $2B–$10B) | Up 200%+ over 2, 3, or 4 weeks | yfinance daily |
| Performance (Small Cap $300M–$2B) | Up 300%+ over 2, 3, or 4 weeks | yfinance daily |
| Consecutive Up Days | 3+ consecutive green days (excludes today's incomplete data if market is still open) | yfinance daily |

Performance is checked over 2-week (10 trading days), 3-week (15 trading days), and 4-week (22 trading days) windows via yfinance. A ticker passes the perf step if it meets the cap-conditional threshold in any window. The Phase 2 filters then run in this order on a single shared yfinance download: **performance → dollar volume → ADR% → consecutive up days**.

Market cap for the perf bucketing is sourced from Futu's real-time snapshot (preferred) rather than Finviz's coarse `"6.96M"` / `"1.23B"` strings — the Finviz formatting truncates to 3 sig figs, which can mis-bucket tickers that sit near the `$2B` / `$10B` boundaries. Finviz Ownership is still parsed first and is used per-ticker for any name Futu doesn't return, and as a full fallback when `[futu] enabled = false` or OpenD is unreachable.

### RS - Relative Strength (conditional)

Based on **Oliver Kell**'s relative strength approach. Only runs when both SPY and QQQ drop more than 1.5% on the day — identifies stocks showing strength in a weak market.

| Strategy | Key Filters |
|----------|-------------|
| Relative Strength | Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200, Dollar Volume >= $100M (via yfinance), ADR% >= 4.0% (via yfinance) |

### HK Shorts (1 strategy, multi-phase filtering)

Hong Kong market short candidates using the same methodology as US Shorts, sourced from **HKEX + yfinance** instead of Finviz.

**Phase 1 — HKEX universe + yfinance filtering:**

| Filter | Criteria |
|--------|----------|
| Universe | HKEX Main Board equities (~2,400 stocks) |
| SMA20 | Price 20%+ above 20-day moving average |
| SMA50 | Price above 50-day moving average |
| Avg Volume | > 1M shares/day (20-day average) |

**Phase 2 — Post-processing:**

| Filter | Criteria | Data source |
|--------|----------|-------------|
| Market Cap | >= HKD 300M | **Futu** snapshot (`total_market_val`, one batch call) → yfinance `fast_info.market_cap` per-ticker fallback |
| Dollar Volume | Price × 20-day avg volume >= HKD 100M | yfinance daily |
| ADR% | mean((High − Low) / Close) over last 20 daily bars × 100 >= 4.0% | yfinance daily |
| Performance (Large Cap ≥ HKD 10B) | Up 50%+ over 2, 3, or 4 weeks | yfinance daily |
| Performance (Mid Cap HKD 2B–10B) | Up 200%+ over 2, 3, or 4 weeks | yfinance daily |
| Performance (Small Cap HKD 300M–2B) | Up 300%+ over 2, 3, or 4 weeks | yfinance daily |
| Consecutive Up Days | 3+ consecutive green days | yfinance daily |

HK tickers are output in `HKEX:XXXX` format for TradingView (e.g. `HKEX:0700`).

### Morning Gap (pre-market + intraday, 7 scans)

Two-phase scanner. **Pre-market (-20 / -10 min before US open)** writes to `MorningGapPre.txt` as an early candidate list. **Post-open (+10 / +15 / +20 / +25 / +30 min)** writes to `MorningGap.txt` and adds an intraday cumulative-volume gate that captures stocks already trading their full daily average volume in the first 30 minutes — a signal of catalyst-driven institutional buying (earnings, FDA, M&A, sector news).

**Phase 1 — Futu snapshot discovery (replaces Finviz):**

Earlier versions screened the candidate set via Finviz's `ta_topgainers` signal, but that ranks by recent regular-session performance, so a stock gapping +19.5% pre-market on earnings (e.g. TWLO 2026-05-01) never entered the candidate set. Discovery now scans NASDAQ / NYSE / AMEX directly via `get_stock_basicinfo` + bulk `get_market_snapshot` (batches of 400) and applies the gap threshold against live snapshot data, so today's actual gappers always surface.

| Filter | Pre-market | Post-open | Snapshot field |
|--------|------------|-----------|----------------|
| Universe | US NASDAQ / NYSE / AMEX, not delisted, `stock_type = STOCK` | same | `get_stock_basicinfo` |
| Market Cap | >= $300M | same | `total_market_val` |
| Price | >= $10 | same | `last_price` |
| Gap | `pre_change_rate` >= 5% (and `pre_volume > 0`) | `(last_price − prev_close_price) / prev_close_price * 100` >= 5% | `pre_change_rate` / derived |

The post-open path derives the gap manually because the installed `futu-api` SDK's `get_market_snapshot` DataFrame has no `change_rate` column. The basicinfo `suspension` column is a string (`"N/A"`) in this SDK and matches 0 rows when compared to `False`, so the active-listing gate uses `delisting` (a real bool) plus the exchange whitelist.

**Phase 2 — yfinance post-processing (1y daily download for SMA200):**

| Filter | Criteria | Pre-market | Post-open | Data source |
|--------|----------|------------|-----------|-------------|
| Dollar Volume | Price × 20-day avg volume >= $100M | ✓ | ✓ | yfinance daily |
| ADR% | mean((High − Low) / Close) over last 20 daily bars × 100 >= 4.0% | ✓ | ✓ | yfinance daily |
| SMA50 / SMA200 trend | Latest close above both SMA50 and SMA200 | ✓ | ✓ | yfinance daily |
| 20-day Avg Volume | >= 500K shares/day | ✓ | ✓ | yfinance daily |
| Intraday Cumulative Volume | Today's RTH cumulative volume since 9:30 ET >= 20-day avg daily volume | — | ✓ | **Futu** snapshot (`volume`) → yfinance 1m fallback |

The intraday volume threshold (post-open only) is the key signal — by 10–30 min after open, the stock has already done a full day's worth of trading. Per Kullamägi: "the best ones have traded their average daily volume in the first 15–30 minutes after the open."

The pre-market path needs no separate gap revalidation: discovery already enforced `pre_change_rate >= min_gap_percent` from the same Futu snapshot, so survivors of Phase 2 ship directly to `MorningGapPre.txt`.

**Why ADR% instead of Finviz beta:** The earlier `ta_beta_o1.5` (beta > 1.5) was excluding mid/large-cap catalyst names (biotech, services with beta 1.0–1.3) that are actually "in-play" on a given session. Beta measures correlation with the broad market over years of history — orthogonal to whether a stock is currently moving on news. **The beta filter has been removed from every group and replaced by an ADR% threshold applied across Longs, Leaders, RS, Shorts, HK Shorts, and Morning Gap.** ADR% (Kullamägi-style) is the average of daily `(High − Low) / Close` over the last 20 completed sessions × 100; the global default is 4.0% and is configured once in `[settings]` (`min_adr_percent`, `adr_days`). Set `min_adr_percent = 0` in `[settings]` to disable globally. Shorts, HK Shorts, and Morning Gap also accept a per-section override of the same key if that group needs a different threshold.

**Why Futu OpenAPI is required:** Both Phase 1 discovery and the post-open cumulative-volume filter rely on Futu's real-time snapshot. With `[futu] enabled = false` or OpenD unreachable, `--mode morning-gap` writes empty `.txt` files, skips the Futu sync, and logs a single warning per run — there is no Finviz fallback. Futu requires US Lv1 BBO real-time quote permission on the OpenD account; without it the snapshot's pre/post fields return delayed/empty values and discovery would silently drop everything.

Each scan that surfaces **new** tickers (not seen in any earlier morning-gap scan today) also pushes an ntfy notification to phone + Mac — see [Push notifications (ntfy)](#push-notifications-ntfy) below.

## Output

```
output/
├── TV/                              # Comma-separated, for TradingView "Import list..."
│   ├── US/
│   │   ├── 2026_04_27_EarningsGap.txt   # Longs strategy 1 (highest priority)
│   │   ├── 2026_04_27_HighVolume.txt    # Longs strategy 2 (Relative Volume Surge)
│   │   ├── 2026_04_27_GapUp.txt         # Longs strategy 3
│   │   ├── 2026_04_27_NewHigh52W.txt    # Longs strategy 4
│   │   ├── 2026_04_27_TopGainers.txt    # Longs strategy 5 (lowest priority)
│   │   ├── 2026_04_27_Leaders.txt       # US trend leaders
│   │   ├── 2026_04_27_Shorts.txt        # US short candidates
│   │   ├── 2026_04_27_RS.txt            # Relative strength (only on RS-eligible days)
│   │   ├── 2026_04_27_MorningGapPre.txt # Pre-market morning-gap candidates (-20/-10 min)
│   │   └── 2026_04_27_MorningGap.txt    # Post-open morning-gap snapshot (+10..+30 min)
│   └── HK/
│       └── 2026_04_27_Shorts.txt        # HK short candidates
└── Webull/                          # Newline-separated mirror, for Webull "Upload as File"
    ├── US/
    │   └── 2026_04_27_*.txt         # Same filenames as TV/US/ above
    └── HK/
        └── 2026_04_27_Shorts.txt
```

Each run writes a single date-stamped file per group. The 5 Longs strategies are mutually exclusive (priority `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers`); their union is then deduped against Leaders and RS (`Longs > Leaders > RS`), so each ticker appears in exactly one of the 7 long-side files. Files are comma-separated ticker symbols, ready for TradingView import.

**Always write:** Every run produces a fresh dated `.txt` for each group, even when the screen returns nothing — empty results just yield a 0-byte file for the day. Yesterday's archive is left intact under its own dated filename, so historical runs are never overwritten. Futu sync, however, is **skipped** on empty results so an off day doesn't wipe an existing Futu group.

### Futu (富途牛牛) Auto-Sync

After each successful watchlist write, the script can sync tickers to a Futu custom watchlist group via OpenAPI. Configured via `[futu]` in `config.toml`. The `.txt` files remain the primary output — Futu sync failures (OpenD not running, group missing, etc.) only log a warning.

**Prerequisites:**
1. Download & launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in with your Futu account (default port `11111`).
2. In the Futu PC client, manually create the custom watchlist groups: `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts` (the API can only modify custom groups, not create them).
3. Set `enabled = true` in `[futu]` (already on by default).

**Sync strategy:** Diff-based for any group **not** listed in `[futu] append_only_groups`; fetches current group contents, then ADDs new tickers and DELs missing ones (Futu rate limit: 10 calls per 30s).

**Append-only mode (all EOD groups):** Every EOD Futu group — `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts` — is listed in `[futu] append_only_groups`, so sync only ADDs and never DELs. For the long-side groups this pairs with the cross-day master `seen` dedup so each ticker enters its group on first sighting and stays there. For `Shorts` / `HKShorts` (which skip the master), the daily `.txt` carries today's full short list including re-detections — the Futu sync simply ADDs whatever isn't already in the group, so re-detected tickers are no-ops on the Futu side and the group still grows monotonically. Tickers accumulate monotonically; clear groups manually in the Futu client when they get too crowded (Futu caps: 500 per group for non-traders, 2000 for active traders). The merged `EarningsGap` group additionally receives `morning_gap_pre` and `morning_gap`; the three corresponding `.txt` files (`EarningsGap.txt`, `MorningGapPre.txt`, `MorningGap.txt`) remain separate and unaffected.

### Push notifications (ntfy)

Each successful Morning Gap scan that surfaces **new** tickers pushes a notification to phone + Mac via [ntfy.sh](https://ntfy.sh). "New" = not seen in any earlier morning-gap scan today; the same ticker won't re-ping across the 7 daily scans. Configured via `[notify]` in `config.toml`. Notification failures only log a warning — never block the scan.

**Title:** `Morning Gap ±Nmin · K new` (sign+offset from open, count of new tickers)
**Body:** Up to `max_tickers_in_body` tickers comma-separated, then `(+N more)` if truncated, then ` · total: M` (full scan count).

**Setup (once):**
1. Install the ntfy iOS / Android app (free, no account).
2. Subscribe to your `ntfy_topic` from `config.toml`. The topic name is your private channel — anyone who knows it can subscribe, so the default suffix is a random string.
3. (Mac, optional) Open `https://ntfy.sh/<your_ntfy_topic>` in Chrome/Safari and click "Subscribe to this topic" → Allow notifications. The browser tab can stay in the background.

**State:** A daily seen-set is kept at `output/state/morning_gap_seen_<YYYY_MM_DD>.txt` (one ticker per line, auto-resets each day via filename). Pre-market and post-open scans share the same file so a ticker that appeared at -30min won't re-ping at +15min.

To disable: set `[notify] enabled = false` in `config.toml`.

## Setup

```bash
# Install dependencies
uv sync

# Run EOD pipeline manually (Longs / Leaders / Shorts / RS / HK Shorts)
uv run main.py

# Run intraday morning-gap scan manually
uv run main.py --mode morning-gap
```

The morning-gap scanner auto-detects current US ET time and runs the matching scan (-20/-10 pre-market, +10/+15/+20/+25/+30 post-open, ±2 min tolerance). Outside any window it logs and exits cleanly.

Every run prints a 3-line banner at the top of stdout / `launchd.log` showing the run date, weekday, HKT time, and mode — useful when grepping back through accumulated launchd logs to find a specific day's run:

```
════════════════════════════════════════════════════════════════
  RUN 2026-04-30 Thursday 11:04 HKT  |  mode=End-of-Day
════════════════════════════════════════════════════════════════
```

## Import to TradingView

1. Open TradingView
2. Right panel → Watchlist → Click the list name
3. Select "Import list..."
4. Choose the latest dated file, e.g. `output/TV/US/2026_04_27_HighVolume.txt` (or `EarningsGap` / `GapUp` / `NewHigh52W` / `TopGainers` / `Leaders` / `Shorts` / `RS` / `MorningGap` / `MorningGapPre` for US, `output/TV/HK/2026_04_27_Shorts.txt` for HK)

## Import to Webull

Webull's "Upload as File" only recognizes one ticker per line — comma-separated lists silently truncate after the first 1-2 entries. The script writes a parallel mirror for this purpose.

1. Open Webull → Watchlist → "Upload as File" (in the More Settings / File menu)
2. Choose the corresponding file from `output/Webull/US/` or `output/Webull/HK/` (same filename as the TradingView version, just newline-separated)

## Automation (launchd + pmset)

The script runs daily after US market close via macOS launchd, with `pmset` to wake the Mac from sleep.

**Schedule:** Tue–Sat 10:00 AM HKT = Mon–Fri after US market close. 10:00 AM HKT is safe for both EDT (6h after close) and EST (5h after close), lets yfinance/Finviz EOD data fully settle, **and lands after the daily Fred6725/rs-log RS Rating commit (worst-observed lag: ~01:30 UTC = ~09:30 HKT)** so the IBD RS table is fresh when the Longs/Leaders gate reads it. Earlier slots (e.g. 8:30 AM) used to produce noisier results and would also race the upstream RS publish.

### How it works

1. **`pmset repeat`** wakes the Mac at 9:59 AM HKT (Tue–Sat)
2. **launchd** (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`) runs the script at 10:00 AM
3. After execution, the Mac automatically returns to sleep

### Setup

```bash
# Schedule Mac to wake at 9:59 AM Tue-Sat
sudo pmset repeat wakeorpoweron TWRFS 09:59:00

# Verify wake schedule
pmset -g sched
```

The launchd plist is installed at `~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`. To manage it:

```bash
# Load (enable)
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# Unload (disable)
launchctl unload ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# Check status
launchctl list | grep finviz
```

> **Note:** Unlike cron, launchd will catch up on missed runs — if the Mac was asleep at 8:30 AM, the task executes as soon as the Mac wakes up.

### Intraday Morning Gap Schedule

The intraday scanner is driven by a separate plist `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist` with 70 calendar entries (Mon–Fri × 7 offsets × EDT/EST). The script self-validates current ET time on each trigger — if not within ±2 min of any scan offset (e.g. on a DST transition day or off-hours run), it exits cleanly without writing.

| Time (HKT) | NY Time | DST | Offset | Output |
|---|---|---|---|---|
| 21:10 / 21:20 | 09:10 / 09:20 | EDT | -20 / -10 | `MorningGapPre.txt` |
| 21:40 / 21:45 / 21:50 / 21:55 / 22:00 | 09:40 / 09:45 / 09:50 / 09:55 / 10:00 | EDT | +10 / +15 / +20 / +25 / +30 | `MorningGap.txt` |
| 22:10 / 22:20 | 09:10 / 09:20 | EST | -20 / -10 | `MorningGapPre.txt` |
| 22:40 / 22:45 / 22:50 / 22:55 / 23:00 | 09:40 / 09:45 / 09:50 / 09:55 / 10:00 | EST | +10 / +15 / +20 / +25 / +30 | `MorningGap.txt` |

```bash
# Load (enable)
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist

# Check status
launchctl list | grep morning-gap

# Tail logs
tail -f /tmp/finviz-to-tv-morning-gap.log
```

> **Wake-up:** `pmset repeat` only supports one wake schedule (already used by the 8:29 AM EOD wake). For the intraday scanner, run `scripts/schedule_morning_gap_wakes.py` to schedule per-day `pmset schedule wake` entries at 20:59 and 21:59 HKT (11 min before each window's first pre-market scan, covers EDT and EST). Re-run weekly to top up.

```bash
# Schedule next 14 weekdays of wakes (one-shot events, requires sudo)
sudo uv run scripts/schedule_morning_gap_wakes.py

# Or specify number of days
sudo uv run scripts/schedule_morning_gap_wakes.py 30

# Verify
pmset -g sched
```

## Configuration

All screener parameters are in `config.toml`. You can modify filters, add new screeners, or adjust settings (delay between requests, output format) without touching the code.

## Dependencies

- Python >= 3.12
- [finviz](https://github.com/mariostoev/finviz) — Finviz web scraper (no API key or premium account required)
- [yfinance](https://github.com/ranaroussi/yfinance) — Yahoo Finance data for post-processing filters and HK market data
- [openpyxl](https://openpyxl.readthedocs.io/) — HKEX securities list xlsx parsing
- [futu-api](https://pypi.org/project/futu-api/) — Optional, for Futu watchlist sync via OpenAPI
