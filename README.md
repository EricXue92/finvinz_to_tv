# Finviz to TradingView

Automated stock screener that runs custom Finviz scans (US) and HKEX + yfinance scans (Hong Kong), exporting results as TradingView-importable watchlists.

## Screening Criteria

### Longs (4 strategies, merged & deduplicated)

Based on **Oliver Kell**'s momentum/breakout methodology:

| Strategy | Key Filters |
|----------|-------------|
| Relative Volume Surge | Avg Vol > 500K, Price > $20, Beta > 1.5, Day Up, Above SMA200, Rel Vol > 3x 20-day avg (via yfinance) |
| Top Gainers | Avg Vol > 500K, Price > $20, Beta > 1.5, Above SMA200, Signal: Top Gainers |
| Gap Up | Avg Vol > 500K, Price > $20, Beta > 1.5, Gap Up 3%+, Above SMA200 |
| 52W New High | Small Cap+, Avg Vol > 1M, Price > $20, Beta > 1.5, New 52W High, Above SMA50 & SMA200 |

All longs strategies also require **Dollar Volume >= $100M** (Price × 20-day avg volume, via yfinance). The "Avg Vol" filters above are Finviz pre-filters using Finviz's 3-month average to reduce result count before post-processing.

### Shorts (1 strategy, multi-phase filtering)

Based on **Kristjan Kullamägi**'s short-selling criteria:

**Phase 1 — Finviz filters:**

| Filter | Criteria |
|--------|----------|
| SMA20 | Price 20%+ above 20-day moving average |
| Avg Volume | > 1M shares (Finviz 3-month avg, pre-filter) |
| Market Cap | > $300M (small cap and above) |

**Phase 2 — Post-processing:**

| Filter | Criteria |
|--------|----------|
| Dollar Volume | Price × 20-day avg volume >= $100M (via yfinance) |
| Performance (Large Cap ≥ $10B) | Up 50%+ over 2, 3, or 4 weeks |
| Performance (Mid Cap $2B–$10B) | Up 200%+ over 2, 3, or 4 weeks |
| Performance (Small Cap $300M–$2B) | Up 300%+ over 2, 3, or 4 weeks |
| Consecutive Up Days | 3+ consecutive green days (via yfinance; excludes today's incomplete data if market is still open) |

Performance is checked over 4-week window via Finviz first, then 2-week and 3-week windows via yfinance for tickers that didn't pass the 4-week check. Results are aggregated.

### RS - Relative Strength (conditional)

Based on **Oliver Kell**'s relative strength approach. Only runs when both SPY and QQQ drop more than 1.5% on the day — identifies stocks showing strength in a weak market.

| Strategy | Key Filters |
|----------|-------------|
| Relative Strength | Avg Vol > 500K, Price > $20, Beta > 1.5, Day Up, Above SMA50 & SMA200 |

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
| Market Cap | >= HKD 3 billion (~$300M USD) |
| Dollar Volume | Price × 20-day avg volume >= HKD 100M |
| Performance (Large Cap ≥ HKD 10B) | Up 50%+ over 2, 3, or 4 weeks |
| Performance (Mid Cap HKD 2B–10B) | Up 200%+ over 2, 3, or 4 weeks |
| Performance (Small Cap HKD 300M–2B) | Up 300%+ over 2, 3, or 4 weeks |
| Consecutive Up Days | 3+ consecutive green days |

HK tickers are output in `HKEX:XXXX` format for TradingView (e.g. `HKEX:0700`).

## Output

```
output/
├── US/
│   ├── Longs.txt              # Latest US long candidates
│   ├── Shorts.txt             # Latest US short candidates
│   ├── RS.txt                 # Latest relative strength (conditional)
│   ├── 2026_04_21_Longs.txt   # Date-stamped archives
│   ├── 2026_04_21_Shorts.txt
│   └── 2026_04_21_RS.txt
└── HK/
    ├── Shorts.txt             # Latest HK short candidates
    └── 2026_04_21_Shorts.txt
```

Each run generates both a latest file (e.g. `Shorts.txt`) and a date-stamped copy to preserve history. Files are comma-separated ticker symbols, ready for TradingView import.

## Setup

```bash
# Install dependencies
uv sync

# Run manually
uv run main.py
```

## Import to TradingView

1. Open TradingView
2. Right panel -> Watchlist -> Click the list name
3. Select "Import list..."
4. Choose `output/US/Longs.txt` (or Shorts/RS for US, `output/HK/Shorts.txt` for HK)

## Automation (launchd + pmset)

The script runs daily after US market close via macOS launchd, with `pmset` to wake the Mac from sleep.

**Schedule:** Tue-Sat 6:00 AM HKT = Mon-Fri after US market close. 6 AM HKT is safe for both EDT (2h after close) and EST (1h after close).

### How it works

1. **`pmset repeat`** wakes the Mac at 5:59 AM HKT (Tue-Sat)
2. **launchd** (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`) runs the script at 6:00 AM
3. After execution, the Mac automatically returns to sleep

### Setup

```bash
# Schedule Mac to wake at 5:59 AM Tue-Sat
sudo pmset repeat wakeorpoweron TWRFS 05:59:00

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

> **Note:** Unlike cron, launchd will catch up on missed runs — if the Mac was asleep at 6:00 AM, the task executes as soon as the Mac wakes up.

## Configuration

All screener parameters are in `config.toml`. You can modify filters, add new screeners, or adjust settings (delay between requests, output format) without touching the code.

## Dependencies

- Python >= 3.12
- [finviz](https://github.com/mariostoev/finviz) - Finviz web scraper (no API key or premium account required)
- [yfinance](https://github.com/ranaroussi/yfinance) - Yahoo Finance data for post-processing filters and HK market data
- [openpyxl](https://openpyxl.readthedocs.io/) - HKEX securities list xlsx parsing
