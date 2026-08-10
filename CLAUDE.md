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
uv run python -m pytest tests/ -v   # `uv run pytest` fails to spawn; use python -m
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
  `output/state/eod_seen_{US,HK,IPO,HKIPO}.txt` (`_dedup_seen`) — daily output = within-day
  survivors minus master, survivors append. Markets independent; IPO/HKIPO have
  own masters. **RS and Shorts are excluded from all dedup** (re-detect by design).
  Reset masters only by deleting the file.
- **Cleanup** (`cleanup_old_outputs`) is glob-driven and soft-fails; **never
  touches** `eod_seen_*`, `ntfy_last_seen.txt`, `edgar_cache/`, logs.
- **HK data-day rule:** only the 20:00 HKT slot uses today's close; earlier runs
  trim today's incomplete bar (and skip the conditional HSI-trigger RS group).
  Weekends map to the previous Friday (`hk_effective_data_day`): Friday's close
  is settled, so weekend reruns fetch Friday's cloud metrics/RS CSVs at full
  coverage, don't trim, and don't skip the HSI-trigger group. Weekday holidays
  have no calendar — they 404 into the yfinance fallback as before.
- **Report** is soft-fail (wrapper exit code reflects only the EOD step). Shorts /
  HK Shorts / Morning Gap are excluded from it.
- **Catalyst report (pre-market)** is a **detached subprocess** spawned
  from the morning-gap path; it MUST NOT block the morning-gap process.
  Always uses DeepSeek + Tavily regardless of `[report] backend`. Reads
  only the JSON snapshot sidecar — MUST NOT call Futu / yfinance. Output:
  `output/Reports/<date>_us_premarket.md`, appended across the pre-market
  scans (-20/-10/-5) whenever one finds fresh tickers.

## RS gating

Percentile tables are computed daily on **GitHub Actions** and published as CSVs;
the local pipeline only fetches them. US: `Fred6725/rs-log` (12M, vs SPY) +
`data/us_rs_3m/` (3M). HK: `data/hk_rs/` (12M+3M, vs HSI). Defaults 90, set 0 to
disable a tier. The HK metrics frame is now also cloud-published (`data/hk_metrics/`,
same workflow) and fetched locally via `hk_metrics.build_hk_metrics_cloud`, so
discovery runs on the full universe on the happy path; a cloud miss falls back to the
local (throttle-prone) k-line fetch.

- Event groups gate on 12M only: US Longs 5 组; HK EarningsGap/HighVolume/GapUp
  (per-group wiring in `run_hk_eod`, mirrors US). Everything else long-side gates
  on 3M only: US Leaders / conditional RS / Shorts (keys `min_rs_percentile_rs` /
  `_shorts`, each defaulting to `min_rs_percentile_longs` when unset), HK Leaders +
  conditional RS, HK Shorts (`[hk_shorts].min_rs_percentile_3m`, defaults to
  `min_rs_percentile_longs_3m`; applied as a universe pre-filter before the
  yfinance batch in `filter_hk_shorts`). The 12M∩3M double gate is currently
  nowhere active (all knobs remain independently tunable).
- Not gated: Morning Gap. IPO: conditional 3M only (≥ 64-day history).
- **Do NOT make fetch failure hard-fail:** walk back ≤ 3 days of stale cache, then
  pass through (no gate) with a warning. Tickers **missing** from the table are
  KEPT, not dropped.
- **RS-line trend (annotate in EOD; manual prune via audit mode):** cloud scripts
  publish `rs_below_ma` / `rs_days_below_ma` / `rs_frac_below_ma` (TraderLion-style
  RS line = price/index vs its own EMA21) as extra columns in
  `data/{us_rs_3m,hk_rs}/<date>.csv`. The EOD log annotates long-side survivors
  whose RS line is persistently below its MA; EOD itself has **no `.txt`/dedup
  effect**. Computed cloud-side only (local never refetches klines). Config:
  `[rs_line]`. Spec:
  `docs/superpowers/specs/2026-05-27-rs-line-trend-filter-design.md`.
- **`uv run main.py --mode rs-line-audit [--market us|hk|both] [--dry-run|--yes]`**
  scores the cross-day master, writes
  `output/rs_line_audit_<MKT>_<date>{,_drop,_keep_ranked}.txt`, prints the
  report, then **prompts y/N** to prune the drops from
  `output/state/eod_seen_{US,HK}.txt` so they can re-qualify on a future EOD run.
  Confirmed prunes back the master up first as `eod_seen_<MKT>.txt.bak.<stamp>`.
  `--yes` skips the prompt (auto-prune, legacy non-interactive behavior);
  `--dry-run` writes the report + sidecars but does NOT touch the master
  (no prompt, no backup). Manual, operator-triggered; not on the launchd schedule.

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
- HK long-side data fetch hard-depends on OpenD (Futu _sync_ being soft-fail does
  not make the _fetch_ soft).

## Scheduling (launchd, HKT)

- US EOD Tue-Sat 10:00; HK EOD Mon-Fri 20:00; morning-gap modes fire many entries
  and **self-validate their window, clean-exiting outside it — do not add a hard
  error path** (missed wakes are silent by design).
- pmset wake keyword is **`wakeorpoweron`** on macOS 26+ (`wakepoweron` no longer
  parses). US EOD uses mode `us-eod` not `eod` (HK bar is incomplete at 10:00 HKT).
- **RS workflow self-trigger:** GH Actions' scheduled cron is unreliable
  (delayed hours; sometimes skipped — observed 2026-06-16: today's 3M CSV
  missing because GH cron never fired). Launchd dispatches the workflow via
  `gh workflow run` 75 min before EOD: `us-rs-3m-trigger` (Tue-Sat 08:45) and
  `hk-rs-trigger` (Mon-Fri 18:45) → `scripts/trigger_rs_workflow.sh`. The
  GH-side cron is kept as belt-and-suspenders; workflow's commit step is
  idempotent (`git diff --staged --quiet → exit 0`) so a double-fire is
  harmless. Failures ntfy via the morning-gap topic.
