# RS Line Direction — Audit-First, Then Entry Gate — Design

**Date:** 2026-05-28
**Author:** XUE (with Claude)
**Status:** Draft, pending user review
**Supersedes:** the planned "v2 hard-drop" of
`docs/superpowers/specs/2026-05-27-rs-line-trend-filter-design.md`. This is the
concrete v2: it replaces that design's *position* signal (`rs_below_ma`) with a
*direction* signal. The v1 annotate-only work (PR #22, `rs_line.py`) stays as the
compute foundation; its position columns (`rs_below_ma` family) are retired.

## What changed from v1 (and why)

v1 (PR #22) measured **position**: is the RS line *below* its own MA, and how
persistently. During design review the user re-framed the goal:

> For an already-screened strong-stock pool, the only thing that can still
> change is **direction** — is the RS line's trend still up. Position is a
> *lagging result*; direction is the *leading cause* (direction rolls over
> 1–2 weeks before the line breaks its MA).

So the signal pivots from **position** (`rs_below_ma`) to **direction** (the
slope of the RS-line 21EMA over a short window). This is a deliberate reversal
of v1 decision #2 ("don't use MA slope") — justified because we now slope the
*already-smoothed* EMA over a *5-bar* window (not the raw line over 1 bar), which
removes the 2-day-wiggle instability that motivated the original objection.

## The signal

```
RS line       = close / benchmark_close        (SPY for US, HSI for HK)
EMA21         = 21-day EMA of the RS line       ("the 21EMA RS line")
rs_ema_chg_5d = (EMA21[t] − EMA21[t−5]) / EMA21[t−5]     (5 = trading bars)

direction up   ⇔  rs_ema_chg_5d ≥ −tolerance     (keep)
direction down ⇔  rs_ema_chg_5d <  −tolerance     (cut / don't admit)
```

- `tolerance` default **0.5%** (`0.005`). Rationale (user): at high RS levels the
  EMA wobbles slightly down even in healthy uptrends; a small band absorbs that
  noise instead of whipsawing. **0.5% is a working hypothesis, not yet
  data-validated** — Phase B exists to let the user judge it before it gates any
  output (see Open question).
- **Scale-invariance** (unchanged from v1 decision #3): the EMA21's absolute
  value depends on the benchmark scaling (SPY vs SPX), so it does **not** match
  the absolute level in TraderLion's TradingView chart. Only the *direction*
  (sign/magnitude of `rs_ema_chg_5d`) is comparable — that is the quantity to
  cross-check against TV.

## Decisions (resolved during brainstorm, 2026-05-28)

| # | Decision | Note |
|---|----------|------|
| 1 | **Hard-drop** is the end goal (not annotate-only) | Phase A removes weak-direction names from output. Built **only after** Phase B validates the criterion. |
| 2 | **Drop the position signal** (`rs_below_ma` family) | Direction-only. The v1 position columns + their log annotation are retired. |
| 3 | **Unknown ⇒ KEEP** | Insufficient history to seed EMA21+5-bar lookback ⇒ ticker kept, never dropped. Matches the CLAUDE.md invariant "tickers missing from the table are KEPT." Non-negotiable. |
| 4 | **5 trading bars**, not calendar days | klines are trading-day bars; weekends/holidays are not in the series. |
| 5 | **Scope = US + HK long-side** (Longs / Leaders / RS) | Shorts excluded (short logic is inverse — it *wants* weakening). IPO excluded (mostly < min_history ⇒ unknown anyway). Morning Gap excluded. |
| 6 | **Direction-dropped tickers do NOT enter the dedup master** (`eod_seen_*`) | Recoverable: a name cut for weak direction today can re-enter when its direction turns back up. Mirrors how Shorts/RS already bypass the master ("re-detect by design"). Only relevant in Phase A. |

## Why Phase B (audit) comes first

The pipeline emits a **deduped stream of NEW names** each day: `_dedup_seen`
(main.py:171) filters today's candidates against the cross-day master
`output/state/eod_seen_{US,HK}.txt`, outputs only the unseen, then appends them
to the master — so **each ticker appears once, ever**, and is never re-evaluated.

Consequence: a direction filter on the daily `.txt` is an **entry gate** on names
*first* entering the list. It can never "cull a weakening name you already hold,"
because that name is already in the master and the pipeline never looks at it
again. The user's "guardian" intent (re-check the held set, flag weakeners) is a
*different* operation the current pipeline doesn't perform.

**Resolution (user):** the real goal is the entry gate (Phase A). But before
committing to it, use a one-off **read-only audit** (Phase B) over the names
already in `eod_seen_US.txt` to see how the direction criterion scores stocks the
user recognizes — and judge whether "direction up" (and the 0.5% band) is a good
screening rule. Validate by eye, then decide on A.

## Phase B — read-only audit tool (build first)

**Goal:** let the user eyeball how `rs_ema_chg_5d` ranks the already-surfaced
names, to judge the criterion. **Touches nothing**: no `.txt`, no master, no
Futu, no cloud pipeline. Pure read.

**Inputs:** `output/state/eod_seen_US.txt` (~132 tickers) and
`output/state/eod_seen_HK.txt` (~8 tickers).

**Compute:** fetch ~6mo daily klines for each master ticker + the benchmark (SPY /
HSI) via yfinance, then `rs_line.py` math → `EMA21` and `rs_ema_chg_5d`.

> **yfinance is acceptable here** even though the daily pipeline avoids it: this
> is a *manual, one-off, read-only* audit over a *bounded* fixed list (~140
> tickers total), not the automated thousands-ticker daily scan that triggered
> the throttling concern. When Phase A is built, A rides the **cloud CSV column**
> instead (see Phase A) — yfinance is Phase-B-only.

**Output (the key design choice):** print **all** master tickers **sorted by
`rs_ema_chg_5d` ascending** (weakest first), with the `−0.5%` cut line marked
in-place, plus a separate "unknown / insufficient history" list. Showing the
**full distribution** (not just the failures) is what lets the user judge the
rule: if recognized strong names cluster well above 0 and the names they'd cut
sit clearly below −0.5% with a gap around the line, the rule holds; if good and
bad names straddle −0.5% indiscriminately, the rule (or the threshold) is wrong.

Per-ticker columns: `ticker`, `rs_ema_chg_5d` (the verdict %), and the current
`EMA21` value (descriptive only; absolute value not TV-comparable — see
scale-invariance). Position (`line vs EMA`) is **not** shown (decision #2).

- Console table **plus** a dated report file `output/rs_line_audit_<date>.txt`
  for later review.
- Run: `uv run main.py --mode rs-line-audit` (optionally `--market {us,hk,both}`,
  default both).
- Read-only and idempotent; safe to run any number of times.

## Reversal exemption (added during Phase B validation)

Validation surfaced a real lag failure: the 21EMA 5-bar slope lags, so a name
that just V-bottomed and is bouncing hard still reads direction-cut (false
positive — observed on CRML, FORM). To avoid cutting genuine reversals, a
direction-cut name is **EXEMPT** ("reversing") when its recent price move is
large relative to its own volatility:

```
ret_per_adr = (lookback-bar price return %) / ADR%        ADR% = mean(last 20 of (high-low)/close) × 100
EXEMPT  if  ret_per_adr ≥ adr_mult        else DROP
```

- **Scale by ADR, not by σ of recent returns.** A `2σ(5-day returns)` rule was
  tried and **empirically rescued zero of 10 cut names** (incl. CRML/FORM):
  high-ADR momentum names have huge own-σ (10–80%), so 2σ is unreachable, and
  using the drop+bounce-inflated σ as the yardstick is self-defeating. ADR% is
  stable, already computed in the pipeline (`us_ipo.py` formula), and gives an
  interpretable "how many ADRs did it move this week" number.
- **`adr_mult` is a tunable config knob** (`[rs_line]`, default 1.5), set by the
  operator — lower in a bull regime, higher in a correction. Auto-regime
  switching (the cut "Layer 0") is intentionally NOT built; revisit only if data
  shows one fixed value can't work across regimes.
- **Validation result (2026-05-28, adr_mult=1.5):** of 10 US direction-cut names,
  6 exempt (CRML 1.99×, FORM 1.80×, AMKR 1.67×, ONTO 1.61×, LGN 1.54×, XNDU
  3.13×), 4 drop (weak/no bounce). Matches judgment.
- **Audit shows it read-only:** `compute_rs_reversal` (pure) + a `ret/ADR` column
  and EXEMPT/DROP flag in the report. No output change. `adr_mult` still to be
  calibrated from more days of data before Phase A enforces it.

## Phase A — direction entry gate (build only after user validates B)

When the user is satisfied with the criterion:

1. **Cloud:** extend the RS-line compute to publish a `rs_ema_chg_5d` column into
   `data/us_rs_3m/<date>.csv` and `data/hk_rs/<date>.csv` (same `rs` series the v1
   merge already computes — one more derived column). Retire the v1 `rs_below_ma /
   rs_days_below_ma / rs_frac_below_ma` columns (decision #2).
2. **Local gate:** after the existing 12M/3M RS-percentile gate, drop long-side
   tickers (US Longs/Leaders/RS, HK long-side) where `rs_ema_chg_5d < −tolerance`.
   - **Unknown ⇒ keep** (decision #3): ticker absent from the CSV, or
     insufficient history ⇒ not dropped.
   - **Dropped names are NOT passed to `_dedup_seen`** and do NOT enter the
     master (decision #6) — recoverable. Kept names follow normal dedup (append
     to master).
3. Retire the v1 annotate-only log summary (`summarize_rs_line`), replaced by a
   one-line "[list] direction-gate: dropped N (recoverable): …" log.

`.txt` / Webull mirror / Futu sync semantics otherwise unchanged. Shorts, IPO,
Morning Gap untouched.

## `rs_line.py` additions

Add a pure-compute direction helper alongside the existing v1 function, e.g.
`compute_rs_direction(klines, benchmark_kline, ma_length=21, lookback=5,
min_history=…) -> DataFrame[rs_ema_chg_5d]` (and optionally the current `EMA21`
for the audit). Reuses the existing `_moving_average` and the date-aligned
`close/benchmark` ratio. Single source of truth for both Phase B (audit) and
Phase A (cloud column). Never raises; ids with < min_history aligned/MA-valid
bars are omitted (⇒ unknown ⇒ keep downstream).

`min_history` must cover EMA21 seeding + the 5-bar lookback; the v1 default
(42 MA-valid bars) is comfortable. 6mo (~126 bars) is plenty.

## `config.toml` `[rs_line]` changes

```toml
[rs_line]
enabled  = true
ma_length = 21          # EMA of the RS line (TraderLion-aligned)
ma_type   = "ema"
lookback  = 5           # trading bars for the direction slope
tolerance = 0.005       # 0.5% band — WORKING HYPOTHESIS, validated via --mode rs-line-audit
# Retired with the position signal: persistence_window, drop_days_below/frac_below.
```

## Tests

| Layer | Approach |
|-------|----------|
| `compute_rs_direction` — up | RS ratio on a steady ramp ⇒ `rs_ema_chg_5d > 0`. |
| — down | RS ratio declining over the last week ⇒ `rs_ema_chg_5d < 0`, magnitude sensible. |
| — flat within band | Tiny wobble ⇒ `|rs_ema_chg_5d|` small (validates the 0.5% band absorbs high-level chop). |
| 5-bar lookback | Change is measured against the bar 5 positions back, not calendar. |
| scale-invariance | Same series ÷ SPY vs ÷ (SPY×10) ⇒ identical `rs_ema_chg_5d`. |
| short history | < min_history aligned bars ⇒ omitted (unknown), no crash. |
| audit report | Synthetic master + klines ⇒ report lists all names sorted ascending, marks the −0.5% cut, separates unknowns; writes the dated file; touches no `.txt`/master. |
| (Phase A) gate keep/drop | Frame with known `rs_ema_chg_5d` ⇒ only `< −tol` dropped; unknown kept; dropped names not added to the master set. |

## Migration / rollback

- **Phase B is purely additive + read-only** — a new mode and a `rs_line.py`
  function. Reverting it changes nothing about daily output.
- **Phase A** changes `.txt`/dedup output; each step is independently revertable,
  and `tolerance` can be re-tuned (or the gate disabled via `enabled = false`)
  without code changes.

## Open question

**The 0.5% tolerance.** Accepted as a working hypothesis on the user's domain
rationale (high-level EMA wobble). Phase B exists to confirm or replace it from
real data. After observing the audit distribution, the calibrated form is one of:
(a) keep flat 0.5% if it cleanly separates winners from the names the user would
cut; or (b) a per-ticker band scaled to each name's own RS-direction volatility,
if a single flat band proves too loose for steady names / too tight for
high-beta movers. Not decided until the user has seen the audit.

## Implementation prerequisite

The merged v1 `rs_line.py` (PR #22) is not yet on the local working tree (local
`main` is behind `origin/main`). Sync local `main` before implementing so the new
direction helper extends the merged module rather than forking it.

## References

- v1 (position, annotate-only): `docs/superpowers/specs/2026-05-27-rs-line-trend-filter-design.md`
- v1 module: `rs_line.py`, tests `tests/test_rs_line.py` (PR #22)
- Dedup master semantics: `_dedup_seen` (main.py:171), CLAUDE.md "Dedup, layered"
- Cloud RS pipeline this extends: `docs/superpowers/specs/2026-05-21-us-rs-3m-cloud-pipeline-design.md`
- TraderLion RS Line: https://www.tradingview.com/script/N4Iqr5Cz-TraderLion-s-Relative-Strength-Line/
