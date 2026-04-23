# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                # Install dependencies
uv run main.py         # Run all screeners and generate watchlists
```

## Architecture

Single-file Python tool (`main.py`) that scrapes Finviz stock screeners and outputs TradingView-importable `.txt` watchlist files.

**Flow:** Load `config.toml` → Run screener groups sequentially → Deduplicate → Write output files

**Three screener groups with different output behavior:**
- **Longs** (`[[longs]]` in config): 4 strategies merged into one deduplicated set. Based on Oliver Kell's methodology.
- **Shorts** (`[shorts]`): Single strategy with multi-phase filtering, output separately. Based on Kristjan Kullamägi's blog criteria. Runs Finviz filters (SMA20+20%, avg vol >1M, cap >$300M), then post-processes for dollar volume, cap-conditional monthly performance thresholds, and consecutive up days (via yfinance).
- **RS** (`[rs]`): Conditional — only runs when both SPY and QQQ drop >1.5% (checked via `finviz.get_stock()`). Based on Oliver Kell's relative strength approach.

**Key mechanisms:**
- `safe_write_watchlist()`: Protects against Finviz rate limiting — if new result count drops >50% vs existing file, the write is skipped and old file preserved.
- Each run writes both a latest file (`Longs.txt`) and a date-stamped archive (`2026_04_21_Longs.txt`). The latest file is used for safe_write comparison.
- 8-second delay between requests to avoid Finviz rate limiting (configurable in `config.toml`).

**Config format:** TOML. Filter strings (e.g. `sh_avgvol_o500`) map directly to Finviz URL parameters. The `signal` field is optional (used for Top Gainers).

**Scheduling:** Runs Tue-Sat 6:00 AM HKT via launchd (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`). Mac wakes at 5:59 AM via `pmset repeat`. Covers US Mon-Fri market close in both EDT and EST.

## Finviz Library

Uses `finviz` package (web scraping, no API key needed):
- `Screener(filters=[...], signal=...)` → `.data` returns list of dicts with `"Ticker"` key
- `get_stock("SPY")` → dict with `"Change"` field as string like `"-1.23%"`
