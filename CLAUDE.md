# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                # Install dependencies
uv run main.py         # Run all screeners and generate watchlists
```

## Architecture

Single-file Python tool (`main.py`) that scrapes Finviz stock screeners (US) and HKEX + yfinance (HK), outputting TradingView-importable `.txt` watchlist files.

**Flow:** Load `config.toml` → Run screener groups sequentially → Deduplicate → Write output files to `output/US/` and `output/HK/`

**Four screener groups with different output behavior:**
- **Longs** (`[[longs]]` in config): 4 strategies merged into one deduplicated set → `output/US/Longs.txt`. Based on Oliver Kell's methodology. Relative Volume Surge uses yfinance post-processing for 20-day relative volume (configurable via `min_relative_volume` and `relative_volume_days` per strategy).
- **Shorts** (`[shorts]`): Single strategy with multi-phase filtering → `output/US/Shorts.txt`. Based on Kristjan Kullamägi's blog criteria. Runs Finviz Ownership screener (SMA20+20%, avg vol >1M, cap >$300M) for market cap data, then post-processes via yfinance for cap-conditional performance (2/3/4-week windows: 10, 15, 22 trading days), dollar volume, and consecutive up days.
- **RS** (`[rs]`): Conditional → `output/US/RS.txt`. Only runs when both SPY and QQQ drop >1.5% (checked via `finviz.get_stock()`). Based on Oliver Kell's relative strength approach.
- **HK Shorts** (`[hk_shorts]`): Hong Kong market short candidates → `output/HK/Shorts.txt`. Same methodology as US Shorts but sources data from HKEX securities list + yfinance. Uses HKD-native cap thresholds. Batch-downloads ~2,400 tickers in groups of 500.

**Key mechanisms:**
- `safe_write_watchlist()`: Protects against data source issues — if new result count drops >50% vs existing file, the write is skipped and old file preserved.
- Each run writes both a latest file (e.g. `Shorts.txt`) and a date-stamped archive (e.g. `2026_04_21_Shorts.txt`). The latest file is used for safe_write comparison.
- 8-second delay between Finviz requests to avoid rate limiting (configurable in `config.toml`).

**Config format:** TOML. Filter strings (e.g. `sh_avgvol_o500`) map directly to Finviz URL parameters. The `signal` field is optional (used for Top Gainers).

**Scheduling:** Runs Tue-Sat 6:00 AM HKT via launchd (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`). Mac wakes at 5:59 AM via `pmset repeat`. Covers US Mon-Fri market close in both EDT and EST.

## Finviz Library

Uses `finviz` package (web scraping, no API key needed):
- `Screener(filters=[...], signal=...)` → `.data` returns list of dicts with `"Ticker"` key
- `get_stock("SPY")` → dict with `"Change"` field as string like `"-1.23%"`
