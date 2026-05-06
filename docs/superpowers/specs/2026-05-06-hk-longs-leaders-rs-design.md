# HK Longs / Leaders / RS — Design Spec

**Status:** Draft
**Date:** 2026-05-06
**Scope:** Extend the existing HK Shorts pipeline to a full HK EOD scanner mirroring the US strategy set, sourced entirely from Futu OpenAPI.

## Goal

Run the same family of EOD scans on Hong Kong Main Board equities that we already run on US tickers, with HKD-native thresholds and a single shared data source (Futu k-line). Produce TradingView-importable `.txt` files, mirror to Webull, and sync into Futu custom watchlist groups — the same artifact contract as the US pipeline.

## In Scope

Six HK groups, in priority order for cross-strategy dedup:

| Group | Type | Output file (under `output/TV/HK/`) | Futu group |
|---|---|---|---|
| EarningsGap | Long | `<date>_EarningsGap.txt` | `HKEarningsGap` |
| HighVolume | Long | `<date>_HighVolume.txt` | `HKHighVolume` |
| GapUp | Long | `<date>_GapUp.txt` | `HKGapUp` |
| Leaders | Long | `<date>_Leaders.txt` | `HKLeaders` |
| RS (conditional) | Long | `<date>_RS.txt` | `HKRS` |
| Shorts (existing) | Short | `<date>_Shorts.txt` | `HKShorts` |

## Out of Scope (vs US)

- **NewHigh52W** and **TopGainers** Longs splits — both rely on Finviz signal flags and are dropped for HK simplicity.
- **IPO sidecar** — the IPO pipeline is a yfinance-data-gap sidecar; under a Futu-only HK pipeline, missing-data tickers are dropped silently like all other Futu fetch failures.
- **Earnings-calendar gating** for EarningsGap. We use a pattern-based proxy (gap ≥ 3% with RVol ≥ 3 — same liquidity surge that earnings days typically produce) instead, since neither Futu nor yfinance has a clean per-ticker HK earnings calendar.

## Architecture

### Module layout

- **New: `hk_eod.py`** — owns the entire HK pipeline. Extracts `fetch_hkex_equities()` and `filter_hk_shorts()` from `main.py`, adds new HK Longs/Leaders/RS logic. Exports `run_hk_eod(config, futu_cfg)`.
- **New: `hk_rs.py`** — local IBD-style RS percentile vs HSI. Same shape as `rs_rating.py` (`load_rs_table`, `filter_by_rs`). Caches to `output/state/hk_rs_rating_<date>.csv`.
- **Edit: `main.py`** — keeps CLI/config-load/top-level orchestration. After the US EOD block, calls `hk_eod.run_hk_eod(config, futu_cfg)`. Removes inline HK Shorts code (now lives in `hk_eod.py`).
- **Edit: `config.toml`** — adds `[hk_settings]`, `[[hk_longs]]`, `[[hk_leaders]]`, `[hk_rs]`, and 5 entries under `[futu.groups]` + `[futu] append_only_groups`.

### Data source: Futu-only

All HK price/volume/cap data comes from Futu OpenAPI:

- **`request_history_kline(code, start=today-260d, end=today, ktype=KL_DAY)`** — daily OHLCV per ticker (260 trading days ≈ 12 months, enough for 52w returns and the RS algorithm).
- **`get_market_snapshot([code])`** — last price, prev close, market cap, shares outstanding, suspension flag.
- **HSI index** — `request_history_kline("HK.800000", ...)` for the RS benchmark; `get_market_snapshot(["HK.800000"])` for today's day-change to trigger the conditional RS scan.

**No yfinance for HK.** The pipeline becomes a hard OpenD dependency, matching morning-gap. If OpenD is unreachable, write empty `.txt` files (preserving the daily artifact contract) and skip Futu sync.

### Pipeline flow

1. **Probe OpenD** via the existing `_opend_reachable` TCP probe. If unreachable, write empty .txt files for all 5 long-side HK groups + the existing HK Shorts and return.
2. **Universe** — `fetch_hkex_equities()` → ~2,400 Main Board codes. Codes converted to Futu format (`HK.00700`, etc.).
3. **K-line batch** — pull 260-day daily OHLCV for every ticker via `request_history_kline`. Throttle to Futu rate limits (the SDK handles this internally; sequential calls work). Estimated ~10–15 minutes full pass.
4. **Snapshot batch** — `get_market_snapshot` chunked at 200 codes/call (Futu cap) for market cap + shares outstanding.
5. **Build metrics frame** — one `pd.DataFrame` indexed by ticker, columns: `market_cap`, `last_price`, `prev_close`, `gap_pct`, `rvol`, `avg_vol_20d`, `avg_dollar_vol_20d`, `adr_pct`, `sma50`, `sma200`, `above_sma50`, `above_sma200`, `perf_4w`, `perf_13w`, `perf_26w`, `perf_ytd`, `perf_52w`, `consecutive_up_days`, `rs_percentile`.
6. **HK RS table** — `hk_rs.load_rs_table(kline_dict)`. Algorithm: per ticker compute `score = 0.4·R3 + 0.2·R6 + 0.2·R9 + 0.2·R12` where `Rn = (price_today / price_n_months_ago) - (HSI_today / HSI_n_months_ago)`. Then percentile-rank across the universe. Cache to `output/state/hk_rs_rating_<date>.csv`. Tickers with insufficient history are kept (passed through, not dropped) — same policy as US RS.
7. **Apply per-strategy filters** (in-memory, all use the metrics frame):

   All 5 long-side groups share a universal baseline: cap ≥ HK$300M, avg vol ≥ 500K shares/day, $vol ≥ HK$100M, ADR ≥ 4%, price ≥ HK$20, RS ≥ 90 (mirrors US Longs+Leaders all using `sh_avgvol_o500` + `sh_price_o20`). On top of the baseline:

   | Group | Additional gates |
   |---|---|
   | EarningsGap | gap ≥ 3%, RVol ≥ 3 |
   | HighVolume | RVol ≥ 3 |
   | GapUp | gap ≥ 5% |
   | Leaders | above SMA50 & SMA200, AND any of (4w +30 / 13w +50 / 26w +100 / YTD +100 / 52w +150) |
   | RS (conditional) | above SMA50 & SMA200; ONLY runs when HSI day-change ≤ −1.5% |
   | Shorts | (existing HK Shorts logic — independent; **retains its own 1M shares/day avg-vol floor** unchanged from today) |

8. **Within-day cross-strategy dedup** — priority order `EarningsGap > HighVolume > GapUp > Leaders > RS`. A ticker that qualifies for two groups lands in the first one only. Shorts is independent (separate side of the book).
9. **Cross-day master dedup** — `output/state/eod_seen_HK.txt` (new file). Applied AFTER within-day priority dedup to all 5 long-side groups. Each daily output = within-day survivors **minus** master; new survivors append to master. **Shorts excluded** from master dedup (mirrors US — short setups are time-sensitive). Reset by deleting the file (manual).
10. **Write** dated .txt files to `output/TV/HK/`, mirror to `output/Webull/HK/`, Futu-sync to the 5 new append-only groups.

### Threshold values

```toml
[hk_settings]
min_market_cap     = 300_000_000      # HKD
min_dollar_volume  = 100_000_000      # HKD; price * 20-day avg vol
min_avg_volume     = 500_000          # shares/day; 20-day average; applies to the 5 long-side HK groups (HK Shorts keeps its existing 1M floor in [hk_shorts])
min_adr_percent    = 4.0              # 20-day ADR%
min_price          = 20.0             # HKD; applies to all 5 long-side groups (mirrors US sh_price_o20)
min_rs_percentile_longs = 90          # all long-side groups
hsi_rs_trigger     = -1.5             # HSI day-change % to fire RS group
```

`min_market_cap = HK$300M` is intentionally small-cap-friendly — HK has many sub-HK$1B names with real volume that would be cut by a US-equivalent HK$2B threshold. `min_dollar_volume = HK$100M` is held at parity with HK Shorts (raising it to a USD-equivalent HK$780M would empty the list given HK liquidity is ~10× thinner than US).

### Leaders performance windows

Direct copy of the US Leaders config — % gains are market-neutral:

```toml
[[hk_leaders]]
name = "HK Leaders 4W +30%"
min_perf_4w = 30

[[hk_leaders]]
name = "HK Leaders 13W +50%"
min_perf_13w = 50

[[hk_leaders]]
name = "HK Leaders 26W +100%"
min_perf_26w = 100

[[hk_leaders]]
name = "HK Leaders YTD +100%"
min_perf_ytd = 100

[[hk_leaders]]
name = "HK Leaders 52W +150%"
min_perf_52w = 150
```

The 5 are merged into a single `Leaders.txt` (same as US Leaders).

### Longs config

```toml
[[hk_longs]]
key = "earnings_gap"
name = "HK Earnings Gap"
min_relative_volume = 3
min_gap_percent = 3.0

[[hk_longs]]
key = "high_volume"
name = "HK Relative Volume Surge"
min_relative_volume = 3

[[hk_longs]]
key = "gap_up"
name = "HK Gap Up"
min_gap_percent = 5.0
```

`key` field maps to the `[futu.groups] hk_longs_<key>` entry — same convention as US Longs.

### RS group config

```toml
[hk_rs]
name = "HK Relative Strength"
# No additional filters beyond [hk_settings] + above-SMA50/SMA200 + RS ≥ 90.
```

### Futu config additions

```toml
[futu.groups]
hk_longs_earnings_gap = "HKEarningsGap"
hk_longs_high_volume  = "HKHighVolume"
hk_longs_gap_up       = "HKGapUp"
hk_leaders            = "HKLeaders"
hk_rs                 = "HKRS"

[futu]
append_only_groups = [
    # ...existing US groups...
    "HKEarningsGap", "HKHighVolume", "HKGapUp", "HKLeaders", "HKRS",
]
```

The user must manually create the 5 new groups in the Futu PC client before first run (Futu API cannot create groups, only modify existing custom groups — same constraint as US groups).

## HK RS Rating (`hk_rs.py`)

```python
def load_rs_table(kline_dict: dict[str, pd.DataFrame], today: pd.Timestamp) -> pd.DataFrame | None:
    """Return DataFrame indexed by Futu code with column `rs_percentile` (0-99).

    Algorithm (mirrors IBD/Fred6725):
      Rn(t) = price_t / price_{t - n*21} - 1   # n in {3, 6, 9, 12}
      score = 0.4*R3 + 0.2*R6 + 0.2*R9 + 0.2*R12
      relative_score = score - hsi_score(same formula on HSI)
      rs_percentile = percentile_rank(relative_score) across universe, scaled 0-99

    Caches to output/state/hk_rs_rating_<YYYY-MM-DD>.csv. Subsequent same-day calls
    read the cache. Returns None on fetch failure (fall back to passthrough in
    filter_by_rs, same as rs_rating.py).
    """

def filter_by_rs(tickers: list[str], rs_table: pd.DataFrame | None, threshold: int) -> list[str]:
    """Keep tickers with rs_percentile >= threshold. Tickers missing from rs_table
    are KEPT (recent listings without 12mo history; matches rs_rating.py policy)."""
```

## Failure modes

| Failure | Behavior |
|---|---|
| OpenD unreachable | All 6 HK .txt files written empty for the day. Futu sync skipped. One warning logged. The .txt is the primary artifact, so an empty file is still a valid daily record. |
| Futu k-line fetch fails for a single ticker | Ticker dropped silently (no IPO sidecar). |
| HSI k-line fetch fails | RS gate becomes passthrough (no filtering), all groups still run. Logged warning. |
| HSI snapshot fetch fails | Conditional RS group does not run (treated as "trigger condition not met"). |
| `eod_seen_HK.txt` corrupted/missing | Treated as empty set on read. Next run rebuilds from today's survivors. |

## Open questions for review

None — all design decisions confirmed during brainstorming.

## Implementation order (for the writing-plans phase)

1. Extract HK Shorts from `main.py` into `hk_eod.py` as a no-op refactor (verify identical output before adding new strategies).
2. Add Futu k-line + snapshot batch fetcher to `hk_eod.py`.
3. Add metrics-frame builder.
4. Implement `hk_rs.py` with HSI benchmark.
5. Implement Leaders, HighVolume, EarningsGap, GapUp filters.
6. Implement conditional RS group (HSI day-change trigger).
7. Wire cross-strategy dedup + `eod_seen_HK.txt` master dedup.
8. Wire Futu sync for the 5 new append-only groups.
9. End-to-end run on a recent trading day, hand-verify a few tickers per group.
