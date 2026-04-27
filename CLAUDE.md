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

**Five screener groups with different output behavior:**
- **Longs** (`[[longs]]` in config): 5 strategies, each written to its **own** file (no merged `Longs.txt`). Config list order = priority for internal mutual-exclusion dedup: `EarningsGap > HighVolume (Relative Volume Surge) > GapUp > NewHigh52W > TopGainers` → `output/US/{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers}.txt`. Each `[[longs]]` entry has a `key` field; the matching `longs_<key>` entry under `[futu.groups]` supplies both the Futu group name and the .txt filename stem. The union of all 5 acts as a "virtual Longs" for the cross-group `Longs > Leaders > RS` dedup. Based on Oliver Kell's methodology. HighVolume uses yfinance post-processing for 20-day relative volume (configurable via `min_relative_volume` and `relative_volume_days`).
- **Leaders** (`[[leaders]]`): 5 strategies sharing a base filter set (cap_smallover, avg vol >500K, price >$20, beta >1.5, above SMA50/SMA200) but differing in performance window (4w/13w/26w/YTD/52w), merged → `output/US/Leaders.txt`. Global `min_dollar_volume` ($100M, 20-day avg) applies.
- **Shorts** (`[shorts]`): Single strategy with multi-phase filtering → `output/US/Shorts.txt`. Based on Kristjan Kullamägi's blog criteria. Runs Finviz Ownership screener (SMA20+20%, avg vol >1M, cap >$300M) for market cap data, then post-processes via yfinance for cap-conditional performance (2/3/4-week windows: 10, 15, 22 trading days), dollar volume, and consecutive up days.
- **RS** (`[rs]`): Conditional → `output/US/RS.txt`. Only runs when both SPY and QQQ drop >1.5% (checked via `finviz.get_stock()`). Based on Oliver Kell's relative strength approach.
- **HK Shorts** (`[hk_shorts]`): Hong Kong market short candidates → `output/HK/Shorts.txt`. Same methodology as US Shorts but sources data from HKEX securities list + yfinance. Uses HKD-native cap thresholds. Batch-downloads ~2,400 tickers in groups of 500.

**Key mechanisms:**
- Each run writes **only** date-stamped files (e.g. `2026_04_21_Shorts.txt`). There is no un-dated "latest" copy.
- `safe_write_watchlist(tickers, output_path, baseline_path=...)`: drop-guard for catastrophic data-source failures. Compares today's count against `baseline_path` (or `output_path` itself if not given) — if the new count drops by more than 50%, the write is skipped and the previous file is kept. EOD groups pass `baseline_path=_previous_dated_file(...)` so they compare against yesterday's archive. Morning-gap omits the baseline so it compares within the same day's earlier scan.
- `_previous_dated_file(directory, today_prefix, suffix)`: returns the lexically-greatest `*<suffix>` file in `directory` excluding today's. Filenames use `YYYY_MM_DD_<group>.txt` so lex order = chronological order.
- **Cross-group dedup (Longs/Leaders/RS)**: Two layers. (1) Within Longs, the 5 strategies are deduped by config-list order — earlier wins. (2) After all three long-side groups have been collected, the Longs union is deduped against Leaders and RS with priority `Longs(union) > Leaders > RS` so each ticker appears in exactly one of the 7 long-side files (5 Longs splits + Leaders + RS) per run. The collection-then-write split means all Longs splits, Leaders, and RS files are written only after RS has finished. Shorts and HK Shorts are independent and written inline.
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
- Hooks fire after every `safe_write_watchlist` of the dated file in `main.py` — one call per group: each Longs split (EarningsGap/HighVolume/GapUp/NewHigh52W/TopGainers), Leaders, Shorts, RS, HKShorts, MorningGap.
- `_futu_sync(config, key, tickers, market)` helper in `main.py` is a no-op when `[futu] enabled = false` or the group isn't mapped, so the EOD/morning-gap pipelines work identically with or without OpenD running.
- `sync_to_futu()` is **diff-based**: calls `get_user_security(group_name)` for current contents, computes set diff, then issues at most one `DEL` and one `ADD` (under the 10-call/30s API rate limit).

**Prerequisites (must be done by the user, once):**
1. Install & launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in with the user's Futu account. Default listens on `127.0.0.1:11111`.
2. In the Futu PC client, manually create the 10 custom watchlist groups: `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts`, `MorningGap`. **The API cannot create groups — it can only modify existing custom groups.**

**Config (`[futu]` in `config.toml`):**
```toml
[futu]
enabled = true
host = "127.0.0.1"
port = 11111

[futu.groups]
longs_earnings_gap = "EarningsGap"
longs_high_volume = "HighVolume"
longs_gap_up = "GapUp"
longs_new_high_52w = "NewHigh52W"
longs_top_gainers = "TopGainers"
shorts = "Shorts"
rs = "RS"
hk_shorts = "HKShorts"
morning_gap = "MorningGap"
leaders = "Leaders"
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
