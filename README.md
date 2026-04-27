# Finviz to TradingView

Automated stock screener that runs custom Finviz scans (US) and HKEX + yfinance scans (Hong Kong), exporting results as TradingView-importable watchlists and auto-syncing to Futu (富途牛牛) custom watchlist groups via OpenAPI.

## Screening Criteria

### Longs (5 strategies, each written to its own file)

Based on **Oliver Kell**'s momentum/breakout methodology. Each strategy outputs to its own `.txt` file and Futu group; tickers are mutually exclusive across the 5 strategies (priority order shown below — earlier wins).

| Priority | Strategy (file stem) | Key Filters |
|----|-----------------------|-------------|
| 1 | `EarningsGap` | Small Cap+, Earnings Today, Avg Vol > 500K, Price > $20, Rel Vol > 3 (Finviz), Beta > 1.5, Gap Up 5%+, Above SMA200 |
| 2 | `HighVolume` | Small Cap+, Avg Vol > 500K, Price > $20, Beta > 1.5, Day Up, Above SMA200, Rel Vol > 3x 20-day avg (via yfinance) |
| 3 | `GapUp` | Small Cap+, Avg Vol > 500K, Price > $20, Beta > 1.5, Gap Up 3%+, Above SMA200 |
| 4 | `NewHigh52W` | Small Cap+, Avg Vol > 1M, Price > $20, Beta > 1.5, New 52W High, Above SMA50 & SMA200 |
| 5 | `TopGainers` | Small Cap+, Avg Vol > 500K, Price > $20, Beta > 1.5, Above SMA200, Signal: Top Gainers |

All longs strategies also require **Dollar Volume >= $100M** (Price × 20-day avg volume, via yfinance). The "Avg Vol" filters above are Finviz pre-filters using Finviz's 3-month average to reduce result count before post-processing.

### Leaders (5 strategies, merged & deduplicated)

Long-term trend leaders trading above both SMA50 and SMA200. The five strategies share the same base filters but differ in the performance-window threshold:

**Shared base filters:** Small Cap+, Avg Vol > 500K, Price > $20, Beta > 1.5, Above SMA50, Above SMA200, Dollar Volume >= $100M (20-day avg, via yfinance).

| Strategy | Performance Threshold |
|----------|-----------------------|
| Leaders 4W +30% | 4-week performance >= 30% |
| Leaders 13W +50% | 13-week performance >= 50% |
| Leaders 26W +100% | 26-week performance >= 100% |
| Leaders YTD +100% | YTD performance >= 100% |
| Leaders 52W +200% | 52-week performance >= 200% |

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
| Performance (Large Cap ≥ $10B) | Up 50%+ over 2, 3, or 4 weeks |
| Performance (Mid Cap $2B–$10B) | Up 200%+ over 2, 3, or 4 weeks |
| Performance (Small Cap $300M–$2B) | Up 300%+ over 2, 3, or 4 weeks |
| Consecutive Up Days | 3+ consecutive green days (excludes today's incomplete data if market is still open) |

Performance is checked over 2-week (10 trading days), 3-week (15 trading days), and 4-week (22 trading days) windows via yfinance. A ticker passes if it meets the cap-conditional threshold in any window. Results are aggregated, then the 3+ consecutive up days filter is applied.

### RS - Relative Strength (conditional)

Based on **Oliver Kell**'s relative strength approach. Only runs when both SPY and QQQ drop more than 1.5% on the day — identifies stocks showing strength in a weak market.

| Strategy | Key Filters |
|----------|-------------|
| Relative Strength | Small Cap+, Avg Vol > 500K, Price > $20, Beta > 1.5, Day Up, Above SMA50 & SMA200, Dollar Volume >= $100M (via yfinance) |

### HK Shorts (1 strategy, multi-phase filtering)

Hong Kong market short candidates using the same methodology as US Shorts, sourced from **HKEX + yfinance** instead of Finviz.

**Phase 1 — HKEX universe + yfinance filtering:**

| Filter | Criteria |
|--------|----------|
| Universe | HKEX Main Board equities (~2,400 stocks) |
| SMA20 | Price 20%+ above 20-day moving average |
| Avg Volume | > 1M shares/day (20-day average) |

**Phase 2 — Post-processing:**

| Filter | Criteria |
|--------|----------|
| Market Cap | >= HKD 300M |
| Dollar Volume | Price × 20-day avg volume >= HKD 100M |
| Performance (Large Cap ≥ HKD 10B) | Up 50%+ over 2, 3, or 4 weeks |
| Performance (Mid Cap HKD 2B–10B) | Up 200%+ over 2, 3, or 4 weeks |
| Performance (Small Cap HKD 300M–2B) | Up 300%+ over 2, 3, or 4 weeks |
| Consecutive Up Days | 3+ consecutive green days |

HK tickers are output in `HKEX:XXXX` format for TradingView (e.g. `HKEX:0700`).

### Morning Gap (pre-market + intraday, 7 scans)

Two-phase scanner. **Pre-market (-20 / -10 min before US open)** writes to `MorningGapPre.txt` as an early candidate list — Finviz filters + dollar volume only, no intraday volume confirmation yet. **Post-open (+10 / +15 / +20 / +25 / +30 min)** writes to `MorningGap.txt` and adds the intraday cumulative-volume filter — captures stocks that have already traded their full daily average volume in the first 30 minutes, a signal of catalyst-driven institutional buying (earnings, FDA, M&A, sector news).

**Phase 1 — Finviz filters (both pre-market and post-open):**

| Filter | Criteria |
|--------|----------|
| Market Cap | Small Cap+ (>= $300M) |
| Avg Volume | > 500K |
| Price | > $10 |
| Beta | > 1.5 |
| Gap Up | >= 5% |
| SMA200 | Price above SMA200 |

**Phase 2 — Post-processing (via yfinance):**

| Filter | Criteria | Pre-market | Post-open |
|--------|----------|------------|-----------|
| Dollar Volume | Price × 20-day avg volume >= $100M | ✓ | ✓ |
| Intraday Cumulative Volume | Volume from 9:30 ET to 9:30+offset ET >= 20-day average daily volume | — | ✓ |

The intraday volume threshold (post-open only) is the key signal — by 10–30 min after open, the stock has already done a full day's worth of trading. Per Kullamägi: "the best ones have traded their average daily volume in the first 15–30 minutes after the open."

## Output

```
output/
├── US/
│   ├── 2026_04_27_EarningsGap.txt   # Longs strategy 1 (highest priority)
│   ├── 2026_04_27_HighVolume.txt    # Longs strategy 2 (Relative Volume Surge)
│   ├── 2026_04_27_GapUp.txt         # Longs strategy 3
│   ├── 2026_04_27_NewHigh52W.txt    # Longs strategy 4
│   ├── 2026_04_27_TopGainers.txt    # Longs strategy 5 (lowest priority)
│   ├── 2026_04_27_Leaders.txt       # US trend leaders
│   ├── 2026_04_27_Shorts.txt        # US short candidates
│   ├── 2026_04_27_RS.txt            # Relative strength (only on RS-eligible days)
│   ├── 2026_04_27_MorningGapPre.txt # Pre-market morning-gap candidates (-20/-10 min)
│   └── 2026_04_27_MorningGap.txt    # Post-open morning-gap snapshot (+10..+30 min)
└── HK/
    └── 2026_04_27_Shorts.txt        # HK short candidates
```

Each run writes a single date-stamped file per group. The 5 Longs strategies are mutually exclusive (priority `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers`); their union is then deduped against Leaders and RS (`Longs > Leaders > RS`), so each ticker appears in exactly one of the 7 long-side files. Files are comma-separated ticker symbols, ready for TradingView import.

**Drop-guard safety:** `safe_write_watchlist` compares today's count against the most recent prior dated file — if the count drops by more than 50%, the new file is **not** written and the previous day's file is preserved. This protects against silent rate-limiting or data-source failures. Morning-gap intra-day scans compare against the same day's earlier scan.

### Futu (富途牛牛) Auto-Sync

After each successful watchlist write, the script can sync tickers to a Futu custom watchlist group via OpenAPI. Configured via `[futu]` in `config.toml`. The `.txt` files remain the primary output — Futu sync failures (OpenD not running, group missing, etc.) only log a warning.

**Prerequisites:**
1. Download & launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in with your Futu account (default port `11111`).
2. In the Futu PC client, manually create the custom watchlist groups: `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts`, `MorningGap`, `MorningGapPre` (the API can only modify custom groups, not create them).
3. Set `enabled = true` in `[futu]` (already on by default).

**Sync strategy:** Diff-based — fetches current group contents, then ADDs new tickers and DELs missing ones, minimizing API calls (Futu rate limit: 10 calls per 30s).

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
4. Choose the latest dated file, e.g. `output/US/2026_04_27_HighVolume.txt` (or `EarningsGap` / `GapUp` / `NewHigh52W` / `TopGainers` / `Leaders` / `Shorts` / `RS` / `MorningGap` / `MorningGapPre` for US, `output/HK/2026_04_27_Shorts.txt` for HK)

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
