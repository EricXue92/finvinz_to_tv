# Daily Stock Screener Pipeline

Multi-source momentum and short screeners (US via Finviz, intraday gaps via Futu snapshots) that emit TradingView- and Webull-importable watchlists, auto-sync to Futu (富途牛牛) custom groups via OpenAPI, and produce daily CANSLIM-style research briefs via Claude Sonnet 4.6. Methodology based on Oliver Kell and Kristjan Kullamägi.

> **Status (2026-05-21):** US + HK. US uses Finviz + yfinance + IBD RS CSV (12M) + local 3M RS computation vs SPY. HK uses yfinance for k-line + HSI history (the original Futu-only spec was rolled back when Futu's free/Lv1 tier was found to cap 12-month history at ~12% of the Main Board universe). Futu still handles HK market caps, the live HSI day-change snapshot for the conditional RS trigger, and watchlist sync. Both markets now use a **12M ∩ 3M RS double gate** on Leaders/RS/Shorts (Longs 5 splits stay 12M-only) and a **depth-conditional IPO ladder** for sub-12-month tickers. HK pipeline runs in its own scheduled slot at 20:00 HKT (US runs at 10:00 HKT) — both write per-market logs. After each EOD run the wrapper scripts also invoke `--mode report` for that market, generating a CANSLIM Markdown + standalone-HTML brief for the day's newly-detected long-side tickers.

## Screeners

All Finviz-based scans use `ind_stocksonly` to exclude ETFs/ETNs. Morning Gap (Futu `stock_type=STOCK`) is stock-only by construction.

### Global gates (long-side)

Applied after the Finviz screen, before any expensive yfinance work. Configurable in `[settings]`.

| Gate | Scope | Threshold | Source |
|---|---|---|---|
| **IBD RS Percentile 12M (Leaders)** | Leaders | ≥ 90 (top 10%; missing tickers KEPT) | [Fred6725/rs-log](https://github.com/Fred6725/relative-strength), `RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` normalised against SPY, refreshed weekday ~01:30 UTC |
| **IBD RS Percentile 12M (Longs/RS/US Shorts)** | Longs (5 splits) + RS group + US Shorts | ≥ 90 (top 10%; missing tickers KEPT) | same source as above |
| **IBD RS Percentile 3M (Leaders/RS/US Shorts)** | Leaders + RS group + US Shorts (NOT Longs) | ≥ 90 (top 10%; missing tickers KEPT) | computed locally in `us_rs_3m.py`: `RS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63` vs SPY, universe = Fred6725 ticker list (~6100), cached to `output/state/rs_rating_3m_<date>.csv` with `raw_score` for IPO out-of-universe lookup |
| **Dollar Volume** | Longs + Leaders | Price × 20-day avg volume ≥ $100M | yfinance daily |
| **ADR%** | Longs + Leaders | mean(`(High − Low) / Close`) × 100 over 20 completed bars ≥ 4.0% | yfinance daily |

**RS scope (two-layer gate):**

| Group | 12M gate | 3M gate |
|---|---|---|
| Longs 5 splits (EarningsGap/HighVolume/GapUp/NewHigh52W/TopGainers) | `min_rs_percentile_longs` | — (12M only) |
| Leaders | `min_rs_percentile` | `min_rs_percentile_3m` |
| Conditional RS group | `min_rs_percentile_longs` | `min_rs_percentile_3m` |
| US Shorts | `min_rs_percentile_longs` | `min_rs_percentile_3m` |
| US IPO ladder (≥ 64 days) | — | `min_rs_percentile_3m` (via `np.searchsorted` against Fred6725 `raw_score`) |

Semantics: 12M ≥ 90 = "long-term leader"; 3M ≥ 90 = "still leading recently". Intersection = "old leader still leading". **Longs 5 splits stay 12M-only** by design — they already have strong event filters (EarningsGap / RVol surge / GapUp / 52W high / Top Gainer) and stacking 3M would over-tighten the universe. Set `min_rs_percentile_3m = 0` to disable the entire 3M layer (skips the ~6100-ticker yfinance batch). HK Shorts and Morning Gap are NOT RS-gated.

ADR% replaces the legacy Finviz `beta > 1.5` filter — beta measures multi-year correlation with the broad market and was excluding mid/large-cap catalyst names that were actually in-play. ADR% (Kullamägi-style) directly measures whether a stock is currently moving.

### Longs (5 strategies, mutually exclusive)

Oliver Kell momentum/breakout setups. Priority order — earlier wins, each ticker appears in at most one Longs file per day.

| Priority | Strategy | Finviz filters |
|---|---|---|
| 1 | `EarningsGap` | Small Cap+, Earnings Today, Avg Vol > 500K, Price > $20, Rel Vol > 1.5, Gap Up 5%+, Above SMA50 & SMA200 |
| 2 | `HighVolume` | Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200 + yfinance Rel Vol ≥ 3× 20-day avg |
| 3 | `GapUp` | Small Cap+, Avg Vol > 500K, Price > $20, Gap Up 3%+, Above SMA50 & SMA200 |
| 4 | `NewHigh52W` | Small Cap+, Avg Vol > 500K, Price > $20, New 52W High, Above SMA50 & SMA200 |
| 5 | `TopGainers` | Small Cap+, Avg Vol > 500K, Price > $20, Above SMA50 & SMA200, Signal: Top Gainers |

All 5 also pass the global Dollar Volume / ADR% gates and IBD RS 12M ≥ 90. **No 3M layer on Longs** (the event filters already select for fresh momentum).

### Leaders (5 strategies, merged)

Long-term trend leaders above SMA50 + SMA200. All five share the same base filters and differ only in performance window.

**Base filters:** Small Cap+, Avg Vol > 500K, Price > $20, Above SMA50, Above SMA200, plus the global gates **including the 12M ∩ 3M RS double gate** (both ≥ 90).

| Strategy | Performance threshold |
|---|---|
| Leaders 4W +30% | 4-week perf ≥ 30% |
| Leaders 13W +50% | 13-week perf ≥ 50% |
| Leaders 26W +100% | 26-week perf ≥ 100% |
| Leaders YTD +100% | YTD perf ≥ 100% |
| Leaders 52W +150% | 52-week perf ≥ 150% |

### US Shorts

Kullamägi parabolic blow-off setups. Two-phase: Finviz Ownership pre-filter, then yfinance post-processing on a single shared download.

**Phase 1 — Finviz Ownership:** SMA20 +20%, Above SMA50, Avg Vol > 1M (Finviz 3-month avg), Cap > $300M. Then **IBD RS 12M ∩ 3M ≥ 90** (cuts before the yfinance batch).

**Phase 2 — yfinance + Futu cap snapshot, in order: performance → dollar volume → ADR% → consecutive up days.**

| Filter | Threshold | Source |
|---|---|---|
| Market cap (perf bucketing) | Live USD value | Futu snapshot `total_market_val` → Finviz Ownership fallback |
| Dollar Volume | ≥ $100M (20-day avg) | yfinance |
| ADR% | ≥ 4.0% (20-day) | yfinance |
| Performance — Large Cap (≥ $10B) | Up 50%+ over 2, 3, or 4 weeks | yfinance |
| Performance — Mid Cap ($2B–$10B) | Up 200%+ over 2, 3, or 4 weeks | yfinance |
| Performance — Small Cap ($300M–$2B) | Up 300%+ over 2, 3, or 4 weeks | yfinance |
| Consecutive Up Days | ≥ 3 green days (excludes today's incomplete bar if market is open) | yfinance |

Cap is sourced from Futu (truncation-free) rather than Finviz's coarse `"6.96M"`/`"1.23B"` strings, which can mis-bucket near the $2B / $10B boundaries.

### RS — Relative Strength (conditional)

Oliver Kell's relative-strength approach. **Runs only when SPY *and* QQQ are both down > 1.5%** on the day — surfaces stocks holding up in a weak market.

Filters: Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200, Dollar Volume ≥ $100M, ADR% ≥ 4.0%, **IBD RS 12M ∩ 3M ≥ 90** (double gate).

### IPO (auto-collected sidecar)

Long-side candidates that pass any Longs/Leaders/RS Finviz screen but get dropped by yfinance for insufficient daily history — typical for stocks IPO'd within the last few months. The candidate set is then run through a depth-conditional ladder (mirror of HK `filter_hk_ipo_candidates`, implementation in `us_ipo.filter_us_ipo_candidates`) so a day-30 IPO can still surface while a day-200 IPO is held to nearly the full long-side baseline:

| Gate | Threshold | Condition |
|---|---|---|
| min history | ≥ 20 trading days | always (drops day 1-19 — too noisy on volume) |
| cap | ≥ $300M | always (cap from Finviz captured during screener pass) |
| price | ≥ $10 | always |
| avg vol | ≥ 500K shares/day | only if ≥ 20 days |
| $vol | ≥ $100M | only if ≥ 20 days |
| ADR% | ≥ 4.0% | only if ≥ 20 days |
| above SMA50 | — | only if ≥ 50 days |
| above SMA200 | — | only if ≥ 200 days |
| 3M RS | ≥ 90 (vs Fred6725 raw_score distribution) | only if ≥ 64 days |

Thresholds match the US Longs baseline so promotion at full history is seamless. The 3M RS gate is special: IPO candidates aren't in the Fred6725 universe (< 120 days), so the ladder computes their score locally and ranks via `np.searchsorted` against the Fred6725 `raw_score` distribution — "where would this IPO rank if it joined the universe today". When `min_rs_percentile_3m = 0` the RS gate is skipped entirely.

- Output: `output/TV/US/<date>_IPO.txt` + Webull mirror + Futu group `IPO`
- Cross-day master: `output/state/eod_seen_IPO.txt` (independent of `eod_seen_US.txt`, so a promoted ticker lands in its proper long-side group on the first qualifying day)
- Guard: tickers present in the 12M Fred6725 RS table have ≥ 12mo of history and can't be fresh IPOs — those drops are flagged as transient yfinance gaps and excluded from the IPO bucket before the ladder.

### HK Shorts

Same Kullamägi methodology as US Shorts, sourced from HKEX equity list (~2,400 Main Board stocks) + yfinance, with HKD-native cap thresholds (cap ≥ HKD 300M, avg vol ≥ 1M shares/day [own floor — long-side uses 500K], dollar volume ≥ HKD 100M, ADR% ≥ 4.0%, perf 50/200/300% by HKD 10B / 2B / 300M cap buckets, 3+ consecutive up days; output `HKEX:NNN` format with leading zeros stripped). Re-enabled 2026-05-06.

### HK Long-side: EarningsGap / HighVolume / GapUp / Leaders / RS

Five strategies sourced from **yfinance** (k-line + HSI) plus **Futu** (market caps + live HSI day-change). The original spec was Futu-only, but Futu's free/Lv1 tier capped 12-month history coverage at ~12% of the Main Board universe — the IBD 12-month RS algorithm had nothing to rank — so the long-side k-line moved to yfinance which reliably gives 2+ years for almost every ticker. Mirrors the US Longs/Leaders/RS methodology with HKD-native thresholds. Universe = HKEX Main Board equities (~2,400). Output: `output/TV/HK/<date>_{EarningsGap,HighVolume,GapUp,Leaders,RS}.txt` in `HKEX:NNN` TradingView format (leading zeros stripped — TV silently rejects `HKEX:0148`-style tickers).

**Universal baseline (`[hk_settings]`):**

| Gate | Threshold | Note |
|---|---|---|
| Market Cap | ≥ HK$300M | small-cap-friendly; HK liquidity ~10× thinner than US |
| Avg Volume | ≥ 500K shares/day (20-day) | mirrors US `sh_avgvol_o500` |
| Dollar Volume | ≥ HK$100M (20-day) | held at parity with HK Shorts |
| ADR% | ≥ 3.5% (20-day) | tuned down from 4.0%; HK blue-chip volatility runs structurally lower than US |
| Last Price | ≥ HK$20 | mirrors US `sh_price_o20` |
| Above SMA50 & SMA200 | both | mirrors US `ta_sma50_pa` + `ta_sma200_pa` on every long-side filter |
| RS Percentile | ≥ 90 (vs HSI) | computed locally in `hk_rs.py`, not from Fred6725 CSV |

**Per-strategy gates** (priority order — earlier wins, each ticker appears in at most one HK long-side file per day). All five inherit the universal baseline above (which now includes the SMA50 & SMA200 trend filter), so the additional gates listed below are layered on top:

| Priority | Strategy | Additional gates |
|---|---|---|
| 1 | HK EarningsGap | gap ≥ 3% + RVol ≥ 3 (pattern-based proxy — no HK earnings calendar) |
| 2 | HK HighVolume | RVol ≥ 3 |
| 3 | HK GapUp | gap ≥ 5% |
| 4 | HK Leaders | any of (4w +30 / 13w +50 / 26w +100 / YTD +100 / 52w +150) |
| 5 | HK RS | none beyond baseline; **conditional** — only runs when HSI day-change ≤ −1.5% |

**HK RS algorithm** (`hk_rs.py`): same `0.4·R3 + 0.2·R6 + 0.2·R9 + 0.2·R12` weighted-quarter-returns formula as the US, but the benchmark is HSI (`^HSI` via yfinance) and percentiles are ranked across the HK Main Board universe. Computed in-process from the same yfinance k-line batch already pulled for the metrics frame; cached to `output/state/hk_rs_rating_<date>.csv`.

**OpenD soft-depends**: HK long-side k-line + HSI history come from yfinance, so OpenD being down does NOT empty the .txt files. With OpenD down: market caps go to NaN (and the cap≥HK$300M baseline drops everything), the conditional-RS HSI-trigger snapshot is skipped, and Futu sync is skipped — but the rank-and-write logic itself runs to completion. With OpenD up, the pipeline is fully populated. Each strategy writes to its own append-only Futu group (`HKEarningsGap`, `HKHighVolume`, `HKGapUp`, `HKLeaders`, `HKRS`) — must be created manually in the Futu PC client before first run.

### HK IPO (auto-collected sidecar)

Mirrors the US IPO sidecar. Tickers in the HKEX Main Board universe that yfinance returned but with `< 253 rows of daily close` (insufficient for the IBD 12-month RS calc) — almost always fresh HK IPOs that aged into yfinance but haven't accumulated 12 months of data yet.

- **Conditional baseline tuned to history depth.** Each gate fires only when the ticker has accumulated enough data — a true day-1 IPO still surfaces, but a 200-day-old IPO is held to nearly the full long-side baseline:

  | Gate | Threshold | Condition |
  |---|---|---|
  | cap | ≥ HK$300M | always |
  | price | ≥ HK$20 | always |
  | avg vol | ≥ 500K shares/day | only if ≥ 20 trading days |
  | $vol | ≥ HK$100M | only if ≥ 20 trading days |
  | ADR% | ≥ 3.5% | only if ≥ 20 trading days |
  | above SMA50 | — | only if ≥ 50 trading days |
  | above SMA200 | — | only if ≥ 200 trading days |
  | RS | — | always skipped (sub-12mo by definition) |

  ADR threshold matches the long-side's 3.5% floor — keeps the IPO baseline consistent with the rest of the HK long-side so promotion at 253 rows is seamless.
- **Output:** `output/TV/HK/<date>_IPO.txt`, mirrored to Webull.
- **Independent cross-day master:** `output/state/eod_seen_HKIPO.txt`. Once an IPO ages into ≥253 rows, it falls out of the IPO bucket and lands in its proper long-side group on the first qualifying day (the long-side master `eod_seen_HK.txt` is separate, no cross-contamination).
- **Futu group:** append-only `HKIPO` — must be created manually in the Futu PC client before first run.

### Morning Gap (pre-market + intraday, 9 daily scans)

Two-phase intraday gap scanner. **Pre-market (-20/-10/-5 min)** writes `MorningGapPre.txt`. **Post-open (+5/+10/+15/+20/+25/+30 min)** writes `MorningGap.txt` and adds an intraday cumulative-volume gate that surfaces stocks already trading their full daily average volume in the first 30 min — a Kullamägi signal of catalyst-driven institutional buying.

**Phase 1 — Futu snapshot discovery (replaces Finviz `ta_topgainers`, which ranked by regular-session perf and missed pre-market gappers):**

| Filter | Threshold | Source |
|---|---|---|
| Universe | NASDAQ / NYSE / AMEX, listed, `stock_type = STOCK` | Futu `get_stock_basicinfo` |
| Market Cap | ≥ $300M | `total_market_val` |
| Price | ≥ $10 | `last_price` |
| Gap (pre) | `pre_change_rate` ≥ 5% (and `pre_volume > 0`) | `pre_change_rate` |
| Gap (post) | `(last_price − prev_close) / prev_close × 100` ≥ 5% | derived from snapshot |

**Phase 2 — yfinance post-processing + Futu intraday volume:**

| Filter | Threshold | Pre | Post |
|---|---|---|---|
| Dollar Volume | ≥ $100M (20-day avg) | ✓ | ✓ |
| ADR% | ≥ 4.0% (20-day) | ✓ | ✓ |
| SMA50 / SMA200 | Latest close above both | ✓ | ✓ |
| 20-day Avg Volume | ≥ 500K shares/day | ✓ | ✓ |
| Intraday Cumulative Volume | RTH cumulative since 9:30 ET ≥ 20-day avg daily volume | — | ✓ |

Requires FutuOpenD running with US Lv1 BBO real-time quote permission. Without it, both Phase 1 discovery and the post-open volume filter return empty — there's no Finviz fallback. Each scan that surfaces *new* tickers (not seen in any earlier scan today) pushes an ntfy notification.

## Daily CANSLIM Report

After each EOD run, `--mode report --market {us,hk}` reads the day's dated long-side `.txt` files, ranks tickers by group priority, caps at 50 per market, and calls Claude Sonnet 4.6 with the `web_search_20250305` tool to produce a CANSLIM-style fundamentals + outlook brief for each ticker. Output: `output/Reports/<date>_{us,hk}.md` and a self-contained `<date>_{us,hk}.html` (inline CSS, no external assets — double-click to open in any browser).

| Aspect | Detail |
|---|---|
| **Inputs (US)** | 8 dated files: `EarningsGap`, `HighVolume`, `Leaders`, `GapUp`, `NewHigh52W`, `IPO`, `TopGainers`, `RS` |
| **Inputs (HK)** | 6 dated files: `EarningsGap`, `HighVolume`, `Leaders`, `GapUp`, `IPO`, `RS` (no NewHigh52W / TopGainers) |
| **Cap & priority** | 50 tickers/market; `EarningsGap > HighVolume > Leaders > GapUp > NewHigh52W > IPO > TopGainers > RS`. Overflow listed in a "Truncated" tail section. |
| **Structured fields** | yfinance: Market Cap, Price, EPS (latest Q + YoY), Revenue (latest Q + YoY), **3-year annual YoY** for both, PE, ROE, Inst. Hold %, latest earnings date. RS percentile from the cached IBD/HSI tables. |
| **Qualitative sections** | Model-generated with up to 3 `web_search` calls per ticker: 公司速览, 基本面/财报, 竞争力, 政策/政府支持, 新产品/催化剂, 风险点, 综合判断. |
| **Bilingual** | Snapshot fields stay in English/numbers; qualitative analysis in Simplified Chinese. |
| **Soft-fail** | Mirrors the Futu-sync contract — wrapper exit code reflects only the EOD step. Missing `ANTHROPIC_API_KEY` → step skipped with a warning, `.txt` artifacts unaffected. 4xx config errors fail fast with a distinct `[配置错误]` placeholder; 5xx/429/timeouts retry once before falling back to `[分析失败]`. |
| **Excluded** | US Shorts, HK Shorts, Morning Gap — technical/intraday plays where fundamentals don't drive entries. |
| **Cost envelope** | ~$0.03–0.05/ticker (Sonnet 4.6 + web_search). Typical $2–3/day. Hard cap ~$5/market/day. |

**Setup:** `export ANTHROPIC_API_KEY=sk-ant-...` in your shell (interactive use) and either in `scripts/run_eod.sh` / `scripts/run_hk_eod.sh` or via `launchctl setenv` / the plist's `EnvironmentVariables` (so launchd inherits it for the scheduled slots).

## Dedup

- **Within Longs** — 5 strategies are mutually exclusive (priority `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers`).
- **Cross-group** — long-side priority `Longs > Leaders > RS`.
- **Cross-day master** — `output/state/eod_seen_{US,IPO}.txt`. Each ticker enters exactly one of the long-side groups on first sighting; subsequent runs only emit *new* tickers. IPO has its own master so a ticker that ages in still surfaces in its proper group later. Reset by deleting the file.
- **Excluded from cross-day master**: Shorts, Morning Gap. Re-detection is meaningful for those.

## Output

```
output/
├── TV/                        # comma-separated, for TradingView "Import list..."
│   ├── US/<date>_{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers,Leaders,Shorts,RS,IPO,MorningGapPre,MorningGap}.txt
│   └── HK/<date>_{EarningsGap,HighVolume,GapUp,Leaders,Shorts,RS,IPO}.txt
├── Webull/                    # newline-separated mirror, for Webull "Upload as File"
│   ├── US/<date>_*.txt
│   └── HK/<date>_*.txt
├── Reports/                   # daily CANSLIM briefs (Markdown + standalone HTML)
│   └── <date>_{us,hk}.{md,html}
└── state/                     # cross-day "seen" masters, RS table caches, morning-gap per-day seen
    ├── eod_seen_US.txt        # US long-side master (5 Longs splits + Leaders + RS)
    ├── eod_seen_HK.txt        # HK long-side master (EarningsGap/HighVolume/GapUp/Leaders/RS)
    ├── eod_seen_IPO.txt       # US IPO sidecar (independent — promoted into US groups when ready)
    ├── eod_seen_HKIPO.txt     # HK IPO sidecar (independent — promoted into HK groups when ready)
    ├── morning_gap_seen_<date>.txt  # per-day MorningGap dedup
    ├── rs_rating_<date>.csv         # US 12M IBD RS percentile cache (Fred6725/rs-log)
    ├── rs_rating_3m_<date>.csv      # US 3M RS cache (raw_score + percentile, vs SPY, local)
    └── hk_rs_rating_<date>.csv      # HK RS percentile cache (computed locally vs HSI)
```

Every run writes a fresh dated `.txt` per group (0-byte file when empty). Futu sync is **skipped** on empty results so an off day doesn't wipe an existing group.

**TradingView ticker format:** US groups use `NASDAQ:AAPL` / `NYSE:WMT` / `AMEX:GLD` (Finviz-derived). HK groups use `HKEX:NNN` with **leading zeros stripped** — TradingView silently rejects `HKEX:0148`-style tickers, must be `HKEX:148`. Codes ≥ 1000 (4-digit) are written unchanged: `HKEX:1810` (Xiaomi), `HKEX:9988` (Alibaba). Codes < 1000 lose their padding: `HKEX:148` (KGI), `HKEX:522` (ASMPT), `HKEX:700` (Tencent).

## Futu auto-sync

Configure `[futu]` in `config.toml`. Sync hooks fire after each successful watchlist write — failures only log a warning, never block the `.txt` output.

**One-time setup:**
1. Launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in (default `127.0.0.1:11111`).
2. In the Futu PC client, manually create these custom groups (the API can only modify existing groups, not create them):
   `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `IPO`.

All EOD groups are append-only — clear them manually when crowded (Futu cap: 500 per group for non-traders, 2000 for active traders).

## Push notifications (ntfy)

Morning-gap scans push an [ntfy.sh](https://ntfy.sh) notification when **new** tickers (not seen in any earlier scan today) appear. Configure `[notify]` in `config.toml`; subscribe to the topic in the ntfy iOS/Android app.

## Setup

```bash
uv sync                                              # install
uv run main.py                                       # EOD pipeline (Longs/Leaders/Shorts/RS/IPO)
uv run main.py --mode morning-gap                    # intraday gap scan (auto-detects time window, exits cleanly outside)
uv run main.py --mode report --market us             # CANSLIM brief for today's US tickers (needs ANTHROPIC_API_KEY)
uv run main.py --mode report --market hk --date YYYY-MM-DD   # back-fill a specific day
```

## Automation (macOS launchd + pmset)

Two daily EOD slots (split by market close) plus an intraday morning-gap scanner. Each writes its own log file under `output/`:

| Slot | Trigger | Mode | Log | Plist |
|---|---|---|---|---|
| US EOD | Tue–Sat 10:00 HKT | `us-eod` | `output/launchd_US.log` | `~/Library/LaunchAgents/com.xue.finviz-to-tv.plist` |
| HK EOD | Mon–Fri 20:00 HKT | `hk-eod` | `output/launchd_HK.log` | `~/Library/LaunchAgents/com.xue.finviz-to-tv.hk-eod.plist` |
| Morning Gap | 90 entries/week (ET-aware) | `morning-gap` | (per-run) | `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist` |

The 10:00 HKT slot lands after US market close in both EDT and EST AND after the daily upstream RS Rating commit. The 20:00 HKT slot leaves 4 hours of slack after HK market close (16:00 HKT) for k-line data to finalize. The US slot uses `--mode us-eod` (HK is intentionally skipped — at 10:00 HKT the HK market has only been open 30 minutes and today's k-line bar is incomplete). After each EOD step succeeds, the wrapper script makes a soft-fail call to `--mode report --market {us,hk}` so the day's CANSLIM brief is produced in the same window — failures there don't affect the EOD's exit code.

```bash
# US slot
sudo pmset repeat wakeorpoweron TWRFS 09:59:00
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# HK slot (no pmset — Mac is typically awake at 20:00 HKT; launchd fires on next wake if asleep)
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.hk-eod.plist

# Morning-gap (separate plist with 90 calendar entries: Mon–Fri × 9 offsets × EDT/EST)
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist
sudo uv run scripts/schedule_morning_gap_wakes.py    # schedule one-shot wakes (re-run weekly)
```

The morning-gap script self-validates ET time on each trigger and exits cleanly outside any window.

## Importing

- **TradingView**: Watchlist → "Import list..." → pick the latest `output/TV/{US,HK}/<date>_*.txt`. HK tickers are written without leading zeros (`HKEX:148`, not `HKEX:0148`) — TradingView silently rejects the padded form.
- **Webull**: Watchlist → "Upload as File" → pick the matching file from `output/Webull/{US,HK}/` (newline-separated; comma format silently truncates).

## Configuration

All screener filters, thresholds, and Futu/ntfy settings are in `config.toml`. See [`CLAUDE.md`](CLAUDE.md) for architecture and contribution notes.

## Dependencies

Python ≥ 3.12 — [finviz](https://github.com/mariostoev/finviz), [yfinance](https://github.com/ranaroussi/yfinance), [openpyxl](https://openpyxl.readthedocs.io/), [futu-api](https://pypi.org/project/futu-api/) (optional), [anthropic](https://pypi.org/project/anthropic/) + [markdown](https://pypi.org/project/Markdown/) (for the daily CANSLIM report).
