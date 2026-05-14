# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                              # Install dependencies
uv run main.py                       # Full EOD (US + HK) — ad-hoc only; production splits these
uv run main.py --mode us-eod         # US only (Longs/Leaders/Shorts/RS/IPO) — 10:00 HKT slot
uv run main.py --mode hk-eod         # HK only (Shorts + Longs/Leaders/RS)   — 20:00 HKT slot
uv run main.py --mode morning-gap    # US intraday gap scan; auto-detects ET window, exits clean outside
uv run main.py --mode hk-morning-gap # HK intraday gap scan (post-open only); auto-detects HKT window, exits clean outside
uv run pytest tests/ -v              # Unit tests (HK pure-logic helpers)
```

## Architecture

Python tool: `main.py` (entry point + US EOD/morning-gap orchestration), `hk_eod.py` (full HK pipeline: Shorts + Longs/Leaders/RS), `hk_rs.py` (local IBD-style RS percentile vs HSI), `rs_rating.py` (US IBD RS table fetcher), `futu_sync.py` (Futu OpenAPI mirror), `notify.py` (ntfy push for morning-gap). Scrapes Finviz (US EOD), HKEX securities list (HK universe), Futu snapshots (HK market caps, intraday US gaps, live HSI day-change), and yfinance (US post-processing + HK Shorts + HK long-side k-line + HSI history). Outputs TradingView- and Webull-importable `.txt` watchlists.

**Flow:** Load `config.toml` → Run screener groups sequentially → Deduplicate → Write output files to `output/TV/US/` and `output/TV/HK/` (TradingView format) and mirror to `output/Webull/{US,HK}/` (newline-separated, for Webull import)

**Thirteen (plus a daily research report) screener groups** (twelve EOD + one intraday-only):
- **Longs** (`[[longs]]` in config): 5 strategies, each written to its **own** file (no merged `Longs.txt`). Config list order = priority for internal mutual-exclusion dedup: `EarningsGap > HighVolume (Relative Volume Surge) > GapUp > NewHigh52W > TopGainers` → `output/TV/US/{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers}.txt`. Each `[[longs]]` entry has a `key` field; the matching `longs_<key>` entry under `[futu.groups]` supplies both the Futu group name and the .txt filename stem. The union of all 5 acts as a "virtual Longs" for the cross-group `Longs > Leaders > RS` dedup. Based on Oliver Kell's methodology. HighVolume uses yfinance post-processing for 20-day relative volume (configurable via `min_relative_volume` and `relative_volume_days`).
- **Leaders** (`[[leaders]]`): 5 strategies sharing a base filter set (cap_smallover, avg vol >500K, price >$10, above SMA50/SMA200) but differing in performance window (4w/13w/26w/YTD/52w), merged → `output/TV/US/Leaders.txt`. Global `min_dollar_volume` ($100M, 20-day avg) and `min_adr_percent` (>= 4.0%, 20-day) apply. The legacy `ta_beta_o1.5` filter has been removed across every Finviz group; ADR% is the replacement.
- **Shorts** (`[shorts]`): Single strategy with multi-phase filtering → `output/TV/US/Shorts.txt`. Based on Kristjan Kullamägi's blog criteria. Runs Finviz Ownership screener (SMA20+20%, avg vol >1M, cap >$300M) for market cap data, then post-processes via yfinance for cap-conditional performance (2/3/4-week windows: 10, 15, 22 trading days), dollar volume, and consecutive up days.
- **RS** (`[rs]`): Conditional → `output/TV/US/RS.txt`. Only runs when both SPY and QQQ drop >1.5% (checked via `finviz.get_stock()`). Based on Oliver Kell's relative strength approach.
- **IPO** (no config): Auto-collected sidecar of the long-side pipeline → `output/TV/US/<date>_IPO.txt`. Tickers that pass any long-side Finviz screener (Longs/Leaders/RS) but get dropped by yfinance for missing/insufficient daily history (typically the "insufficient data" / "failed to process" warnings). These are almost always fresh IPOs that lack the 20+ daily bars needed for DV/ADR/RVol. Has its own append-only Futu group `IPO` and its own cross-day master `output/state/eod_seen_IPO.txt` — the IPO master is independent of `eod_seen_US.txt`, so a ticker that ages into having enough yfinance data still lands in its proper long-side group on the first qualifying day. ADR% < min and dollar-volume < min rejections are NOT IPO drops (they're real filter rejections, not data gaps).
- **HK Shorts** (`[hk_shorts]`): Hong Kong market short candidates → `output/TV/HK/<date>_Shorts.txt`. Same methodology as US Shorts but sources data from HKEX securities list + yfinance. Uses HKD-native cap thresholds. Batch-downloads ~2,400 tickers in groups of 500. Retains its own `min_avg_volume = 1_000_000` floor (the 5 HK long-side groups share a 500K floor instead).
- **HK Long-side data-day rule**: the 20:00 HKT slot uses today's settled close. Any earlier run (mid-session, immediately post-close, or weekend) automatically trims today's incomplete bar from every k-line and uses **yesterday's close** as the latest data point. Same-day runs before 20:00 HKT also skip the conditional HSI-trigger RS group (the live HSI snapshot doesn't match the trimmed bars). Implementation: `hk_eod.run_hk_eod` checks `datetime.now(HKT).hour < 20` and rewrites the k-line dict in place.
- **HK Long-side** (`[hk_settings]` + `[[hk_longs]]` + `[[hk_leaders]]` + `[hk_rs]`): Five strategies sourced from yfinance (k-line + HSI) plus Futu (market caps + live HSI day-change). The original spec was Futu-only, but Futu's free/Lv1 tier capped 12-month history coverage at ~12% of the Main Board universe (PR #7 diagnostic), so the IBD 12-month RS algorithm had nothing to rank. yfinance reliably gives 2+ years for nearly every Main Board listing. Universe = HKEX Main Board equities (~2,400). All five share a baseline: cap ≥ HK$300M, avg vol ≥ 500K shares/day, $vol ≥ HK$100M, ADR ≥ 3.5%, price ≥ HK$20, **above SMA50 & SMA200** (mirrors US Longs `ta_sma50_pa` + `ta_sma200_pa` on every filter set), RS ≥ 90 (vs HSI). Cross-strategy priority: `EarningsGap > HighVolume > GapUp > Leaders > RS`. Each writes to its own `output/TV/HK/<date>_<Name>.txt`:
  - **HK EarningsGap** — pattern-based proxy for post-earnings setups (gap ≥ 3% + RVol ≥ 3). No HK earnings calendar, so the high-volume gap-up *pattern* is the signal.
  - **HK HighVolume** — RVol ≥ 3 (relative volume surge).
  - **HK GapUp** — gap ≥ 5%.
  - **HK Leaders** — ANY of (4w +30 / 13w +50 / 26w +100 / YTD +100 / 52w +150) on top of the universal baseline (which already includes SMA50 & SMA200). All 5 windows merged into one `Leaders.txt`.
  - **HK RS** — conditional. Only runs when HSI day-change ≤ −1.5% (Futu snapshot of `HK.800000`). No additional gates beyond the universal baseline. Output: `output/TV/HK/<date>_RS.txt`.
- **HK IPO** (no config; auto-collected sidecar of the HK long-side pipeline): tickers in the HKEX Main Board universe that yfinance returned but with `< 253 rows of daily close` (insufficient for the IBD 12-month RS calc) — almost always fresh HK IPOs that aged into yfinance but haven't accumulated 12 months of data. NOT RS-gated. Filters apply **conditionally** based on history depth so a true day-1 IPO still surfaces while a 200-day-old IPO is held to nearly the full long-side baseline:
  - **Always** (day 1+): cap ≥ HK$300M, price ≥ HK$20.
  - **If ≥ 20 trading days** (i.e., `avg_vol_20d` is non-NaN): avg vol ≥ 500K shares/day, $vol ≥ HK$100M, ADR ≥ 3.5% (matches the long-side floor; promotion at 253 rows is seamless).
  - **If ≥ 50 trading days** (i.e., `sma50` is non-NaN): price above SMA50.
  - **If ≥ 200 trading days** (i.e., `sma200` is non-NaN): price above SMA200.

  Output: `output/TV/HK/<date>_IPO.txt`. Independent cross-day master at `output/state/eod_seen_HKIPO.txt` — once an IPO ages into 253+ rows, it lands in its proper long-side group on the first qualifying day (the long-side master `eod_seen_HK.txt` is separate). Append-only Futu group `HKIPO`. Per-bucket drop counts are logged so the operator can see which conditional gate cut a given IPO.
  - **OpenD soft-depends**: HK long-side k-line + HSI history come from yfinance, so OpenD being down does NOT empty the .txt files. With OpenD down: market caps go to NaN (and the cap≥HK$300M baseline drops everything), the conditional-RS HSI-trigger snapshot is skipped, and Futu sync is skipped — but the rank-and-write logic itself runs to completion. With OpenD up, the pipeline is fully populated.
- **Daily CANSLIM Report** (`--mode report --market {us,hk}`; `[report]` config): Reads today's dated `.txt` files for the chosen market and produces a per-ticker fundamentals + outlook brief via a pluggable LLM backend (default: Claude Sonnet 4.6 with `web_search_20250305`). Output: `output/Reports/<date>_{us,hk}.md` and a self-contained `<date>_{us,hk}.html` (inline CSS, no external assets). Inputs: 8 US long-side files (EarningsGap, HighVolume, Leaders, GapUp, NewHigh52W, IPO, TopGainers, RS) or 6 HK files (same minus NewHigh52W and TopGainers). Capped at 30 tickers/market/day with priority `EarningsGap > HighVolume > Leaders > GapUp > NewHigh52W > IPO > TopGainers > RS`; overflow listed in a "Truncated" section. Triggered by `scripts/run_eod.sh` (US, after `--mode us-eod`) and `scripts/run_hk_eod.sh` (HK, after `--mode hk-eod`). **Soft-fail like Futu sync** — wrapper exit code reflects only the EOD step, not the report step. Requires `ANTHROPIC_API_KEY` env var (set in the wrapper scripts via `export` or via launchd plist `EnvironmentVariables`); missing → step skipped with a warning, `.txt` artifacts unaffected. Cost envelope (anthropic backend): ~$0.03–0.05/ticker (Sonnet 4.6 + web_search), ~$1–2 typical day, ~$3/market hard cap (30 × $0.10). Per-ticker structured data (Market Cap, EPS, Revenue, **5-year annual YoY**, **4-quarter YoY trajectory**, PE, ROE, RS percentile, latest earnings date) comes from SEC EDGAR for US fundamentals (`report/edgar.py`, 7-day TTL local cache, automatic per-ticker fallback to yfinance income statement on EDGAR miss) plus yfinance for snapshot fields (company name / sector / industry / market cap / price / institutional holdings / earnings date) and for HK fundamentals; qualitative analysis (competitive moat, government/policy support, new products, catalysts, risks, bottom-line) comes from the model with up to 2 web_search calls. **Latest-quarter EPS dual display**: alongside EDGAR's GAAP `EarningsPerShareDiluted`, the pipeline pulls **Adjusted (consensus headline)** EPS from yfinance `earnings_dates`'s "Reported EPS" column — same fetch as `latest_earnings_date`, so zero added HTTP calls. When GAAP and Adjusted diverge by more than 5% (relative to GAAP, with a 0.01 floor for near-zero) the snapshot row shows both side by side (`$X GAAP / $Y Adj`) and the HTML report appends a one-line footnote under the snapshot tables explaining the convention; otherwise a single value is shown unchanged. Rationale: TV's Latest-Quarter EPS follows the company's press-release headline — non-GAAP for SaaS/tech with heavy SBC (AKAM, DDOG, FROG), GAAP for loss-makers without material non-GAAP adjustments (FLNC, MRNA, INOD). No single feed picks correctly per-ticker, so we show both. The 4-quarter YoY trajectory and 5-year annual YoY series stay on EDGAR GAAP — historical-trend consistency wins over per-row TV match.

**Backend abstraction** (`report/llm.py`): The model + search wiring lives behind an `LLMBackend` protocol so we can swap providers without touching `analyst.py` or `__main__.py`. Two implementations today:
- **`anthropic`** (default): `claude-sonnet-4-6` via `anthropic.AsyncAnthropic` with the first-party `web_search_20250305` server tool. Single round trip.
- **`deepseek`**: `deepseek-v4-flash` via DeepSeek's Anthropic-compatible endpoint (`https://api.deepseek.com/anthropic` — same SDK, only `base_url` differs). DeepSeek has no first-party web search, so the backend runs a manual tool-use loop: it offers a `web_search` tool (input_schema `{query: string}`), intercepts each `tool_use` block, calls Tavily Search API (`finance` topic, basic depth, ~5 results) via the small `report/search.py` httpx wrapper, and feeds the result back as a `tool_result`. After `max_search_calls` (default 2) the loop forces one final no-tool turn so the model emits text. Cost envelope: ~$0.5/day per market at 30 tickers (DeepSeek LLM is ~$0.03 of that — Tavily at $0.008/credit dominates). Required env: `DEEPSEEK_API_KEY`, `TAVILY_API_KEY` (Tavily has 1000 free credits/month). Quality is a step below Sonnet 4.6 on nuanced Chinese financial analysis but acceptable for the routine "bullet-point catalysts/risks" sections.

Switch via `[report] backend = "deepseek"` in `config.toml`. Per-backend tuning (model name, max_tokens, search call limits) lives under `[report.anthropic]` / `[report.deepseek]`. The factory `build_backend()` raises a clear error when required env vars are missing — `__main__.py` catches and soft-fails the same way as missing `ANTHROPIC_API_KEY`. Snapshot fields stay in English/numbers; qualitative sections are in Chinese. 4xx API errors (bad key/model) fail fast with a distinct `[配置错误]` placeholder; 5xx/429/timeouts retry once before falling back to `[分析失败]`. Shorts, HK Shorts, and Morning Gap are intentionally excluded (technical/intraday plays, fundamentals are not the deciding signal).
- **Morning Gap** (`--mode morning-gap` only; `[morning_gap]` config): Intraday gap-up scanner. **Pre-market** (-20/-10 min) → `MorningGapPre.txt`; **post-open** (+10/+15/+20/+25/+30 min) → `MorningGap.txt`. Phase 1 = Futu snapshot universe scan (NASDAQ/NYSE/AMEX, cap ≥ $300M, price ≥ $10, gap ≥ 5%). Phase 2 = yfinance DV/ADR/SMA/avg-vol + Futu intraday cumulative volume (post-open only; ≥ 20-day avg daily volume). Per-day, **per-phase** seen files at `output/state/morning_gap_seen_pre_<date>.txt` and `morning_gap_seen_post_<date>.txt` — pre-market and post-open dedup independently so a ticker pushed in pre-market can still trigger a post-open alert. Both files merge into the append-only `EarningsGap` Futu group. **Requires OpenD** — no Finviz fallback. Each scan fires up to two ntfy pushes via `notify.py`: a regular **"N new"** alert for tickers brand-new to the phase, and a high-priority **"N PROMOTED"** alert listing pre-market gappers that have just crossed the post-open RTH cumulative-volume gate (volume confirmation of a pre-market gap).
- **HK Morning Gap** (`--mode hk-morning-gap`; `[hk_morning_gap]` config): HK intraday gap-up scanner — **post-open only** (+10/+20/+30/+40/+50/+60 min after 09:30 HKT, i.e. 09:40/09:50/10:00/10:10/10:20/10:30) → `output/TV/HK/<date>_HKMorningGap.txt`. **No pre-market phase**: Futu snapshot returns `pre_change_rate` / `pre_volume` / `pre_price` = `N/A` for all HK tickers regardless of time-of-day (verified 2026-05-11 — HK pre-auction IEP is not exposed by the snapshot API at our Lv1 account permission). Phase 1 = Futu basicinfo (HK_MAINBOARD universe, ~3,300 after delisting filter) + snapshot batch (cap ≥ HK$300M, price ≥ HK$20, gap ≥ 5%; gap derived from `(last_price - prev_close_price) / prev_close_price`). Phase 2 = yfinance daily 1y → 20d avg dollar volume ≥ HK$100M, ADR% ≥ 3.5%, above SMA50+SMA200, 20d avg volume ≥ 500K. Phase 3 = Futu snapshot `volume` field (today's RTH cumulative) ≥ 20-day avg daily volume — no yfinance 1m fallback (HK 1m data is unreliable; OpenD is already required upstream so a second source of truth would just complicate failure modes). Per-day seen file `output/state/hk_morning_gap_seen_post_<date>.txt` (no pre-phase variant). Independent append-only Futu group `HKMorningGap` (not merged into `HKEarningsGap` — HKEarningsGap is the EOD pattern-based proxy, semantically distinct from intraday gappers). ntfy title prefix is `"HK Morning Gap"`. PROMOTED alert never fires (no pre phase). Output ticker format is TradingView `HKEX:N` (leading zeros stripped) to match the rest of the HK pipeline.

**Key mechanisms:**
- Each run writes **only** date-stamped files (e.g. `2026_04_21_Shorts.txt`). There is no un-dated "latest" copy.
- Every dated write is mirrored to `output/Webull/{US,HK}/<same-filename>.txt` as **one ticker per line** (newline-separated, no exchange prefix change). Webull's "Upload as File" silently truncates comma-separated lists after the first 1-2 entries — the newline mirror is what you upload there. The TradingView `.txt` in `output/TV/{US,HK}/` stays comma-separated. `_write_webull(tickers, dated_path, output_dir)` runs after each `write_watchlist` call.
- `write_watchlist(tickers, output_path, fmt)`: unconditional writer. Always writes the dated file, even when `tickers` is empty (produces a 0-byte file for the day). No drop-guard / baseline comparison — every run leaves an artifact and Futu syncs to whatever was just written.
- **Cross-group dedup (Longs/Leaders/RS)**: Two layers. (1) Within Longs, the 5 strategies are deduped by config-list order — earlier wins. (2) After all three long-side groups have been collected, the Longs union is deduped against Leaders and RS with priority `Longs(union) > Leaders > RS` so each ticker appears in exactly one of the 7 long-side files (5 Longs splits + Leaders + RS) per run. The collection-then-write split means all Longs splits, Leaders, and RS files are written only after RS has finished. Shorts and HK Shorts are independent and written inline.
- **Cross-day master dedup** (`output/state/eod_seen_{US,HK,IPO}.txt`, implemented in `_dedup_seen`): applied to long-side EOD groups (5 US Longs splits + Leaders + RS; 5 HK long-side groups) AFTER within-day priority dedup. Each daily output = within-day survivors **minus** master; new survivors append to master. Net effect: every long-side ticker enters exactly ONE of its market's long-side groups on first sighting and never reappears in any long-side `.txt` / Webull / Futu push. Reset by deleting the file (manual only). **Markets are independent**: `eod_seen_US.txt` and `eod_seen_HK.txt` never cross-contaminate — a ticker dual-listed in both markets (rare for our universe) would track separately.
  - **IPO has its own master** `eod_seen_IPO.txt`, independent of `eod_seen_US.txt` — a ticker collected as an IPO drop today still lands in its proper long-side group on the first day it has enough yfinance history.
  - **Excluded**: US Shorts, HK Shorts, Morning Gap. Short setups are time-sensitive (parabolic blow-off can re-qualify weeks later), so re-detection is meaningful and a Shorts hit today does NOT suppress a future Longs hit on the same ticker. Morning Gap uses its own per-day, per-phase seen files `output/state/morning_gap_seen_{pre,post}_<date>.txt` — pre and post are independent so a pre-market push does not suppress a post-open push (and vice versa); see the Morning Gap entry above for promotion-alert behavior.
  - **Futu side**: long-side groups are in `[futu] append_only_groups` so they accumulate monotonically — the dated `.txt` records "today's NEW additions"; the Futu group records the all-time union. Shorts/HKShorts are also append-only on Futu but the daily `.txt` contains every Finviz-detected short for the day (including re-detections).
- 8-second delay between Finviz requests to avoid rate limiting (configurable in `config.toml`).

**Config format:** TOML. Filter strings (e.g. `sh_avgvol_o500`) map directly to Finviz URL parameters. The `signal` field is optional (used for Top Gainers).

**Scheduling (US EOD):** Runs Tue-Sat 10:00 AM HKT via launchd (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist` → `scripts/run_eod.sh` → `main.py --mode us-eod`). Mac wakes at 9:55 AM via `sudo pmset repeat wakeorpoweron TWRFS 09:55:00` (5-minute headroom so the WiFi/DNS stack is ready before launchd fires at 10:00; on 2026-05-14 a 9:59 wake left DNS unresolved through all 3 RS-fetch retries at 10:04:53–10:05:33, ~6.5 min post-wake). The keyword is `wakeorpoweron` on macOS 26+ — older docs / legacy schedules show `wakepoweron`, which no longer parses in `pmset repeat` even though existing schedules under that name still run. The 10:00 slot covers US Mon-Fri market close in both EDT and EST AND lands after the daily Fred6725/rs-log RS Rating commit (worst-observed lag: ~01:31 UTC = 09:31 HKT) so the IBD RS table is fresh when Longs/Leaders read it. Logs to `output/launchd_US.log` (rotated per calendar day by the wrapper). **Mode is `us-eod`, not `eod`** — the HK pipeline is intentionally skipped here because at 10:00 HKT the HK market has only been open 30 minutes; today's k-line bar is incomplete and would contaminate the cross-day master.

**Scheduling (HK EOD):** Runs Mon-Fri 20:00 HKT via launchd (`~/Library/LaunchAgents/com.xue.finviz-to-tv.hk-eod.plist` → `scripts/run_hk_eod.sh` → `main.py --mode hk-eod`). HK market closes at 16:00 HKT; the 20:00 slot leaves 4 hours of slack for k-line data to finalize. No `pmset` wake — the user's Mac is typically awake at 20:00, and launchd fires immediately on next wake if asleep. Logs to `output/launchd_HK.log`.

**Modes:** `eod` (default; full US+HK run, useful for ad-hoc), `us-eod` (US only — used by the 10:00 HKT slot), `hk-eod` (HK only — used by the 20:00 HKT slot), `morning-gap` (US intraday gap scanner), `hk-morning-gap` (HK intraday gap scanner; post-open only).

**Scheduling (morning-gap):** `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist` fires 90 calendar entries/week (Mon-Fri × 9 ET offsets × EDT/EST). One-shot pmset wakes are scheduled by `sudo uv run scripts/schedule_morning_gap_wakes.py` (re-run weekly). The script self-validates ET on each trigger and exits cleanly outside any window — DO NOT add a hard error path here; missed wakes are silent by design.

**Scheduling (hk-morning-gap):** Template plist `scripts/com.xue.finviz-to-tv.hk-morning-gap.plist` fires 30 entries/week (Mon-Fri × 6 HKT offsets at 09:40/09:50/10:00/10:10/10:20/10:30). HKT has no DST so EDT/EST split is not needed. No `pmset` wake required (user's Mac is typically awake during HK morning); if asleep, launchd fires on next wake. Wrapper `scripts/run_hk_morning_gap.sh` rotates `output/launchd_HK_morning_gap.log` daily. To install: `cp scripts/com.xue.finviz-to-tv.hk-morning-gap.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.xue.finviz-to-tv.hk-morning-gap.plist`. Same self-validation contract as the US plist: `_get_hkt_scan_offset` returns None outside the window and the run exits cleanly.

## IBD Relative Strength Rating

Two separate RS implementations — one per market.

### US: `rs_rating.py` (CSV-based, vs SPY)

`rs_rating.py` pulls the daily IBD-style RS percentile table (0-99) from `Fred6725/rs-log/output/rs_stocks.csv` (the published artifact of the [Fred6725/relative-strength](https://github.com/Fred6725/relative-strength) GitHub Action) and exposes a filter applied to gated EOD groups **right after `run_screener`** (or after the Finviz Ownership screener for Shorts) — placed before any yfinance batch download so the gate cuts most tickers before the expensive step. All US EOD long-side groups plus US Shorts gate at **RS ≥ 90** (top 10%): **Leaders** uses `min_rs_percentile`; **Longs (5 splits) + RS group + US Shorts** share `min_rs_percentile_longs`. HK Shorts, Morning Gap, and IPO are NOT RS-gated.

### HK: `hk_rs.py` (computed locally, vs HSI)

The Fred6725 CSV is US-only, so HK long-side groups use a **separate local RS computation**. Same algorithm (`RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12`) but the benchmark is HSI (`^HSI` via yfinance) instead of SPY, and the percentile is ranked across the HK Main Board universe (~2,400 tickers) instead of US. Computed in-process from the same yfinance k-line batch already pulled for the metrics frame, so there's no separate fetch step. Cached to `output/state/hk_rs_rating_<date>.csv`. All 5 HK long-side groups gate at `[hk_settings] min_rs_percentile_longs = 90`. HK Shorts is NOT RS-gated.

- **Algorithm**: `RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` normalised against SPY's same-formula score, then percentile-ranked across ~6100 NYSE/NASDAQ stocks (ETFs and test issues excluded). All 0-99 percentiles are pre-computed in the CSV — we don't recompute anything.
- **Caching**: First call per day downloads to `output/state/rs_rating_<date>.csv`; subsequent calls (e.g. ad-hoc re-runs) read the cache.
- **Failure mode**: If the fetch fails (network, GitHub outage, schema change) the fetcher first scans `output/state/rs_rating_*.csv` for the most recent file within 3 days of today and uses it as a stale fallback (logged as `Using stale fallback cache: rs_rating_<date>.csv (N day(s) old)`). The fallback file is **not** copied to today's cache path so a same-day rerun after the network recovers will still attempt a fresh download. Only when no acceptable cache exists does the loaded table become `None` and every `filter_by_rs` call becomes a one-line warning + passthrough — the pipeline still produces output; it just skips the RS gate for the day. **Do not turn this into a hard failure** — the .txt files are the primary artifact. The 3-day cap is in `rs_rating._FALLBACK_MAX_AGE_DAYS`; rationale is that US EOD runs Tue-Sat so a Mon-holiday + Tue-DNS-failure gap is at most 2 days, and beyond 3 days the percentile drift makes "no gate" more honest than "wrong gate".
- **Missing tickers**: Tickers not in the CSV (recent IPOs lacking 12mo history, foreign listings) are KEPT, not dropped — silently dropping them would surprise the user and over-prune new momentum names.
- **Config**: `[settings] min_rs_percentile = 90` (Leaders) and `min_rs_percentile_longs = 90` (Longs + RS group + US Shorts). Set either to 0 to disable that tier independently; the GitHub fetch is skipped only when both are 0.
- **Scope**: All US long-side EOD groups plus US Shorts gate at RS ≥ 90. **Leaders** uses `min_rs_percentile`. **Longs (5 splits)** (EarningsGap, HighVolume, GapUp, NewHigh52W, TopGainers), the conditional weak-market **RS** scan, and **US Shorts** share `min_rs_percentile_longs`. **HK Shorts** is unfiltered (HK tickers aren't in the US RS table; gating would either drop everything or — under the keep-as-missing policy — be a no-op). **Morning Gap and IPO** are unfiltered (Morning Gap is intraday discovery; IPO is by definition pre-RS-rating).

## Finviz Library

Uses `finviz` package (web scraping, no API key needed):
- `Screener(filters=[...], signal=...)` → `.data` returns list of dicts with `"Ticker"` key
- `get_stock("SPY")` → dict with `"Change"` field as string like `"-1.23%"`

## Futu (富途牛牛) OpenAPI Integration

`futu_sync.py` mirrors each successfully-written watchlist into a Futu custom watchlist group via the `futu-api` SDK. The `.txt` files remain the primary artifact — Futu sync is a soft side-effect that logs a warning on any failure and never raises.

**Architecture:**
- Hooks fire after every `write_watchlist` of the dated file in `main.py` / `hk_eod.py` — one call per group: each US Longs split (EarningsGap/HighVolume/GapUp/NewHigh52W/TopGainers), US Leaders/Shorts/RS/IPO, HKShorts + 5 HK long-side groups (HKEarningsGap/HKHighVolume/HKGapUp/HKLeaders/HKRS), MorningGap, MorningGapPre. Empty result = empty .txt **but Futu sync is skipped** so the existing Futu group is preserved (handled by `_futu_sync` early-return on empty `tickers`).
- `_futu_sync(config, key, tickers, market)` helper in `main.py` is a no-op when `[futu] enabled = false` or the group isn't mapped, so the EOD/morning-gap pipelines work identically with or without OpenD running. **Note:** the HK long-side pipeline still hard-depends on OpenD for the k-line/snapshot data fetch — Futu sync being soft-fail doesn't mean the data fetch is.
- `sync_to_futu()` is **diff-based**: calls `get_user_security(group_name)` for current contents, computes set diff, then issues at most one `DEL` and one `ADD` (under the 10-call/30s API rate limit).
- **Append-only / merged groups**: When a group is listed in `[futu] append_only_groups`, `sync_to_futu(append_only=True)` skips the DEL phase — tickers only accumulate. **All EOD groups** (US: `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `IPO`; HK: `HKShorts`, `HKEarningsGap`, `HKHighVolume`, `HKGapUp`, `HKLeaders`, `HKRS`) are append-only to pair with the cross-day master dedup. The merged `EarningsGap` group additionally receives `morning_gap` + `morning_gap_pre`. Groups grow monotonically across runs and must be cleared manually in the Futu client when they get too crowded (Futu cap: 500 per group for non-traders, 2000 for active traders).

**Prerequisites (must be done by the user, once):**
1. Install & launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in with the user's Futu account. Default listens on `127.0.0.1:11111`.
2. In the Futu PC client, manually create the 17 custom watchlist groups: `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts`, `IPO`, `HKEarningsGap`, `HKHighVolume`, `HKGapUp`, `HKLeaders`, `HKRS`, `HKIPO`, `HKMorningGap`. **The API cannot create groups — it can only modify existing custom groups.** The earnings-gap, pre-market and post-open morning-gap scans all sync into the single append-only `EarningsGap` group; HK morning-gap goes to its own `HKMorningGap` (not merged into `HKEarningsGap`).
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
    "HKEarningsGap", "HKHighVolume", "HKGapUp", "HKLeaders", "HKRS",
    "HKMorningGap",
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
# HK long-side groups (Futu-only EOD, added 2026-05-06)
hk_longs_earnings_gap = "HKEarningsGap"
hk_longs_high_volume  = "HKHighVolume"
hk_longs_gap_up       = "HKGapUp"
hk_leaders            = "HKLeaders"
hk_rs                 = "HKRS"
hk_morning_gap        = "HKMorningGap"   # post-open HK intraday gappers
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
