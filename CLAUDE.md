# CLAUDE.md

Thresholds and group configs live in `config.toml`. This file covers only the
non-obvious invariants that are easy to break.

## Commands

```bash
uv sync
uv run main.py --mode us-eod         # US EOD (Longs/Leaders/Shorts/RS/IPO) — 10:00 HKT
uv run main.py --mode hk-eod         # HK EOD (Shorts + Longs/Leaders/RS)   — 20:00 HKT
uv run main.py --mode morning-gap    # US intraday gap scan; clean-exits outside ET window
uv run main.py --mode hk-morning-gap # HK intraday gap scan (post-open only)
uv run main.py --mode report --market {us,hk}  # CANSLIM report from today's .txt files
uv run pytest tests/ -v
```

## Layout

`main.py` (US EOD + morning-gap), `hk_eod.py` (HK pipeline), `rs_rating.py` /
`us_rs_3m.py` / `hk_rs.py` (RS table fetchers), `hk_metrics.py` (HK metrics frame
fetcher — cloud-published `data/hk_metrics/`, local-fetch fallback), `futu_sync.py`,
`notify.py`, `us_ipo.py`, `report/`, `cleanup.py`. Sources: Finviz, HKEX list,
Futu snapshots, yfinance. Output: `output/TV/{US,HK}/` (TradingView, comma-sep)
mirrored to `output/Webull/{US,HK}/` (newline-sep), then Futu sync.

## Invariants (don't break these)

- **Dated files only**, no "latest" copy. `write_watchlist` always writes the
  dated file even when empty (0-byte), no drop-guard.
- **Webull mirror is newline-separated** (its file upload truncates comma lists);
  TV `.txt` stays comma-separated.
- **Dedup, layered:** (1) within Longs, earlier `config.toml` entry wins; (2) Longs
  union deduped against Leaders (`Longs > Leaders`); (3) cross-day master
  `eod_seen_{US,HK,IPO,HKIPO}.txt` (`_dedup_seen`) — daily output = within-day
  survivors minus master, survivors append. Markets independent; IPO/HKIPO have
  own masters. **RS and Shorts are excluded from all dedup** (re-detect by design).
  Reset masters only by deleting the file.
- **Cleanup** (`cleanup_old_outputs`) is glob-driven and soft-fails; **never
  touches** `eod_seen_*`, `ntfy_last_seen.txt`, `edgar_cache/`, logs.
- **HK data-day rule:** only the 20:00 HKT slot uses today's close; earlier runs
  trim today's incomplete bar (and skip the conditional HSI-trigger RS group).
- **Report** is soft-fail (wrapper exit code reflects only the EOD step). Shorts /
  HK Shorts / Morning Gap are excluded from it.

## RS gating

Percentile tables are computed daily on **GitHub Actions** and published as CSVs;
the local pipeline only fetches them. US: `Fred6725/rs-log` (12M, vs SPY) +
`data/us_rs_3m/` (3M). HK: `data/hk_rs/` (12M+3M, vs HSI). Defaults 90, set 0 to
disable a tier. The HK metrics frame is now also cloud-published (`data/hk_metrics/`,
same workflow) and fetched locally via `hk_metrics.build_hk_metrics_cloud`, so
discovery runs on the full universe on the happy path; a cloud miss falls back to the
local (throttle-prone) k-line fetch.

- US Longs: 12M only. US Leaders + RS + Shorts, and all HK long-side: 12M ∩ 3M.
- Not gated: HK Shorts, Morning Gap. IPO: conditional 3M only (≥ 64-day history).
- **Do NOT make fetch failure hard-fail:** walk back ≤ 3 days of stale cache, then
  pass through (no gate) with a warning. Tickers **missing** from the table are
  KEPT, not dropped.

## Futu sync

Soft side-effect — logs a warning on failure, never raises. No-op when disabled /
unmapped / empty tickers (an empty `.txt` must not wipe the existing group).
Diff-based (one DEL + one ADD max). Append-only groups skip DEL and accumulate.
Ticker format: US `AAPL`→`US.AAPL`, HK `522`→`HK.00522` (5-digit). The 17 custom
groups must be created by hand in the client (API can't create groups).

**Gotchas (do not regress):**
- TCP probe `_opend_reachable` (1.5s) before `OpenQuoteContext` — without it the
  SDK retries forever on `ECONNREFUSED`. **Do not remove.**
- `get_market_snapshot` has no `change_rate` — derive from `(last_price -
  prev_close_price) / prev_close_price`. `pre_/after_change_rate` do exist.
- `suspension` is a string (`"N/A"`), not bool — use bool `delisting` instead.
- HK long-side data fetch hard-depends on OpenD (Futu *sync* being soft-fail does
  not make the *fetch* soft).

## Scheduling (launchd, HKT)

- US EOD Tue-Sat 10:00; HK EOD Mon-Fri 20:00; morning-gap modes fire many entries
  and **self-validate their window, clean-exiting outside it — do not add a hard
  error path** (missed wakes are silent by design).
- pmset wake keyword is **`wakeorpoweron`** on macOS 26+ (`wakepoweron` no longer
  parses). US EOD uses mode `us-eod` not `eod` (HK bar is incomplete at 10:00 HKT).
