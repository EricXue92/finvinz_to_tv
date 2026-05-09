# Latest-Quarter EPS: GAAP + Adjusted Dual Display

## Problem

The CANSLIM report's `Latest Quarter — EPS` row is sourced from SEC EDGAR
`EarningsPerShareDiluted` (GAAP only). TradingView's "Latest Quarter EPS"
displays whichever number the company features in its earnings press-release
headline — which is non-GAAP / Adjusted for SaaS/tech (large stock-based
compensation makes the gap material), but GAAP for loss-makers without
material non-GAAP adjustments. Empirical sample (2026-05-07 report):

| Ticker | EDGAR GAAP | yfinance "Reported EPS" (Adj) | TV displays |
|---|---|---|---|
| AKAM Q1'26 | 0.71 | 1.61 | **1.61** |
| DDOG Q1'26 | 0.15 | 0.60 | **0.60** |
| FROG Q1'26 | -0.07 | 0.27 | **0.27** |
| FLNC Q2'26 | -0.16 | -0.12 | **-0.16** |
| INOD Q1'26 | 0.42 | 0.42 | 0.42 |
| MRNA Q1'26 | -3.40 | -3.40 | -3.40 |

No single free data source matches TV across the full sample. The decision
"GAAP vs Adjusted as headline" is set per-company by the issuer, not by any
public feed.

Revenue is unaffected — GAAP and Adjusted revenue are essentially the same
number (the "Reported" feed is consistent with EDGAR within rounding).

## Goal

Show enough information that, regardless of which TV widget the user is
comparing against, the matching number is visible in our report. Drop
the implicit promise "this is the TV number" — replace it with "here is
both GAAP and Adjusted; TV is one of them".

Non-goals:
- Identifying programmatically which value TV chose for a given ticker.
- Adding paid data sources.
- Changing 5-year annual YoY or 4-quarter YoY trajectory data sources
  (those stay on EDGAR — historical-consistency wins over per-row TV match).

## Design

### Data flow

`report/enrich.py:fetch_ticker_data` already calls `yf.Ticker(t).earnings_dates`
once (lines 242-249) to pick the latest past earnings date. Extend that
same call to also extract Reported EPS, and store as new fields alongside
the existing EDGAR-sourced GAAP fields. Zero new HTTP requests.

New fields in the ticker dict (added to the schema in `enrich.py:178-215`):

```python
"eps_latest_q_adj":          float | None,  # yf "Reported EPS" (Adjusted)
"eps_latest_q_adj_yoy_pct":  float | None,  # YoY of Reported EPS, paired
                                            # with row 4 entries earlier
```

Existing fields keep their semantics:
- `eps_latest_q` — GAAP from EDGAR (fallback: yfinance `quarterly_income_stmt`
  Diluted EPS, which is also GAAP).
- `eps_latest_q_yoy_pct` — GAAP YoY computed by EDGAR's quarter selector.

### Extraction logic (`enrich.py`)

Inside the existing `try: ed = t.earnings_dates` block:

1. Filter past rows (`ed.index <= now`).
2. Take the most recent past row's `Reported EPS` field as Adj latest.
3. Take the row whose timestamp is ≈ 1 year before that as Adj prior. Match
   by index position (4 rows back) — yfinance reports one row per fiscal
   quarter, so position-based pairing is consistent with the existing
   `latest_quarterly_with_yoy` helper.
4. Drop NaN to None (yfinance pads pending earnings with NaN).
5. Compute YoY via the shared `compute_yoy` helper (`enrich.py:34`) — same
   convention as the rest of the codebase (denominator is `abs(prior)`).

If `earnings_dates` is empty, raises, or has no past row with non-NaN
`Reported EPS`, both new fields stay None. No fallback — when yf has
nothing on Adjusted, we just show GAAP.

### Display logic (`renderer.py`)

Two render paths: `_render_quarterly` (HTML, line 656) and the markdown
inline-paragraph variant (line 996). Add a single helper:

```python
def _format_eps_dual(
    gaap: float | None,
    gaap_yoy: float | None,
    adj: float | None,
    adj_yoy: float | None,
) -> tuple[str, str]:
    """Return (value_str, yoy_str). Shows both GAAP and Adj only when both
    are usable and they differ by >5% relative to abs(gaap) (with a 0.01
    floor to avoid blow-up near zero)."""
```

Threshold rule: show both when both values exist AND
`abs(adj - gaap) / max(abs(gaap), 0.01) > 0.05`.

Output forms:

| Case | Value | YoY |
|---|---|---|
| Both, materially differ | `$0.71 GAAP / $1.61 Adj` | `YoY GAAP -13% / Adj -5%` |
| Both, ~equal | `$0.42` | `YoY +91%` (use GAAP) |
| Only GAAP usable | `$0.71` | existing GAAP YoY |
| Only Adj usable | `$1.61 Adj` | Adj YoY (with `Adj` suffix) |
| Neither | `—` | `—` |

The "GAAP" / "Adj" suffix only appears when the dual form is shown OR when
only Adj is available — when GAAP is the sole value (status quo for
non-tech), no suffix to keep the table clean.

YoY pill / "hot" highlighting: triggered if EITHER YoY clears the
`EPS_HOT_PCT` threshold (currently 25%). Bold/hot is a "this is exciting"
signal; we don't want to lose it because GAAP looks tame while Adj is
hot, or vice versa.

### Markdown variant (`renderer.py:996`)

Same logic, plain text output:

```
**Latest Quarter:**  EPS $0.71 GAAP / $1.61 Adj (YoY GAAP -13% / Adj -5%)  ·  Revenue $7.06B (YoY +7.66%)
```

### Worked examples

| Ticker | Display |
|---|---|
| AKAM | `EPS $0.71 GAAP / $1.61 Adj (YoY GAAP -13% / Adj -5%)` |
| DDOG | `EPS $0.15 GAAP / $0.60 Adj (YoY GAAP +114% / Adj +30%)` |
| FROG | `EPS $-0.07 GAAP / $0.27 Adj (YoY GAAP +56% / Adj +23%)` |
| FLNC | `EPS $-0.16 GAAP / $-0.12 Adj (YoY GAAP +50% / Adj +50%)` |
| INOD | `EPS $0.42 (YoY +91%)` |
| MRNA | `EPS $-3.40 (YoY -35%)` |

Whichever number TV happens to display, it's in our row.

### HK behavior

HK tickers go through the same yfinance code path. yfinance
`earnings_dates` for HK varies in coverage (some tickers have it, some
don't). When `Reported EPS` is missing, we fall through to GAAP-only
display — identical to the current behavior for any ticker without
yfinance Adj data.

### Header / column label

Keep the row label `EPS (latest Q)`. The body of the row carries the
GAAP/Adj distinction inline; a column-level label change would be wrong
half the time (single-source rows wouldn't need it).

Add a one-line footnote under the snapshot table when ANY ticker in the
report shows the dual form:

> *EPS shows GAAP / Adjusted when they differ materially. GAAP is from
> SEC 10-Q. Adjusted is the consensus headline (yfinance "Reported EPS").*

## Error handling

- `earnings_dates` raises → existing `try/except pass` swallows it; both
  Adj fields stay None; renderer shows GAAP-only — same as today.
- `earnings_dates` returns empty / no past row → Adj fields stay None;
  GAAP-only display.
- `Reported EPS` is NaN for the latest past row (financial-calendar lag
  on small caps for a day or two) → Adj latest is None; renderer shows
  GAAP-only.
- Prior-year row missing or NaN → Adj YoY is None; renderer shows
  Adj value with `YoY —`.
- Both GAAP and Adj are None → `EPS —`, no YoY pill (today's behavior
  for the IPO group).

## Testing

Unit tests in `tests/test_enrich_eps_adj.py`:
- `earnings_dates` with full history → both fields populated, YoY computed.
- `earnings_dates` empty → both None.
- `earnings_dates` with NaN in `Reported EPS` for latest row → both None.
- `earnings_dates` with prior-year row missing → Adj latest set, YoY None.

Unit tests in `tests/test_renderer_eps_dual.py`:
- GAAP and Adj differ >5% → dual form rendered.
- GAAP and Adj differ <5% → single GAAP value rendered.
- Only GAAP populated → GAAP-only, no suffix.
- Only Adj populated → Adj-only, with `Adj` suffix.
- Both None → `—`, no pill.
- "Hot" pill triggers if EITHER YoY clears threshold.

Regression: existing tests for `_render_quarterly` and the markdown variant
must keep passing — the change is purely additive when only one source is
available.

End-to-end smoke (manual after merge):
- Re-run the 2026-05-07 US sample with the EDGAR cache present.
- Spot-check the 6 tickers in the problem table above against TV.

## Files modified

- `report/enrich.py` — add `eps_latest_q_adj` and `eps_latest_q_adj_yoy_pct`
  to the schema; extend the `t.earnings_dates` block to extract Reported EPS.
- `report/renderer.py` — add `_format_eps_dual` helper; wire into
  `_render_quarterly` (HTML) and the markdown EPS line.
- `tests/test_enrich_eps_adj.py` — new file.
- `tests/test_renderer_eps_dual.py` — new file.
- `CLAUDE.md` — append one paragraph under the report section noting the
  dual-source EPS display and the GAAP/Adj convention.

## Out of scope

- Changing 4-quarter YoY trajectory or 5-year annual YoY data sources.
  These keep using EDGAR (or yfinance fallback) GAAP, because the value
  is in the *trend shape* and EDGAR gives consistent quarter-over-quarter
  comparison; mixing GAAP and Adj across the trajectory would be confusing.
- HK Adjusted EPS coverage — use whatever yfinance has, accept gaps.
- LLM web-search extraction of company-preferred headline EPS — kept as a
  future enhancement if the dual display still doesn't satisfy the
  "looks like TV" check.
