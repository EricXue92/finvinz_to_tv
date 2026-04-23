# HK Shorts Screener — Design Spec

## Goal

Add a Hong Kong market short candidates screener to the existing Finviz-to-TradingView tool. Replicates the US Shorts strategy logic but sources data from HKEX + yfinance instead of Finviz.

## Data Source: HKEX Securities List

**URL:** `https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx`

- Official HKEX xlsx file, updated daily, ~18,000 rows covering all listed securities.
- Filter to `Category == "Equity"` to get ~2,752 equities.
- Further filter to `Sub-Category == "Equity Securities (Main Board)"` (~2,413 stocks). GEM board stocks are excluded (too illiquid for shorting).
- Stock codes are 5-digit zero-padded strings (e.g., `"00700"`).
- **New dependency:** `openpyxl` to parse the xlsx.

## Pipeline

The HK Shorts pipeline mirrors the US Shorts pipeline but replaces Finviz with HKEX + yfinance for all filtering:

### Step 1 — Fetch universe

Download the HKEX xlsx, parse with openpyxl, extract Main Board equity stock codes.

### Step 2 — Batch download yfinance data

Download 2 months of daily OHLCV data for all ~2,400 Main Board tickers via `yf.download()` with `threads=True`. Estimated time: ~6 minutes. yfinance ticker format: `{code}.HK` where code strips one leading zero from the 5-digit HKEX code (e.g., `00700` -> `0700.HK`).

### Step 3 — Initial filters (replaces Finviz screener step)

From the downloaded data, apply:

| Filter | US Shorts (Finviz) | HK Shorts (yfinance) |
|---|---|---|
| Price above SMA20 by 20% | `ta_sma20_pa20` | `close > SMA20 * 1.2` |
| Avg daily volume | `sh_avgvol_o1000` (>1M) | configurable, default 1M shares |
| Market cap | `cap_smallover` (>$300M) | `yf.Ticker().fast_info.market_cap`, configurable default in HKD |

This step reduces ~2,400 stocks to a much smaller set (likely ~50-200).

### Step 4 — Post-processing (same logic as US)

Applied to the filtered subset:

1. **Dollar volume filter** — price * 20-day avg volume >= threshold (configurable, default 100M HKD)
2. **Cap-conditional monthly performance** — same tiered logic as US (large/mid/small cap thresholds, configurable)
3. **Consecutive up days** — filter for stocks with N+ consecutive up days (configurable, default 3)

### Step 5 — Output

- Ticker format: `HKEX:{code}` (e.g., `HKEX:0700`) for TradingView compatibility.
- Output directory: `output/HongKongShorts/`
- Files: `HK_Shorts.txt` (latest) + `{date}_HK_Shorts.txt` (archive)
- Same `safe_write_watchlist()` protection as US.
- Same configurable format (comma or newline).

## Market Hours Handling

- HK market timezone: `Asia/Hong_Kong`
- HK market closes at 4:00 PM HKT
- When market is open, exclude today's incomplete data from yfinance (same logic as US but with HK timezone)

## Config

New `[hk_shorts]` section in `config.toml`:

```toml
[hk_shorts]
name = "HK Short Candidates"
min_avg_volume = 1_000_000        # shares/day
min_market_cap = 2_000_000_000    # HKD (~$300M USD equivalent)
min_dollar_volume = 100_000_000   # HKD
min_consecutive_up_days = 3
# Cap-conditional monthly perf thresholds (%)
perf_large_cap = 50               # >= HKD 80B (~$10B USD)
perf_mid_cap = 200                # HKD 16B-80B (~$2B-$10B USD)
perf_small_cap = 300              # < HKD 16B
# Cap boundaries in HKD
large_cap_threshold = 80_000_000_000
mid_cap_threshold = 16_000_000_000
```

## Code Changes

All changes in `main.py`:

1. **New function `fetch_hkex_equities()`** — downloads xlsx, parses with openpyxl, returns list of Main Board equity codes.
2. **New function `filter_hk_shorts()`** — orchestrates the full HK pipeline: fetch universe, batch yfinance download, apply SMA20/volume/cap filters, then dollar volume/performance/up-days post-processing.
3. **New section in `main()`** — after US Shorts, run HK Shorts if `[hk_shorts]` config exists.
4. **Reuse existing functions** — `filter_dollar_volume_yf()`, `filter_consecutive_up_days()`, `safe_write_watchlist()`, `parse_number()`.

## Dependencies

- Add `openpyxl` via `uv add openpyxl`

## Scheduling

Runs on the same schedule as US (Tue-Sat 6:00 AM HKT). HK market closes at 4 PM HKT the previous day, so data is finalized by run time.
