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

**Scheduling:** Runs Tue-Sat 8:30 AM HKT via launchd (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`). Mac wakes at 8:29 AM via `pmset repeat`. Covers US Mon-Fri market close in both EDT and EST. Later time (vs earlier 6:00 AM) lets yfinance/Finviz EOD data settle before the run.

## Finviz Library

Uses `finviz` package (web scraping, no API key needed):
- `Screener(filters=[...], signal=...)` → `.data` returns list of dicts with `"Ticker"` key
- `get_stock("SPY")` → dict with `"Change"` field as string like `"-1.23%"`

## Futu (富途牛牛) OpenAPI Integration

`futu_sync.py` mirrors each successfully-written watchlist into a Futu custom watchlist group via the `futu-api` SDK. The `.txt` files remain the primary artifact — Futu sync is a soft side-effect that logs a warning on any failure and never raises.

**Architecture:**
- Hooks fire after every `safe_write_watchlist` of the *latest* file (not the dated archive) in `main.py` — one call per group: Longs, Shorts, RS, HKShorts, MorningGap.
- `_futu_sync(config, key, tickers, market)` helper in `main.py` is a no-op when `[futu] enabled = false` or the group isn't mapped, so the EOD/morning-gap pipelines work identically with or without OpenD running.
- `sync_to_futu()` is **diff-based**: calls `get_user_security(group_name)` for current contents, computes set diff, then issues at most one `DEL` and one `ADD` (under the 10-call/30s API rate limit).

**Prerequisites (must be done by the user, once):**
1. Install & launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in with the user's Futu account. Default listens on `127.0.0.1:11111`.
2. In the Futu PC client, manually create the 5 custom watchlist groups: `Longs`, `Shorts`, `RS`, `HKShorts`, `MorningGap`. **The API cannot create groups — it can only modify existing custom groups.**

**Config (`[futu]` in `config.toml`):**
```toml
[futu]
enabled = true
host = "127.0.0.1"
port = 11111

[futu.groups]
longs = "Longs"
shorts = "Shorts"
rs = "RS"
hk_shorts = "HKShorts"
morning_gap = "MorningGap"
```

**Ticker format conversion (`_to_futu_code`):**
- US: `AAPL` → `US.AAPL`
- HK: `HKEX:0522` / `522` / `0522.HK` → `HK.00522` (5-digit zero-padded)

**Robustness:**
- TCP probe (`_opend_reachable`, 1.5s timeout) runs before invoking `OpenQuoteContext` — without it, the SDK retries forever on `ECONNREFUSED` instead of raising. **Do not remove this probe.**
- All exceptions inside `sync_to_futu` are caught; failures log a warning and return `False`.

**Futu API limits to remember:**
- 10 `modify_user_security` calls per 30 seconds
- 500 tickers in "all" watchlist for untraded users; 2000 for active traders
- Cannot modify system groups (e.g. "全部"), only user-created custom groups
