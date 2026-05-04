# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                              # Install dependencies
uv run main.py                       # EOD pipeline (Longs/Leaders/Shorts/RS/IPO/HK Shorts)
uv run main.py --mode morning-gap    # intraday gap scan; auto-detects ET window, exits clean outside
```

## Architecture

Python tool: `main.py` (entry point + all EOD/morning-gap pipelines), `rs_rating.py` (IBD RS table fetcher), `futu_sync.py` (Futu OpenAPI mirror), `notify.py` (ntfy push for morning-gap). Scrapes Finviz (US EOD), HKEX + yfinance (HK), and Futu snapshots (intraday gaps), outputting TradingView- and Webull-importable `.txt` watchlists.

**Flow:** Load `config.toml` → Run screener groups sequentially → Deduplicate → Write output files to `output/TV/US/` and `output/TV/HK/` (TradingView format) and mirror to `output/Webull/{US,HK}/` (newline-separated, for Webull import)

**Seven screener groups** (six EOD + one intraday-only):
- **Longs** (`[[longs]]` in config): 5 strategies, each written to its **own** file (no merged `Longs.txt`). Config list order = priority for internal mutual-exclusion dedup: `EarningsGap > HighVolume (Relative Volume Surge) > GapUp > NewHigh52W > TopGainers` → `output/TV/US/{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers}.txt`. Each `[[longs]]` entry has a `key` field; the matching `longs_<key>` entry under `[futu.groups]` supplies both the Futu group name and the .txt filename stem. The union of all 5 acts as a "virtual Longs" for the cross-group `Longs > Leaders > RS` dedup. Based on Oliver Kell's methodology. HighVolume uses yfinance post-processing for 20-day relative volume (configurable via `min_relative_volume` and `relative_volume_days`).
- **Leaders** (`[[leaders]]`): 5 strategies sharing a base filter set (cap_smallover, avg vol >500K, price >$20, above SMA50/SMA200) but differing in performance window (4w/13w/26w/YTD/52w), merged → `output/TV/US/Leaders.txt`. Global `min_dollar_volume` ($100M, 20-day avg) and `min_adr_percent` (>= 4.0%, 20-day) apply. The legacy `ta_beta_o1.5` filter has been removed across every Finviz group; ADR% is the replacement.
- **Shorts** (`[shorts]`): Single strategy with multi-phase filtering → `output/TV/US/Shorts.txt`. Based on Kristjan Kullamägi's blog criteria. Runs Finviz Ownership screener (SMA20+20%, avg vol >1M, cap >$300M) for market cap data, then post-processes via yfinance for cap-conditional performance (2/3/4-week windows: 10, 15, 22 trading days), dollar volume, and consecutive up days.
- **RS** (`[rs]`): Conditional → `output/TV/US/RS.txt`. Only runs when both SPY and QQQ drop >1.5% (checked via `finviz.get_stock()`). Based on Oliver Kell's relative strength approach.
- **IPO** (no config): Auto-collected sidecar of the long-side pipeline → `output/TV/US/<date>_IPO.txt`. Tickers that pass any long-side Finviz screener (Longs/Leaders/RS) but get dropped by yfinance for missing/insufficient daily history (typically the "insufficient data" / "failed to process" warnings). These are almost always fresh IPOs that lack the 20+ daily bars needed for DV/ADR/RVol. Has its own append-only Futu group `IPO` and its own cross-day master `output/state/eod_seen_IPO.txt` — the IPO master is independent of `eod_seen_US.txt`, so a ticker that ages into having enough yfinance data still lands in its proper long-side group on the first qualifying day. ADR% < min and dollar-volume < min rejections are NOT IPO drops (they're real filter rejections, not data gaps).
- **HK Shorts** (`[hk_shorts]`): Hong Kong market short candidates → `output/TV/HK/Shorts.txt`. Same methodology as US Shorts but sources data from HKEX securities list + yfinance. Uses HKD-native cap thresholds. Batch-downloads ~2,400 tickers in groups of 500.
- **Morning Gap** (`--mode morning-gap` only; `[morning_gap]` config): Intraday gap-up scanner. **Pre-market** (-20/-10 min) → `MorningGapPre.txt`; **post-open** (+10/+15/+20/+25/+30 min) → `MorningGap.txt`. Phase 1 = Futu snapshot universe scan (NASDAQ/NYSE/AMEX, cap ≥ $300M, price ≥ $10, gap ≥ 5%). Phase 2 = yfinance DV/ADR/SMA/avg-vol + Futu intraday cumulative volume (post-open only; ≥ 20-day avg daily volume). Per-day seen file at `output/state/morning_gap_seen_<date>.txt`. Both files merge into the append-only `EarningsGap` Futu group. **Requires OpenD** — no Finviz fallback. Each scan that surfaces NEW tickers triggers an ntfy push via `notify.py`.

**Key mechanisms:**
- Each run writes **only** date-stamped files (e.g. `2026_04_21_Shorts.txt`). There is no un-dated "latest" copy.
- Every dated write is mirrored to `output/Webull/{US,HK}/<same-filename>.txt` as **one ticker per line** (newline-separated, no exchange prefix change). Webull's "Upload as File" silently truncates comma-separated lists after the first 1-2 entries — the newline mirror is what you upload there. The TradingView `.txt` in `output/TV/{US,HK}/` stays comma-separated. `_write_webull(tickers, dated_path, output_dir)` runs after each `write_watchlist` call.
- `write_watchlist(tickers, output_path, fmt)`: unconditional writer. Always writes the dated file, even when `tickers` is empty (produces a 0-byte file for the day). No drop-guard / baseline comparison — every run leaves an artifact and Futu syncs to whatever was just written.
- **Cross-group dedup (Longs/Leaders/RS)**: Two layers. (1) Within Longs, the 5 strategies are deduped by config-list order — earlier wins. (2) After all three long-side groups have been collected, the Longs union is deduped against Leaders and RS with priority `Longs(union) > Leaders > RS` so each ticker appears in exactly one of the 7 long-side files (5 Longs splits + Leaders + RS) per run. The collection-then-write split means all Longs splits, Leaders, and RS files are written only after RS has finished. Shorts and HK Shorts are independent and written inline.
- **Cross-day master dedup** (`output/state/eod_seen_{US,HK,IPO}.txt`, implemented in `_dedup_seen`): applied to long-side EOD groups (5 Longs splits + Leaders + RS) AFTER within-day priority dedup. Each daily output = within-day survivors **minus** master; new survivors append to master. Net effect: every long-side ticker enters exactly ONE of the 7 EOD groups on first sighting and never reappears in any long-side `.txt` / Webull / Futu push. Reset by deleting the file (manual only).
  - **IPO has its own master** `eod_seen_IPO.txt`, independent of `eod_seen_US.txt` — a ticker collected as an IPO drop today still lands in its proper long-side group on the first day it has enough yfinance history.
  - **Excluded**: Shorts, HK Shorts, Morning Gap. Short setups are time-sensitive (parabolic blow-off can re-qualify weeks later), so re-detection is meaningful and a Shorts hit today does NOT suppress a future Longs hit on the same ticker. Morning Gap uses its own per-day seen file `output/state/morning_gap_seen_<date>.txt`.
  - **Futu side**: long-side groups are in `[futu] append_only_groups` so they accumulate monotonically — the dated `.txt` records "today's NEW additions"; the Futu group records the all-time union. Shorts/HKShorts are also append-only on Futu but the daily `.txt` contains every Finviz-detected short for the day (including re-detections).
- 8-second delay between Finviz requests to avoid rate limiting (configurable in `config.toml`).

**Config format:** TOML. Filter strings (e.g. `sh_avgvol_o500`) map directly to Finviz URL parameters. The `signal` field is optional (used for Top Gainers).

**Scheduling (EOD):** Runs Tue-Sat 10:00 AM HKT via launchd (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`). Mac wakes at 9:59 AM via `pmset repeat wakepoweron TWRFS 9:59:00` (sudo). The 10:00 slot covers US Mon-Fri market close in both EDT and EST AND lands after the daily Fred6725/rs-log RS Rating commit (worst-observed lag: ~01:31 UTC = 09:31 HKT) so the IBD RS table is fresh when Longs/Leaders read it.

**Scheduling (morning-gap):** `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist` fires 70 calendar entries/week (Mon-Fri × 7 ET offsets × EDT/EST). One-shot pmset wakes are scheduled by `sudo uv run scripts/schedule_morning_gap_wakes.py` (re-run weekly). The script self-validates ET on each trigger and exits cleanly outside any window — DO NOT add a hard error path here; missed wakes are silent by design.

## IBD Relative Strength Rating

`rs_rating.py` pulls the daily IBD-style RS percentile table (0-99) from `Fred6725/rs-log/output/rs_stocks.csv` (the published artifact of the [Fred6725/relative-strength](https://github.com/Fred6725/relative-strength) GitHub Action) and exposes a filter applied to Leaders **right after `run_screener`** — placed before yfinance dollar-volume so a 90+ gate cuts ~80-90% of tickers before any expensive batch download. **Longs are intentionally NOT RS-gated**: the long-side strategies (EarningsGap, HighVolume, GapUp, NewHigh52W, TopGainers) target setups (gap-ups, earnings reactions, volume surges) where the catalyst itself qualifies the name, and a 90+ RS filter would prune fresh breakouts that haven't built a 12-month track record yet.

- **Algorithm**: `RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` normalised against SPY's same-formula score, then percentile-ranked across ~6100 NYSE/NASDAQ stocks (ETFs and test issues excluded). All 0-99 percentiles are pre-computed in the CSV — we don't recompute anything.
- **Caching**: First call per day downloads to `output/state/rs_rating_<date>.csv`; subsequent calls (e.g. ad-hoc re-runs) read the cache.
- **Failure mode**: If the fetch fails (network, GitHub outage, schema change) the loaded table is `None` and every `filter_by_rs` call becomes a one-line warning + passthrough. The pipeline still produces output; it just skips the RS gate for the day. **Do not turn this into a hard failure** — the .txt files are the primary artifact.
- **Missing tickers**: Tickers not in the CSV (recent IPOs lacking 12mo history, foreign listings) are KEPT, not dropped — silently dropping them would surprise the user and over-prune new momentum names.
- **Config**: `[settings] min_rs_percentile = 90`. Set to 0 to disable entirely (skips the fetch too).
- **Scope**: Leaders only. Longs splits are not RS-gated (catalyst-driven setups). Shorts (parabolic blow-offs are by definition high-RS) and HK Shorts are not filtered. The conditional `[rs]` group is also unfiltered — its purpose is to find relative-strength names on a weak market day, which already overlaps the IBD definition.

## Finviz Library

Uses `finviz` package (web scraping, no API key needed):
- `Screener(filters=[...], signal=...)` → `.data` returns list of dicts with `"Ticker"` key
- `get_stock("SPY")` → dict with `"Change"` field as string like `"-1.23%"`

## Futu (富途牛牛) OpenAPI Integration

`futu_sync.py` mirrors each successfully-written watchlist into a Futu custom watchlist group via the `futu-api` SDK. The `.txt` files remain the primary artifact — Futu sync is a soft side-effect that logs a warning on any failure and never raises.

**Architecture:**
- Hooks fire after every `write_watchlist` of the dated file in `main.py` — one call per group: each Longs split (EarningsGap/HighVolume/GapUp/NewHigh52W/TopGainers), Leaders, Shorts, RS, IPO, HKShorts, MorningGap, MorningGapPre. Empty result = empty .txt **but Futu sync is skipped** so the existing Futu group is preserved (handled by `_futu_sync` early-return on empty `tickers`).
- `_futu_sync(config, key, tickers, market)` helper in `main.py` is a no-op when `[futu] enabled = false` or the group isn't mapped, so the EOD/morning-gap pipelines work identically with or without OpenD running.
- `sync_to_futu()` is **diff-based**: calls `get_user_security(group_name)` for current contents, computes set diff, then issues at most one `DEL` and one `ADD` (under the 10-call/30s API rate limit).
- **Append-only / merged groups**: When a group is listed in `[futu] append_only_groups`, `sync_to_futu(append_only=True)` skips the DEL phase — tickers only accumulate. **All EOD groups** (`EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts`, `IPO`) are append-only to pair with the cross-day master dedup. The merged `EarningsGap` group additionally receives `morning_gap` + `morning_gap_pre`. Groups grow monotonically across runs and must be cleared manually in the Futu client when they get too crowded (Futu cap: 500 per group for non-traders, 2000 for active traders).

**Prerequisites (must be done by the user, once):**
1. Install & launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in with the user's Futu account. Default listens on `127.0.0.1:11111`.
2. In the Futu PC client, manually create the 10 custom watchlist groups: `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts`, `IPO`. **The API cannot create groups — it can only modify existing custom groups.** The earnings-gap, pre-market and post-open morning-gap scans all sync into the single append-only `EarningsGap` group.
3. The morning-gap scan now **requires** OpenD running — discovery is
   Futu-snapshot based (it no longer depends on Finviz). With OpenD down,
   `--mode morning-gap` writes empty `.txt` files and skips the Futu sync,
   logging a single warning per run.

**Config (`[futu]` in `config.toml`):**
```toml
[futu]
enabled = true
host = "127.0.0.1"
port = 11111

append_only_groups = [
    "EarningsGap", "HighVolume", "GapUp", "NewHigh52W", "TopGainers",
    "Leaders", "Shorts", "RS", "HKShorts", "IPO",
]

[futu.groups]
longs_earnings_gap = "EarningsGap"
morning_gap = "EarningsGap"          # merged into EarningsGap (append-only)
morning_gap_pre = "EarningsGap"      # merged into EarningsGap (append-only)
longs_high_volume = "HighVolume"
longs_gap_up = "GapUp"
longs_new_high_52w = "NewHigh52W"
longs_top_gainers = "TopGainers"
shorts = "Shorts"
rs = "RS"
hk_shorts = "HKShorts"
leaders = "Leaders"
ipo = "IPO"                          # auto-collected; long-side yfinance drops
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

**Snapshot DataFrame quirks (SDK-version-specific, validated against the installed `futu-api`):**
- `get_market_snapshot` has no `change_rate` column. Derive the regular-session percent change from `(last_price - prev_close_price) / prev_close_price * 100`. Pre/after-hours rates ARE present as `pre_change_rate` / `after_change_rate`.
- `suspension` is a **string** column (only value seen: `"N/A"`), not a bool — `(df["suspension"] == False)` matches 0 rows. Use `delisting` (which IS bool) plus the exchange whitelist for the active/listed gate.
