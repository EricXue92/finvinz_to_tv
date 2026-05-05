# Daily Stock Screener Pipeline

Multi-source momentum and short screeners (US via Finviz, intraday gaps via Futu snapshots) that emit TradingView- and Webull-importable watchlists and auto-sync to Futu (富途牛牛) custom groups via OpenAPI, based on Oliver Kell and Kristjan Kullamägi.

> **Status (2026-05-05):** US-only. The HK Shorts pipeline is disabled in `config.toml` (`[hk_shorts]` commented out). Code path is preserved — uncomment to re-enable.

## Screeners

All Finviz-based scans use `ind_stocksonly` to exclude ETFs/ETNs. Morning Gap (Futu `stock_type=STOCK`) is stock-only by construction.

### Global gates (long-side)

Applied after the Finviz screen, before any expensive yfinance work. Configurable in `[settings]`.

| Gate | Scope | Threshold | Source |
|---|---|---|---|
| **IBD RS Percentile (Leaders)** | Leaders | ≥ 90 (top 10%; missing tickers KEPT) | [Fred6725/rs-log](https://github.com/Fred6725/relative-strength), `RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` normalised against SPY, refreshed weekday ~01:30 UTC |
| **IBD RS Percentile (Longs/RS/US Shorts)** | Longs (5 splits) + RS group + US Shorts | ≥ 90 (top 10%; missing tickers KEPT) | same source as above |
| **Dollar Volume** | Longs + Leaders | Price × 20-day avg volume ≥ $100M | yfinance daily |
| **ADR%** | Longs + Leaders | mean(`(High − Low) / Close`) × 100 over 20 completed bars ≥ 4.0% | yfinance daily |

**RS scope:** All US long-side EOD groups plus US Shorts gate at RS ≥ 90 — the IBD top decile is the leadership cohort on both directions (a parabolic blow-off short candidate is by definition high-RS). HK Shorts, Morning Gap, and IPO are NOT RS-gated: HK tickers aren't in the US RS table; Morning Gap is intraday discovery; IPO is by definition pre-RS-rating (sub-12-month history).

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

All 5 also pass the global Dollar Volume / ADR% gates and IBD RS ≥ 90.

### Leaders (5 strategies, merged)

Long-term trend leaders above SMA50 + SMA200. All five share the same base filters and differ only in performance window.

**Base filters:** Small Cap+, Avg Vol > 500K, Price > $20, Above SMA50, Above SMA200, plus the global gates.

| Strategy | Performance threshold |
|---|---|
| Leaders 4W +30% | 4-week perf ≥ 30% |
| Leaders 13W +50% | 13-week perf ≥ 50% |
| Leaders 26W +100% | 26-week perf ≥ 100% |
| Leaders YTD +100% | YTD perf ≥ 100% |
| Leaders 52W +150% | 52-week perf ≥ 150% |

### US Shorts

Kullamägi parabolic blow-off setups. Two-phase: Finviz Ownership pre-filter, then yfinance post-processing on a single shared download.

**Phase 1 — Finviz Ownership:** SMA20 +20%, Above SMA50, Avg Vol > 1M (Finviz 3-month avg), Cap > $300M. Then IBD RS ≥ 90 (cuts before the yfinance batch).

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

Filters: Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200, Dollar Volume ≥ $100M, ADR% ≥ 4.0%, IBD RS ≥ 90.

### IPO (auto-collected sidecar)

Long-side candidates that pass any Longs/Leaders/RS Finviz screen but get dropped by yfinance for **insufficient daily history** — typical for stocks IPO'd within the last ~20 trading days. They cleared price/volume on Finviz and are worth watching while they age in.

- Output: `output/TV/US/<date>_IPO.txt` + Webull mirror + Futu group `IPO`
- Has its own cross-day master `output/state/eod_seen_IPO.txt`. Once a ticker has enough yfinance bars to pass DV/ADR/RVol, it lands in its proper long-side group on the first qualifying day.
- Triggered by yfinance "insufficient data" / "insufficient volume data" / "insufficient daily bars for ADR%" / "failed to process" warnings. Real ADR%/dollar-volume rejections (data was sufficient, just below threshold) are NOT collected.

### HK Shorts (disabled)

Same Kullamägi methodology as US Shorts, sourced from HKEX equity list (~2,400 Main Board stocks) + yfinance, with HKD-native cap thresholds (cap ≥ HKD 300M, dollar volume ≥ HKD 100M, ADR% ≥ 4.0%, perf 50/200/300% by HKD 10B / 2B / 300M cap buckets, 3+ consecutive up days; output `HKEX:XXXX` format).

**Disabled since 2026-05-05** — uncomment `[hk_shorts]` in `config.toml` (and the `HKShorts` entry under `[futu]`) to re-enable. `main.py` skips the entire pipeline when the section is absent; no code changes needed.

### Morning Gap (pre-market + intraday, 7 daily scans)

Two-phase intraday gap scanner. **Pre-market (-20/-10 min)** writes `MorningGapPre.txt`. **Post-open (+10/+15/+20/+25/+30 min)** writes `MorningGap.txt` and adds an intraday cumulative-volume gate that surfaces stocks already trading their full daily average volume in the first 30 min — a Kullamägi signal of catalyst-driven institutional buying.

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

## Dedup

- **Within Longs** — 5 strategies are mutually exclusive (priority `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers`).
- **Cross-group** — long-side priority `Longs > Leaders > RS`.
- **Cross-day master** — `output/state/eod_seen_{US,IPO}.txt`. Each ticker enters exactly one of the long-side groups on first sighting; subsequent runs only emit *new* tickers. IPO has its own master so a ticker that ages in still surfaces in its proper group later. Reset by deleting the file.
- **Excluded from cross-day master**: Shorts, Morning Gap. Re-detection is meaningful for those.

## Output

```
output/
├── TV/                        # comma-separated, for TradingView "Import list..."
│   └── US/<date>_{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers,Leaders,Shorts,RS,IPO,MorningGapPre,MorningGap}.txt
├── Webull/                    # newline-separated mirror, for Webull "Upload as File"
│   └── US/<date>_*.txt
└── state/                     # cross-day "seen" masters, RS table cache, morning-gap per-day seen
```

Every run writes a fresh dated `.txt` per group (0-byte file when empty). Futu sync is **skipped** on empty results so an off day doesn't wipe an existing group.

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
uv sync                              # install
uv run main.py                       # EOD pipeline (Longs/Leaders/Shorts/RS/IPO)
uv run main.py --mode morning-gap    # intraday gap scan (auto-detects time window, exits cleanly outside)
```

## Automation (macOS launchd + pmset)

Daily EOD run: Tue–Sat 10:00 AM HKT (lands after US market close in both EDT and EST, and after the daily upstream RS Rating commit).

```bash
sudo pmset repeat wakeorpoweron TWRFS 09:59:00
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist
```

Intraday morning-gap run: separate plist with 70 calendar entries (Mon–Fri × 7 offsets × EDT/EST). The script self-validates ET time on each trigger and exits cleanly outside any window.

```bash
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist
sudo uv run scripts/schedule_morning_gap_wakes.py    # schedule one-shot wakes (re-run weekly)
```

## Importing

- **TradingView**: Watchlist → "Import list..." → pick the latest `output/TV/US/<date>_*.txt`.
- **Webull**: Watchlist → "Upload as File" → pick the matching file from `output/Webull/US/` (newline-separated; comma format silently truncates).

## Configuration

All screener filters, thresholds, and Futu/ntfy settings are in `config.toml`. See [`CLAUDE.md`](CLAUDE.md) for architecture and contribution notes.

## Dependencies

Python ≥ 3.12 — [finviz](https://github.com/mariostoev/finviz), [yfinance](https://github.com/ranaroussi/yfinance), [openpyxl](https://openpyxl.readthedocs.io/), [futu-api](https://pypi.org/project/futu-api/) (optional).
