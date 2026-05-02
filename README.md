# Finviz to TradingView

Stock screeners (US via Finviz, HK via HKEX + yfinance) that emit TradingView- and Webull-importable watchlists and auto-sync to Futu (富途牛牛) custom groups via OpenAPI.

## Screeners

| Group | Source | Notes |
|---|---|---|
| **Longs** (5 splits) | Finviz | `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers` (priority order, mutually exclusive). Oliver Kell methodology. |
| **Leaders** (5 strategies, merged) | Finviz | Above SMA50/SMA200, perf windows 4w/13w/26w/YTD/52w. |
| **Shorts** | Finviz Ownership + yfinance | Kullamägi parabolic blow-off: SMA20+20%, cap-conditional 2/3/4-week perf, 3+ consecutive up days. |
| **RS** (conditional) | Finviz | Only runs when SPY *and* QQQ are down >1.5%. Oliver Kell's relative-strength approach. |
| **IPO** (sidecar) | — | Auto-collected: long-side candidates dropped by yfinance for insufficient daily history (typical for stocks IPO'd within ~20 trading days). |
| **HK Shorts** | HKEX + yfinance | Same methodology as US Shorts, HKD-native cap thresholds. |
| **Morning Gap** (pre + post) | Futu snapshot + yfinance | 7 daily scans: -20/-10 pre-market, +10/+15/+20/+25/+30 post-open. Requires Futu OpenD running. |

**Global gates** (all long-side, configurable in `[settings]`):
- Dollar Volume ≥ $100M (20-day avg, yfinance)
- ADR% ≥ 4.0% (mean of `(High − Low) / Close` over 20 bars × 100, yfinance) — replaces the legacy Finviz `beta > 1.5`
- IBD RS Percentile ≥ 90 (from [Fred6725/rs-log](https://github.com/Fred6725/relative-strength), refreshed weekday ~01:30 UTC)

## Dedup

- **Within Longs** — 5 strategies are mutually exclusive (priority `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers`).
- **Cross-group** — long-side priority `Longs > Leaders > RS`.
- **Cross-day master** — `output/state/eod_seen_{US,HK,IPO}.txt`. Each ticker enters exactly one of the long-side groups on first sighting; subsequent runs only emit *new* tickers. IPO has its own master so a ticker that ages in still surfaces in its proper group later. Reset by deleting the file.
- **Excluded from cross-day master**: Shorts, HK Shorts, Morning Gap. Re-detection is meaningful for those.

## Output

```
output/
├── TV/                        # comma-separated, for TradingView "Import list..."
│   ├── US/<date>_{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers,Leaders,Shorts,RS,IPO,MorningGapPre,MorningGap}.txt
│   └── HK/<date>_Shorts.txt
├── Webull/                    # newline-separated mirror, for Webull "Upload as File"
│   ├── US/<date>_*.txt
│   └── HK/<date>_Shorts.txt
└── state/                     # cross-day "seen" masters, RS table cache, morning-gap per-day seen
```

Every run writes a fresh dated `.txt` per group (0-byte file when empty). Futu sync is **skipped** on empty results so an off day doesn't wipe an existing group.

## Futu auto-sync

Configure `[futu]` in `config.toml`. Sync hooks fire after each successful watchlist write — failures only log a warning, never block the `.txt` output.

**One-time setup:**
1. Launch [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html), log in (default `127.0.0.1:11111`).
2. In the Futu PC client, manually create these custom groups (the API can only modify existing groups, not create them):
   `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `HKShorts`, `IPO`.

All EOD groups are append-only — clear them manually when crowded (Futu cap: 500 per group for non-traders, 2000 for active traders).

## Push notifications (ntfy)

Morning-gap scans push an [ntfy.sh](https://ntfy.sh) notification when **new** tickers (not seen in any earlier scan today) appear. Configure `[notify]` in `config.toml`; subscribe to the topic in the ntfy iOS/Android app.

## Setup

```bash
uv sync                              # install
uv run main.py                       # EOD pipeline (Longs/Leaders/Shorts/RS/IPO/HK Shorts)
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

- **TradingView**: Watchlist → "Import list..." → pick the latest `output/TV/{US,HK}/<date>_*.txt`.
- **Webull**: Watchlist → "Upload as File" → pick the matching file from `output/Webull/{US,HK}/` (newline-separated; comma format silently truncates).

## Configuration

All screener filters, thresholds, and Futu/ntfy settings are in `config.toml`. See [`CLAUDE.md`](CLAUDE.md) for architecture and contribution notes.

## Dependencies

Python ≥ 3.12 — [finviz](https://github.com/mariostoev/finviz), [yfinance](https://github.com/ranaroussi/yfinance), [openpyxl](https://openpyxl.readthedocs.io/), [futu-api](https://pypi.org/project/futu-api/) (optional).
