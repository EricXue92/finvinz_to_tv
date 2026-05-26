# CLAUDE.md

Guidance for Claude Code when working in this repo. Exact thresholds live in
`config.toml`; this file covers architecture and the non-obvious invariants that
are easy to break.

## Commands

```bash
uv sync                              # Install dependencies
uv run main.py                       # Full EOD (US + HK) — ad-hoc only; production splits these
uv run main.py --mode us-eod         # US only (Longs/Leaders/Shorts/RS/IPO) — 10:00 HKT slot
uv run main.py --mode hk-eod         # HK only (Shorts + Longs/Leaders/RS)   — 20:00 HKT slot
uv run main.py --mode morning-gap    # US intraday gap scan; exits clean outside the ET window
uv run main.py --mode hk-morning-gap # HK intraday gap scan (post-open only)
uv run main.py --mode report --market {us,hk}  # Daily CANSLIM report from today's .txt files
uv run pytest tests/ -v              # Unit tests
```

## Architecture

- `main.py` — entry point + US EOD / morning-gap orchestration.
- `hk_eod.py` — full HK pipeline (Shorts + Longs/Leaders/RS + HK IPO).
- `rs_rating.py` / `us_rs_3m.py` — US IBD RS table fetchers (12M / 3M, vs SPY).
- `hk_rs.py` — HK RS table fetcher (12M + 3M, vs HSI).
- `futu_sync.py` — Futu OpenAPI watchlist mirror.
- `notify.py` — ntfy push for morning-gap.
- `us_ipo.py` — US IPO depth-conditional filter ladder.
- `report/` — daily CANSLIM report (LLM backend + EDGAR/yfinance fundamentals).
- `cleanup.py` — retention cleanup of dated artifacts.

**Data sources:** Finviz (US EOD screeners), HKEX securities list (HK universe),
Futu snapshots (market caps, intraday gaps, live HSI), yfinance (k-line / SMA /
ADR / volume post-processing, HSI history).

**Flow:** load `config.toml` → run screener groups sequentially → dedup → write
to `output/TV/{US,HK}/` (TradingView, comma-separated) and mirror to
`output/Webull/{US,HK}/` (newline-separated). Then sync to Futu (soft side-effect).

## Screener groups

All long-side outputs are date-stamped (`<date>_<Name>.txt`). Thresholds are in
`config.toml`.

**US EOD** (`us-eod`):
- **Longs** (`[[longs]]`) — 5 strategies, each its own file: EarningsGap,
  HighVolume (RVol surge), GapUp, NewHigh52W, TopGainers. Config list order =
  within-Longs dedup priority. Oliver Kell methodology.
- **Leaders** (`[[leaders]]`) — 5 perf-window strategies (4w/13w/26w/YTD/52w)
  merged into `Leaders.txt`.
- **Shorts** (`[shorts]`) — Kullamägi criteria; Finviz Ownership screener +
  yfinance post-processing → `Shorts.txt`.
- **RS** (`[rs]`) — conditional, only runs when SPY and QQQ both drop ≥ 1.2%.
- **IPO** — auto-collected sidecar: long-side tickers dropped by yfinance for
  insufficient history, re-filtered through a depth-conditional ladder
  (`us_ipo.filter_us_ipo_candidates`). Own master `eod_seen_IPO.txt`.

**HK EOD** (`hk-eod`):
- **HK Shorts** (`[hk_shorts]`) — US Shorts methodology, HKD thresholds, HKEX +
  yfinance, ~2,400 tickers batched.
- **HK Long-side** (`[[hk_longs]]`/`[[hk_leaders]]`/`[hk_rs]`) — 5 strategies
  (EarningsGap/HighVolume/GapUp/Leaders/RS) over HKEX Main Board, sourced from
  yfinance (k-line + HSI) + Futu (caps + live HSI). HK RS is conditional on
  HSI day-change ≤ −1.2%.
- **HK IPO** — sidecar mirror of US IPO (`filter_hk_ipo_candidates` in
  `hk_eod.py`, unit-tested pure function). Own master `eod_seen_HKIPO.txt`.
- **Data-day rule:** the 20:00 HKT slot uses today's settled close. Earlier runs
  trim today's incomplete bar and use yesterday's close; same-day pre-20:00 runs
  also skip the conditional HSI-trigger RS group.
- **OpenD soft-depends:** k-line/HSI come from yfinance, so OpenD down does not
  empty the files — but caps go NaN (cap≥HK$300M baseline then drops everything)
  and Futu sync is skipped.

**Intraday:**
- **Morning Gap** (`morning-gap`) — US gap-up scanner. Pre-market (−20/−10 min) →
  `MorningGapPre.txt`; post-open (+10..+30) → `MorningGap.txt`. Futu-snapshot
  discovery + yfinance/Futu volume confirmation. **Requires OpenD.** Per-phase
  seen files; fires ntfy "N new" + high-priority "N PROMOTED" (pre-market gapper
  that crossed the post-open volume gate).
- **HK Morning Gap** (`hk-morning-gap`) — post-open only (no HK pre-auction data
  at our Lv1 permission). Own Futu group `HKMorningGap`, own seen file.

**Report:** `report --market {us,hk}` → `output/Reports/<date>_{us,hk}.{md,html}`.
Reads today's long-side files, caps 30 tickers/market/day. Structured fundamentals
from SEC EDGAR (US, 7-day cache, yfinance fallback) + yfinance snapshot; qualitative
analysis from the LLM backend (Chinese). Soft-fail — wrapper exit code reflects only
the EOD step. Shorts / HK Shorts / Morning Gap are excluded (technical plays).
LLM backend is pluggable (`report/llm.py`, `LLMBackend` protocol): `anthropic`
(default, `claude-sonnet-4-6` + native `web_search_20250305`) or `deepseek`
(`deepseek-v4-pro` + manual Tavily tool-loop). Switch via `[report] backend`.

## Invariants (don't break these)

- **Dated files only** — no un-dated "latest" copy. `write_watchlist` always
  writes the dated file, even when empty (0-byte file), with no drop-guard.
- **Webull mirror is newline-separated** — Webull's file upload truncates
  comma-separated lists. TV `.txt` stays comma-separated.
- **Cross-group dedup (Longs/Leaders):** within Longs, earlier config entry wins;
  then the Longs union is deduped against Leaders (`Longs > Leaders`). **RS is
  excluded** — the weak-market scan is meant to re-surface held-up names.
- **Cross-day master dedup** (`eod_seen_{US,HK,IPO,HKIPO}.txt`, `_dedup_seen`):
  first-sighting long-side groups only. Each daily output = within-day survivors
  minus master; survivors append to master. Markets are independent; IPO/HKIPO
  have their own masters. **Excluded:** US/HK Shorts, US/HK RS, Morning Gap (these
  re-detect by design). Reset only by deleting the file manually.
- **Cleanup** (`cleanup.cleanup_old_outputs`): deletes dated artifacts older than
  yesterday after each successful run; glob-driven, soft-fails. **Never touches**
  the `eod_seen_*` masters, `ntfy_last_seen.txt`, `edgar_cache/`, or logs.
  `rs_rating_*.csv` gets a 4-day window (preserves the 3-day fetch fallback).
- 8-second delay between Finviz requests (configurable).

## IBD Relative Strength

Two implementations. Both percentile tables (0-99) are **computed daily on GitHub
Actions** and published as CSVs in this repo; the local pipeline only fetches them.

- **US:** `rs_rating.py` fetches the 12M table (vs SPY) from the external
  `Fred6725/rs-log` artifact. `us_rs_3m.py` fetches the 3M table from
  `data/us_rs_3m/<date>.csv` (workflow `update_us_rs_3m.yml`, cron `0 1 * * 1-5`).
- **HK:** `hk_rs.py` fetches a combined 12M+3M table from `data/hk_rs/<date>.csv`
  (workflow `update_hk_rs.yml`, cron `0 11 * * 1-5`), vs HSI.

**Gates** (all thresholds default 90; set to 0 to disable a tier):
- US Longs (5 splits): 12M only.
- US Leaders + RS group + US Shorts: 12M ∩ 3M (double gate).
- HK long-side (all 5): 12M ∩ 3M (double gate).
- IPO ladders: conditional 3M gate (only tickers with ≥ 64 days of history).
- **Not gated:** HK Shorts, Morning Gap.

**Failure policy (do NOT make hard-fail):** on fetch failure the fetcher walks
back up to 3 days of stale cache, then passes through (no gate) with a warning.
The `.txt` files are the primary artifact. Tickers **missing** from the table
(recent IPOs, foreign listings) are KEPT, not dropped.

> Why cloud-computed: home-IP yfinance throttles partway through the ~2,400 HK /
> ~5,900 US universe, so the percentile distribution was built over only part of
> the universe. GH Actions runners get fresh IPs. The compute logic (`compute_*`,
> `fetch_*` in `us_rs_3m.py`/`hk_rs.py`) is unchanged — only the orchestrator
> moved to `scripts/compute_*_cloud.py`. The local HK 20:00 run still fetches
> ~2,400 k-lines for the *metrics* frame, so discovery is still locally throttled.

## Finviz

`finviz` package (web scraping, no API key): `Screener(filters=[...], signal=...)`
→ `.data` (list of dicts with `"Ticker"`); `get_stock("SPY")` → dict with `"Change"`.
Filter strings (e.g. `sh_avgvol_o500`) map directly to Finviz URL params.

## Futu (富途牛牛) OpenAPI

`futu_sync.py` mirrors each written watchlist into a Futu custom group. **Soft
side-effect** — logs a warning on any failure, never raises. `_futu_sync` is a
no-op when `[futu] enabled = false` or the group is unmapped, and early-returns on
empty `tickers` (so an empty `.txt` does not wipe the existing Futu group).
`sync_to_futu()` is diff-based (one DEL + one ADD max, under the rate limit).

> Note: HK long-side data fetch still hard-depends on OpenD even though Futu
> *sync* is soft-fail.

- **Append-only groups** (`[futu] append_only_groups`): skip the DEL phase, so
  they accumulate monotonically — pairs with the cross-day master dedup. All EOD
  groups + `EarningsGap` (merged with morning-gap) are append-only. Clear manually
  in the client when full (cap: 500 non-trader / 2000 active trader).
- **Ticker format** (`_to_futu_code`): US `AAPL` → `US.AAPL`; HK `522`/`0522.HK`/
  `HKEX:0522` → `HK.00522` (5-digit zero-padded).

**Prerequisites (once, by the user):**
1. Install & launch FutuOpenD, log in (default `127.0.0.1:11111`).
2. Manually create the 17 custom groups (the API can only modify existing custom
   groups, not create them): EarningsGap, HighVolume, GapUp, NewHigh52W,
   TopGainers, Leaders, Shorts, RS, HKShorts, IPO, HKEarningsGap, HKHighVolume,
   HKGapUp, HKLeaders, HKRS, HKIPO, HKMorningGap. Morning-gap (pre + post) merges
   into `EarningsGap`; HK morning-gap goes to its own `HKMorningGap`.

**Gotchas (do not regress):**
- **TCP probe** (`_opend_reachable`, 1.5s) runs before `OpenQuoteContext` —
  without it the SDK retries forever on `ECONNREFUSED`. **Do not remove.**
- `get_market_snapshot` has **no `change_rate`** column — derive regular-session %
  from `(last_price - prev_close_price) / prev_close_price`. `pre_change_rate` /
  `after_change_rate` ARE present.
- `suspension` is a **string** column (`"N/A"`), not bool — use the bool
  `delisting` + exchange whitelist for the active/listed gate.

## Scheduling (launchd, all HKT)

- **US EOD** — Tue-Sat 10:00 (`com.xue.finviz-to-tv.plist` → `run_eod.sh` →
  `us-eod`). Mac wakes 09:55 via `pmset repeat wakeorpoweron TWRFS 09:55:00` (5-min
  DNS headroom). **Keyword is `wakeorpoweron`** on macOS 26+ (`wakepoweron` no
  longer parses). Mode is `us-eod` not `eod` — at 10:00 HKT the HK bar is
  incomplete and would contaminate the master.
- **HK EOD** — Mon-Fri 20:00 (`.hk-eod.plist` → `run_hk_eod.sh`). No pmset wake.
- **US morning-gap** — `.morning-gap.plist`, 90 entries/week (Mon-Fri × 9 ET
  offsets × EDT/EST). pmset wakes via `sudo uv run scripts/schedule_morning_gap_wakes.py`
  (re-run weekly).
- **HK morning-gap** — `scripts/com.xue.finviz-to-tv.hk-morning-gap.plist`, 30
  entries/week (09:40/09:50/10:00/10:10/10:20/10:30; HKT has no DST).

**Self-validation contract:** morning-gap modes detect their own ET/HKT window
and exit cleanly outside it (`_get_hkt_scan_offset` returns None). **Do not add a
hard error path** — missed wakes are silent by design.
