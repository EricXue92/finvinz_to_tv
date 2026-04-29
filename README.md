# Finviz to TradingView

Automated stock screener that runs custom Finviz scans (US) and HKEX + yfinance scans (Hong Kong), exporting results as TradingView- and Webull-importable watchlists and auto-syncing to Futu (富途牛牛) custom watchlist groups via OpenAPI.

## Screening Criteria

> **Stocks-only universe:** All Finviz-based scans (Longs / Leaders / Shorts / RS / Morning Gap) include the `ind_stocksonly` filter to exclude ETFs, ETNs, and other non-stock instruments. HK Shorts is sourced from HKEX's equity list directly and is already stock-only by construction.

### Longs (5 strategies, each written to its own file)

Based on **Oliver Kell**'s momentum/breakout methodology. Each strategy outputs to its own `.txt` file and Futu group; tickers are mutually exclusive across the 5 strategies (priority order shown below — earlier wins).

| Priority | Strategy (file stem) | Key Filters |
|----|-----------------------|-------------|
| 1 | `EarningsGap` | Small Cap+, Earnings Today, Avg Vol > 500K, Price > $20, Rel Vol > 1.5 (Finviz), Gap Up 5%+, Above SMA200 |
| 2 | `HighVolume` | Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA200, Rel Vol > 3x 20-day avg (via yfinance) |
| 3 | `GapUp` | Small Cap+, Avg Vol > 500K, Price > $20, Gap Up 3%+, Above SMA200 |
| 4 | `NewHigh52W` | Small Cap+, Avg Vol > 500K, Price > $20, New 52W High, Above SMA50 & SMA200 |
| 5 | `TopGainers` | Small Cap+, Avg Vol > 500K, Price > $20, Above SMA200, Signal: Top Gainers |

All longs strategies also require **Dollar Volume >= $100M** (Price × 20-day avg volume, via yfinance) and **ADR% >= 3.5%** (mean of `(High − Low) / Close` over the last 20 completed daily bars × 100, via yfinance). The "Avg Vol" filters above are Finviz pre-filters using Finviz's 3-month average to reduce result count before post-processing.

### Leaders (5 strategies, merged & deduplicated)

Long-term trend leaders trading above both SMA50 and SMA200. The five strategies share the same base filters but differ in the performance-window threshold:

**Shared base filters:** Small Cap+, Avg Vol > 500K, Price > $20, Above SMA50, Above SMA200, Dollar Volume >= $100M (20-day avg, via yfinance), ADR% >= 3.5% (20-day, via yfinance).

| Strategy | Performance Threshold |
|----------|-----------------------|
| Leaders 4W +30% | 4-week performance >= 30% |
| Leaders 13W +50% | 13-week performance >= 50% |
| Leaders 26W +100% | 26-week performance >= 100% |
| Leaders YTD +100% | YTD performance >= 100% |
| Leaders 52W +150% | 52-week performance >= 150% |

### Cross-group dedup (Longs / Leaders / RS)

Two layers:

1. **Within Longs** — the 5 strategies are mutually exclusive in priority order `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers` (earlier wins).
2. **Across long-side groups** — the union of all 5 Longs strategies is then deduped against Leaders and RS with priority `Longs(union) > Leaders > RS`.

Result: each ticker appears in exactly one of the 7 long-side files (5 Longs splits + Leaders + RS) per day. Because every `.txt` file and Futu group is rewritten on each run, this also prevents day-over-day cross-group duplication — a ticker that migrates between groups is automatically removed from its old group.

Shorts, HK Shorts, and Morning Gap are independent and do not participate in the dedup.

### US Shorts (1 strategy, multi-phase filtering)

Based on **Kristjan Kullamägi**'s short-selling criteria:

**Phase 1 — Finviz filters:**

| Filter | Criteria |
|--------|----------|
| SMA20 | Price 20%+ above 20-day moving average |
| Avg Volume | > 1M shares (Finviz 3-month avg, pre-filter) |
| Market Cap | > $300M (small cap and above) |

**Phase 2 — Post-processing (via yfinance):**

| Filter | Criteria |
|--------|----------|
| Dollar Volume | Price × 20-day avg volume >= $100M |
| ADR% | mean((High − Low) / Close) over last 20 daily bars × 100 >= 3.5% |
| Performance (Large Cap ≥ $10B) | Up 50%+ over 2, 3, or 4 weeks |
| Performance (Mid Cap $2B–$10B) | Up 200%+ over 2, 3, or 4 weeks |
| Performance (Small Cap $300M–$2B) | Up 300%+ over 2, 3, or 4 weeks |
| Consecutive Up Days | 3+ consecutive green days (excludes today's incomplete data if market is still open) |

Performance is checked over 2-week (10 trading days), 3-week (15 trading days), and 4-week (22 trading days) windows via yfinance. A ticker passes the perf step if it meets the cap-conditional threshold in any window. The Phase 2 filters then run in this order on a single shared yfinance download: **performance → dollar volume → ADR% → consecutive up days**.

### RS - Relative Strength (conditional)

Based on **Oliver Kell**'s relative strength approach. Only runs when both SPY and QQQ drop more than 1.5% on the day — identifies stocks showing strength in a weak market.

| Strategy | Key Filters |
|----------|-------------|
| Relative Strength | Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200, Dollar Volume >= $100M (via yfinance), ADR% >= 3.5% (via yfinance) |

### HK Shorts (1 strategy, multi-phase filtering)

Hong Kong market short candidates using the same methodology as US Shorts, sourced from **HKEX + yfinance** instead of Finviz.

**Phase 1 — HKEX universe + yfinance filtering:**

| Filter | Criteria |
|--------|----------|
| Universe | HKEX Main Board equities (~2,400 stocks) |
| SMA20 | Price 20%+ above 20-day moving average |
| Avg Volume | > 1M shares/day (20-day average) |

**Phase 2 — Post-processing:**

| Filter | Criteria | Data source |
|--------|----------|-------------|
| Market Cap | >= HKD 300M | **Futu** snapshot (`total_market_val`, one batch call) → yfinance `fast_info.market_cap` per-ticker fallback |
| Dollar Volume | Price × 20-day avg volume >= HKD 100M | yfinance daily |
| ADR% | mean((High − Low) / Close) over last 20 daily bars × 100 >= 3.5% | yfinance daily |
| Performance (Large Cap ≥ HKD 10B) | Up 50%+ over 2, 3, or 4 weeks | yfinance daily |
| Performance (Mid Cap HKD 2B–10B) | Up 200%+ over 2, 3, or 4 weeks | yfinance daily |
| Performance (Small Cap HKD 300M–2B) | Up 300%+ over 2, 3, or 4 weeks | yfinance daily |
| Consecutive Up Days | 3+ consecutive green days | yfinance daily |

HK tickers are output in `HKEX:XXXX` format for TradingView (e.g. `HKEX:0700`).

### Morning Gap (pre-market + intraday, 7 scans)

Two-phase scanner. **Pre-market (-20 / -10 min before US open)** writes to `MorningGapPre.txt` as an early candidate list — Finviz filters → dollar volume → ADR% → pre-market gap revalidation, but no intraday volume confirmation yet (the regular session hasn't opened). **Post-open (+10 / +15 / +20 / +25 / +30 min)** writes to `MorningGap.txt` — Finviz filters → dollar volume → ADR% → intraday cumulative-volume filter, which captures stocks that have already traded their full daily average volume in the first 30 minutes (a signal of catalyst-driven institutional buying — earnings, FDA, M&A, sector news).

**Phase 1 — Finviz filters (different sets for pre-market vs post-open):**

| Filter | Pre-market candidate set | Post-open candidate set |
|--------|--------------------------|-------------------------|
| Market Cap | Small Cap+ (>= $300M) | Small Cap+ (>= $300M) |
| Avg Volume | > 500K | > 500K |
| Price | > $10 | > $10 |
| Gap Up | — (Finviz `Gap` is yesterday's gap before 9:30 ET; see note below) | >= 5% (Finviz `ta_gap_u5`) |
| SMA200 | Price above SMA200 | Price above SMA200 |
| Signal | Top Gainers (Finviz `ta_topgainers`) | — |

**Phase 2 — Post-processing:**

| Filter | Criteria | Pre-market | Post-open | Data source |
|--------|----------|------------|-----------|-------------|
| Dollar Volume | Price × 20-day avg volume >= $100M | ✓ | ✓ | yfinance daily |
| ADR% | mean((High − Low) / Close) over last 20 daily bars × 100 >= 3.5% | ✓ | ✓ | yfinance daily |
| Pre-market Gap Revalidation | (latest pre-market price − prev close) / prev close >= +5% | ✓ | — | **Futu** snapshot (`pre_change_rate`) → yfinance 1m prepost fallback |
| Intraday Cumulative Volume | Today's RTH cumulative volume since 9:30 ET >= 20-day average daily volume | — | ✓ | **Futu** snapshot (`volume`) → yfinance 1m fallback |

The intraday volume threshold (post-open only) is the key signal — by 10–30 min after open, the stock has already done a full day's worth of trading. Per Kullamägi: "the best ones have traded their average daily volume in the first 15–30 minutes after the open."

**Why ADR% instead of Finviz beta:** The earlier `ta_beta_o1.5` (beta > 1.5) was excluding mid/large-cap catalyst names (biotech, services with beta 1.0–1.3) that are actually "in-play" on a given session. Beta measures correlation with the broad market over years of history — orthogonal to whether a stock is currently moving on news. **The beta filter has been removed from every group and replaced by an ADR% threshold applied across Longs, Leaders, RS, Shorts, HK Shorts, and Morning Gap.** ADR% (Kullamägi-style) is the average of daily `(High − Low) / Close` over the last 20 completed sessions × 100; the global default is 3.5% and is configured once in `[settings]` (`min_adr_percent`, `adr_days`). Set `min_adr_percent = 0` in `[settings]` to disable globally. Shorts, HK Shorts, and Morning Gap also accept a per-section override of the same key if that group needs a different threshold.

**Why the pre-market candidate set is different:** Finviz's `Gap` column is `(today's regular-session open − yesterday's close) ÷ yesterday's close`. Before 9:30 ET the regular session hasn't opened, so Finviz still serves yesterday's gap value — a stock that gapped up ≥5% yesterday but is gapping down today still passes `ta_gap_u5`. To get a candidate set that reflects *today's* movement, the pre-market scan drops `ta_gap_u5` and adds `signal = ta_topgainers` (Finviz Top Gainers, updated in real time). The revalidation step then pulls each candidate's pre-market price from Futu OpenAPI (`get_market_snapshot.pre_change_rate`, real-time on US Lv1 BBO accounts) and re-computes the gap against yesterday's close, dropping anything below `min_pre_market_gap_percent` (default 5.0). yfinance's `prepost=True` 1m bars are the fallback when Futu OpenD is unreachable. Tickers with no pre-market trades yet (`pre_volume == 0`) are also dropped — they have no signal. Post-open scans keep the original `ta_gap_u5` filter because Finviz's `Gap` field reflects today's actual open by then.

**Why Futu OpenAPI for live data:** Both the pre-market gap revalidation and the post-open cumulative-volume filter are single-call snapshot lookups against a list of <30 candidates — Futu returns real-time pre/post fields and today's RTH cumulative `volume` in one network round-trip. yfinance's per-ticker 1m bar fetches are slower, hit rate limits, and frequently returned no data for valid pre-market gappers in past runs (logged as `yfinance 1m: failed to process <ticker>, dropping`). Futu requires US Lv1 BBO real-time quote permission on the OpenD account; without it the snapshot's pre/post fields return delayed/empty values and the filter would silently drop everything. The yfinance fallback runs whenever `[futu] enabled = false` or the OpenD TCP probe fails.

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

**Sync strategy:** Diff-based — fetches current group contents, then ADDs new tickers and DELs missing ones, minimizing API calls (Futu rate limit: 10 calls per 30s).

**Merged `EarningsGap` group (append-only):** The EOD `EarningsGap` scan, the pre-market `MorningGapPre` scan, and the post-open `MorningGap` scan all sync into the **same** Futu group, `EarningsGap`. Because three different scanners feed one group, `EarningsGap` is listed in `[futu] append_only_groups` — sync only ADDs tickers, never DELs, so each scanner doesn't clobber the others' contributions. Tickers accumulate across days; clear the group manually in the Futu client when it gets too crowded (Futu caps: 500 per group for non-traders, 2000 for active traders). The three `.txt` files (`EarningsGap.txt`, `MorningGapPre.txt`, `MorningGap.txt`) remain separate and unaffected.

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

**Schedule:** Tue–Sat 8:30 AM HKT = Mon–Fri after US market close. 8:30 AM HKT is safe for both EDT (4.5h after close) and EST (3.5h after close), and allows yfinance/Finviz EOD data to fully settle before the run — earlier times (e.g. 6 AM) can produce noisier results due to stale or partial data.

### How it works

1. **`pmset repeat`** wakes the Mac at 8:29 AM HKT (Tue–Sat)
2. **launchd** (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`) runs the script at 8:30 AM
3. After execution, the Mac automatically returns to sleep

### Setup

```bash
# Schedule Mac to wake at 8:29 AM Tue-Sat
sudo pmset repeat wakeorpoweron TWRFS 08:29:00

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
