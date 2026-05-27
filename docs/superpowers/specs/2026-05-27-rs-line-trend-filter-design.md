# RS Line Trend Filter — Design

**Date:** 2026-05-27
**Author:** XUE (with Claude)
**Status:** Draft, pending user review

## Problem

The pipeline gates long-side lists on the RS *percentile* (a snapshot rank,
0-99). But a stock at percentile 95 *falling* from 99 is very different from one
*rising* from 85 — the snapshot hides the trend. The trader's actual rule is:
**only trade names whose relative strength is holding up, and drop those whose
RS line is persistently declining.**

The reference object is the **RS line** as drawn by TraderLion's TradingView
indicator (`N4Iqr5Cz`): the price-vs-index ratio with a user-configurable moving
average + cloud, a blue/pink up/down color, a "RS overtakes MA" triangle, and a
"new high before price" pink dot. The user reads the **line vs its MA**
relationship when deciding to keep or cut.

## Goal

Detect, for each long-side candidate, whether its **RS line is persistently
below its own moving average** — the inverse of TraderLion's bullish "RS line
overtakes MA" triangle — and surface it. v1 **annotates only** (logs the state,
no change to `.txt` / dedup output); a later v2 flips the calibrated signal into
a hard drop.

## Key design decisions (resolved during brainstorm)

1. **Object = RS line vs its MA position, NOT the RS percentile.** Percentile is
   cross-sectional rank (vs peers, re-ranked daily — noisy, and misleading in a
   broad correction). The RS line is vs the index, matching what the user sees in
   TV.
2. **Signal = line-vs-MA position + persistence, NOT the MA's slope.** The MA
   slope flips on 2-day wiggles and is hard to define stably (user's own point).
   The MA is the *noise filter*; we compare the line's position against it. A
   slow MA only gets decisively broken when the name genuinely weakens.
3. **Scale-invariance.** We only use whether the line is above/below its *own*
   MA. Scaling the denominator by a constant (SPX vs SPY ≈ ×10) scales the line
   and its MA by the same constant, leaving the position relationship unchanged.
   So benchmark choice (US: SPY in our tables vs SPX in TraderLion) is immaterial
   to this signal. HK uses HSI either way.
4. **Compute cloud-side.** The cloud RS scripts already fetch every ticker's
   klines + the benchmark kline (US 6mo, HK 2y) — enough to seed any reasonable
   MA with no ramp-up. The local pipeline stopped fetching klines (throttling) so
   this is the only place it *can* live, and it's free here.
5. **v1 annotate, observe, then calibrate the drop threshold.** Publish the raw
   persistence measures; do NOT hardcode a "≥ k days below" or "≥ X% below"
   cutoff until the real distribution is observed. (Avoids an unjustified magic
   number.)
6. **Scope: US + HK long-side only.** Shorts excluded for now.

## Non-goals (v1)

- **No hard drop.** v1 does not remove any ticker from any `.txt` or change
  dedup. Annotation/logging only.
- **No drop-threshold constants.** `drop_days_below` / `drop_frac_below` are
  intentionally absent until calibrated from observed data.
- **No TraderLion extras:** the "new high before price" pink dot (RSNHBP),
  the index-context overlay ("line above MA *while index below its MA*"), and the
  blue/pink direction color are out of scope (future enrichments, not needed for
  the core keep/cut decision).
- **No change to the existing RS percentile gate.** This is an *additional*
  observed signal layered after the existing 12M / 3M gates.
- **No local kline refetch.** Stays cloud-side.

## Architecture

### Module split

| File | Role | Status |
|------|------|--------|
| `rs_line.py` | pure compute: `compute_rs_line_features(klines, benchmark_kline, ...)` → per-ticker DataFrame | **new** |
| `scripts/compute_us_rs_3m_cloud.py` | call `rs_line` features, merge columns into US CSV before write | modified |
| `scripts/compute_hk_rs_cloud.py` | call `rs_line` features, merge columns into HK CSV before write | modified |
| `us_rs_3m.build_3m_table` | already returns full CSV frame → new columns flow through | unchanged |
| `hk_rs.build_hk_rs_tables` / `_split_combined` | extend to surface the new RS-line columns alongside 12M/3M | modified |
| `main.py` (US EOD) | annotate long-side log with RS-line state | modified |
| `hk_eod.py` | extend the per-category summary block (commit `9600c0e`) with RS-line state | modified |
| `config.toml` | new `[rs_line]` section | modified |

### Data flow

```
(cloud, in the existing weekday RS workflows)
  klines{ticker: close[]}  ─┐
  benchmark kline (SPY/HSI)─┴─> rs_line.compute_rs_line_features
                                   └─> rs_below_ma, rs_days_below_ma, rs_frac_below_ma
                                         │ merged as columns into
                                         v
                              data/us_rs_3m/<date>.csv   (adds columns)
                              data/hk_rs/<date>.csv       (adds columns)
                                         │ git push
                                         v
(local EOD)  build_3m_table / build_hk_rs_tables ──> annotate long-side log
                                                      (no .txt / dedup change)
```

## Component specs

### 1. `rs_line.py` — pure compute (new)

```python
def compute_rs_line_features(
    klines: dict[str, pd.DataFrame],   # {id: DataFrame[time_key, close]}
    benchmark_kline: pd.DataFrame,     # SPY (US) or HSI (HK), [time_key, close]
    ma_length: int = 21,
    ma_type: str = "ema",              # "ema" | "sma"
    persistence_window: int = 20,
    min_history: int = 42,             # < this many aligned bars → "unknown"
) -> pd.DataFrame:
    """Per-ticker RS-line-vs-MA features, indexed by the kline dict key.

    For each ticker:
      rs_line = close / benchmark_close   (date-aligned inner join)
      ma      = EMA/SMA(rs_line, ma_length)
      below   = rs_line < ma              (boolean series)
    Columns:
      rs_below_ma       bool   below on the latest aligned bar
      rs_days_below_ma  int    trailing consecutive bars below MA (0 if above)
      rs_frac_below_ma  float  fraction of last `persistence_window` bars below MA
    Tickers with < min_history aligned bars are emitted with all-NaN /
    rs_state="unknown" (short history can't seed the MA). Never raises.
    """
```

- Date-align each ticker's `close` to the benchmark via inner join on `time_key`
  before dividing (handles holiday/missing-bar mismatches).
- EMA via `series.ewm(span=ma_length, adjust=False).mean()`; SMA via
  `series.rolling(ma_length).mean()`.
- Pure function, fully unit-testable with synthetic series; no network, no I/O.

### 2. Cloud scripts — merge columns (modified)

**US** (`compute_us_rs_3m_cloud.py`, after `compute_us_rs_3m_table`): the
`klines` dict and `spy_kline` are already in scope.
```python
from rs_line import compute_rs_line_features
feats = compute_rs_line_features(klines, spy_kline, ma_length=CFG.ma_length, ...)
table = table.join(feats, how="left")   # table indexed by ticker
```
CSV columns become `ticker, raw_score, rs_percentile, rs_below_ma,
rs_days_below_ma, rs_frac_below_ma`.

**HK** (`compute_hk_rs_cloud.py`, after building `combined`): `klines` +
`hsi_kline` already in scope. Compute on HSI, join into `combined` (indexed by
`code`). Note: the HK RS *percentile* tables use weighted multi-window scores,
but the RS-*line* features are computed straight from `close/HSI_close` —
independent of the 12M/3M weight tuples.

The MA params come from the same `config.toml` the local pipeline reads, so
cloud and local agree by construction. Coverage guards and pruning unchanged;
new columns ride along in the same commit.

### 3. Local consumption (modified)

- **US:** `build_3m_table` already returns the whole CSV frame → `rs_below_ma`
  etc. are present automatically. No change to `filter_by_rs` (it only reads
  `rs_percentile`).
- **HK:** `_split_combined` currently extracts only `rs_percentile_12m/3m`.
  Extend `build_hk_rs_tables` to also return the RS-line columns (e.g. a third
  frame `rs_line_tbl` indexed by `code`), or pass the combined frame through.
- **Missing columns** (stale CSV from before this ships, or older fallback day):
  treated as `unknown` — annotation simply omits the name. Mirrors the
  `filter_by_rs` missing→passthrough philosophy. Never raises, never drops.

### 4. Annotation (modified, logging only)

- **US** (`main.py` EOD): after each long-side list is finalized/written, look up
  the surviving tickers' `rs_below_ma` / `rs_days_below_ma` / `rs_frac_below_ma`
  and log a one-line per-list summary, e.g.:
  `[Leaders] RS-line: 12 above MA, 3 below (>=10d below: NVDA(14d), XYZ(11d))`.
- **HK** (`hk_eod.py`): add an RS-line column to the existing per-category
  headline summary introduced in commit `9600c0e`.
- Output `.txt`, Webull mirror, Futu sync, dedup masters: **untouched**.

### 5. `config.toml` — new section

```toml
[rs_line]
enabled = true
ma_length = 21          # MATCH your TraderLion RS-line MA — confirm in TV settings
ma_type  = "ema"        # "ema" | "sma" — match TraderLion
persistence_window = 20 # trading days for the "fraction below MA" measure
min_history = 42        # fewer aligned bars → state = unknown
# v1 = annotate only. Drop thresholds intentionally ABSENT until the annotate
# phase reveals the real distribution; add one of these in v2:
#   drop_days_below = ...   # consecutive days below MA
#   drop_frac_below = ...   # fraction of persistence_window below MA
```

## Tests

| Layer | Approach |
|-------|----------|
| `compute_rs_line_features` — above/below | Synthetic ticker whose ratio is clearly above its MA → `rs_below_ma=False, rs_days_below_ma=0`; clearly below → `True`, positive day count. |
| persistence count | Series that goes below MA and stays → `rs_days_below_ma` increments; a 2-day dip then back above → small `frac_below`, `days_below` resets to 0 (validates the user's "2 days then up" case is not flagged as persistent). |
| `rs_frac_below_ma` | Half-below/half-above window → ≈0.5. |
| scale-invariance | Same ticker divided by SPY vs SPX-scaled-by-10 → identical `rs_below_ma` series. |
| short history | < `min_history` aligned bars → `unknown` row, no crash. |
| ema vs sma | Both paths produce finite features on a ramp series. |
| date alignment | Ticker with a missing/holiday bar vs benchmark → inner-join aligns, no NaN propagation into the boolean. |
| cloud merge | Smoke test: synthetic klines + benchmark → CSV gains the 3 columns with correct dtypes. |
| local annotation | Frame with known states → log summary lists the expected below-MA names; missing column → no crash, no annotation. |

## Migration plan

Independently reviewable commits:

1. **`rs_line.py` + unit tests.** Pure compute, no wiring. Verifiable in
   isolation.
2. **Cloud merge.** Modify both compute scripts + `config.toml`; `workflow_dispatch`
   each to seed CSVs carrying the new columns. Verify columns appear in
   `data/{us_rs_3m,hk_rs}/<date>.csv` on main.
3. **Local consumption + annotation.** `hk_rs` column surfacing + US/HK log
   annotation. Verify a real EOD run logs RS-line state, `.txt` output byte-identical
   to before (annotate-only).
4. **Docs.** `CLAUDE.md` RS-gating section note.

**v2 (separate, after observation):** add the calibrated `drop_*` threshold and
wire the drop into the long-side filter. Out of scope for this spec.

**Rollback safety:** every v1 commit is annotation-only; reverting any of them
leaves `.txt` output unchanged at all times.

## Scope summary

- **New:** `rs_line.py` (~60 lines) + tests (~80).
- **Modified:** 2 cloud scripts (~10 lines each), `hk_rs.py` (~15), `main.py`
  (~15), `hk_eod.py` (~10), `config.toml` (1 section), `CLAUDE.md`.
- **No new runtime dependencies** (pandas only).
- **No behavior change to any watchlist output in v1.**

## Open questions

1. **MA length + type** — RESOLVED: **EMA 21** (confirmed by user 2026-05-27 to
   match their TraderLion RS-line MA). Remains a `config.toml` knob.
2. **Persistence measure for v2** — `rs_days_below_ma` (consecutive) vs
   `rs_frac_below_ma` (fraction of window). Both published in v1; the annotate
   phase decides which separates winners from losers better. (Leaning fraction —
   more robust to chop.)

## References

- TraderLion RS Line indicator: https://www.tradingview.com/script/N4Iqr5Cz-TraderLion-s-Relative-Strength-Line/
- Cloud RS pipeline this extends: `docs/superpowers/specs/2026-05-21-us-rs-3m-cloud-pipeline-design.md`
- HK per-category summary log we extend: commit `9600c0e`
- Existing RS modules: `rs_rating.py` (12M), `us_rs_3m.py` (US 3M), `hk_rs.py` (HK 12M+3M)
