# EPS GAAP / Adjusted Dual Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add company-Adjusted EPS alongside the existing GAAP EPS in the report's "Latest Quarter — EPS" row, so that whichever number TradingView happens to display is visible in our report.

**Architecture:** Two new fields (`eps_latest_q_adj`, `eps_latest_q_adj_yoy_pct`) populated from yfinance's existing `earnings_dates` DataFrame ("Reported EPS" column = consensus/Adjusted convention). Renderer shows both values inline only when they differ by >5% relative to GAAP; otherwise single value as today. EDGAR remains the GAAP source. No new HTTP calls, no new data sources. Spec: `docs/superpowers/specs/2026-05-09-eps-gaap-adj-display-design.md`.

**Tech Stack:** Python, yfinance, pytest. Files: `report/enrich.py`, `report/renderer.py`, `tests/test_report_enrich.py`, `tests/test_report_renderer.py`, `CLAUDE.md`.

---

## File Structure

- `report/enrich.py` — add 2 schema fields and ~10 lines extracting `Reported EPS` inside the existing `t.earnings_dates` block.
- `report/renderer.py` — add `_format_eps_dual` helper (~30 lines); replace inline EPS formatting in `_render_quarterly` (HTML, ~line 656) and `render_markdown_document` (~line 996) with calls to it. Add footnote line under HTML snapshot table when any ticker in the report uses the dual form.
- `tests/test_report_enrich.py` — append 3 tests for the new extraction logic.
- `tests/test_report_renderer.py` — append 4 tests for the dual-display formatter and HTML footnote.
- `CLAUDE.md` — append one paragraph under the "Daily CANSLIM Report" section noting the dual-source EPS convention.

---

### Task 1: Extend ticker schema + extract Adjusted EPS from yfinance earnings_dates

**Files:**
- Modify: `report/enrich.py:178-260`
- Test: `tests/test_report_enrich.py`

- [ ] **Step 1: Write the failing test (full happy path)**

Append to `tests/test_report_enrich.py`:

```python
def test_fetch_ticker_data_extracts_adjusted_eps_from_earnings_dates():
    """yfinance earnings_dates has 'Reported EPS' column = consensus/Adjusted
    convention. We extract latest past row + same row 4 entries earlier for
    YoY. EDGAR GAAP fields stay independent."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "AKAM-mock", "currentPrice": 100, "previousClose": 99}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    # Index: 1 future row + 5 past rows. Past rows oldest→newest:
    # 2025-05 (1.70) → 2025-08 (1.73) → 2025-11 (1.86) → 2026-02 (1.84) → 2026-05 (1.61)
    # Latest past = 2026-05 row, prior-year row = 2025-05 row (4 entries earlier).
    idx = pd.to_datetime([
        "2026-08-06 16:00", "2026-05-07 16:00", "2026-02-19 16:00",
        "2025-11-06 16:00", "2025-08-07 16:00", "2025-05-08 16:00",
    ], utc=True)
    fake_ticker.earnings_dates = pd.DataFrame(
        {"EPS Estimate": [1.61, 1.60, 1.75, 1.64, 1.53, 1.57],
         "Reported EPS": [float("nan"), 1.61, 1.84, 1.86, 1.73, 1.70],
         "Surprise(%)": [float("nan"), 0.35, 5.12, 13.71, 13.18, 8.58]},
        index=idx,
    )
    # freeze "now" so the earnings_dates index filtering is deterministic
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None), \
         patch("report.enrich.pd.Timestamp.now", return_value=pd.Timestamp("2026-05-09", tz="UTC")):
        data = enrich.fetch_ticker_data("AKAM", "Leaders", "NASDAQ", rs_lookup=lambda t: 90)
    assert data["eps_latest_q_adj"] == pytest.approx(1.61)
    # YoY = (1.61 - 1.70) / |1.70| * 100 ≈ -5.29%
    assert data["eps_latest_q_adj_yoy_pct"] == pytest.approx(-5.294, rel=0.01)


def test_fetch_ticker_data_adj_eps_handles_nan_latest_row():
    """Reported EPS is NaN for the just-released earnings (financial calendar
    sometimes lags by hours). Adj fields should be None (not NaN), so the
    renderer falls back to GAAP-only display."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "T", "currentPrice": 100, "previousClose": 99}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    idx = pd.to_datetime([
        "2026-05-08 16:00", "2026-02-08 16:00", "2025-11-08 16:00",
        "2025-08-08 16:00", "2025-05-08 16:00",
    ], utc=True)
    fake_ticker.earnings_dates = pd.DataFrame(
        {"EPS Estimate": [0.50, 0.45, 0.40, 0.35, 0.30],
         "Reported EPS": [float("nan"), 0.46, 0.41, 0.36, 0.31],
         "Surprise(%)": [float("nan"), 2.0, 2.5, 2.5, 3.0]},
        index=idx,
    )
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None), \
         patch("report.enrich.pd.Timestamp.now", return_value=pd.Timestamp("2026-05-09", tz="UTC")):
        data = enrich.fetch_ticker_data("T", "EarningsGap", "NASDAQ", rs_lookup=lambda t: 90)
    # Latest past row IS the 2026-05-08 row. Reported EPS is NaN there.
    # We do NOT silently advance to 2026-02 (that would mislabel the quarter).
    assert data["eps_latest_q_adj"] is None
    assert data["eps_latest_q_adj_yoy_pct"] is None


def test_fetch_ticker_data_adj_eps_missing_prior_year_row():
    """Only 3 past rows = no row 4 entries earlier. Latest is set; YoY None."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "T", "currentPrice": 100, "previousClose": 99}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    idx = pd.to_datetime([
        "2026-05-08 16:00", "2026-02-08 16:00", "2025-11-08 16:00",
    ], utc=True)
    fake_ticker.earnings_dates = pd.DataFrame(
        {"EPS Estimate": [0.50, 0.45, 0.40],
         "Reported EPS": [0.55, 0.46, 0.41],
         "Surprise(%)": [10.0, 2.0, 2.5]},
        index=idx,
    )
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None), \
         patch("report.enrich.pd.Timestamp.now", return_value=pd.Timestamp("2026-05-09", tz="UTC")):
        data = enrich.fetch_ticker_data("T", "Leaders", "NASDAQ", rs_lookup=lambda t: 90)
    assert data["eps_latest_q_adj"] == pytest.approx(0.55)
    assert data["eps_latest_q_adj_yoy_pct"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_enrich.py::test_fetch_ticker_data_extracts_adjusted_eps_from_earnings_dates tests/test_report_enrich.py::test_fetch_ticker_data_adj_eps_handles_nan_latest_row tests/test_report_enrich.py::test_fetch_ticker_data_adj_eps_missing_prior_year_row -v`

Expected: 3 FAIL — `KeyError: 'eps_latest_q_adj'` (field not in schema yet).

- [ ] **Step 3: Add the two new schema fields**

Edit `report/enrich.py:178-215` (the `data` dict in `fetch_ticker_data`). Insert after line 197 (`"revenue_latest_q_yoy_pct": None,`):

```python
        # Company-Adjusted EPS from yfinance earnings_dates "Reported EPS"
        # (consensus convention used by TV's earnings widget). EDGAR's
        # eps_latest_q stays GAAP. See spec
        # docs/superpowers/specs/2026-05-09-eps-gaap-adj-display-design.md.
        "eps_latest_q_adj": None,
        "eps_latest_q_adj_yoy_pct": None,
```

- [ ] **Step 4: Extend the existing earnings_dates block to extract Reported EPS**

Replace `report/enrich.py:242-249` (current block):

```python
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                past = ed[ed.index <= pd.Timestamp.now(tz=ed.index.tz)]
                if not past.empty:
                    data["latest_earnings_date"] = past.index.max()
        except Exception:
            pass
```

with:

```python
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                past = ed[ed.index <= pd.Timestamp.now(tz=ed.index.tz)]
                if not past.empty:
                    data["latest_earnings_date"] = past.index.max()
                    # Reported EPS is yfinance's term for the consensus-reported
                    # (Adjusted) headline number. earnings_dates is sorted
                    # newest→oldest, so latest past row is the first row of
                    # `past`; the same fiscal quarter one year prior sits 4
                    # rows further down.
                    if "Reported EPS" in past.columns:
                        latest_adj = past["Reported EPS"].iloc[0]
                        if pd.notna(latest_adj):
                            data["eps_latest_q_adj"] = float(latest_adj)
                            if len(past) > 4:
                                prior_adj = past["Reported EPS"].iloc[4]
                                if pd.notna(prior_adj):
                                    data["eps_latest_q_adj_yoy_pct"] = compute_yoy(
                                        float(latest_adj), float(prior_adj)
                                    )
        except Exception as e:
            logger.warning(f"[enrich] {t.ticker if hasattr(t, 'ticker') else '?'}: earnings_dates parse failed: {e}")
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_report_enrich.py::test_fetch_ticker_data_extracts_adjusted_eps_from_earnings_dates tests/test_report_enrich.py::test_fetch_ticker_data_adj_eps_handles_nan_latest_row tests/test_report_enrich.py::test_fetch_ticker_data_adj_eps_missing_prior_year_row -v`

Expected: 3 PASS.

- [ ] **Step 6: Run the full enrich test file to verify no regressions**

Run: `uv run pytest tests/test_report_enrich.py -v`

Expected: all green (existing tests use `fake_ticker.earnings_dates = None` or omit `Reported EPS`, so the new branch is dormant for them).

- [ ] **Step 7: Commit**

```bash
git add report/enrich.py tests/test_report_enrich.py
git commit -m "$(cat <<'EOF'
feat(report): extract Adjusted EPS from yfinance earnings_dates

Add `eps_latest_q_adj` / `eps_latest_q_adj_yoy_pct` populated from
yfinance's "Reported EPS" column (consensus headline / Adjusted
convention). EDGAR continues to supply GAAP. Renderer wiring follows
in next commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add `_format_eps_dual` helper to renderer

**Files:**
- Modify: `report/renderer.py` (add helper near the existing `_render_quarterly` at line 656)
- Test: `tests/test_report_renderer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_renderer.py`:

```python
def test_format_eps_dual_shows_both_when_materially_different():
    """AKAM-style: GAAP 0.71, Adj 1.61 → diff = 0.90, ratio = 127% > 5%
    threshold → both rendered with explicit GAAP/Adj suffixes."""
    val_str, yoy_str = renderer._format_eps_dual(
        gaap=0.71, gaap_yoy=-13.41, adj=1.61, adj_yoy=-5.29,
    )
    assert val_str == "$0.71 GAAP / $1.61 Adj"
    assert yoy_str == "YoY GAAP -13.4% / Adj -5.3%"


def test_format_eps_dual_collapses_when_close():
    """INOD-style: GAAP and Adj both 0.42 → ratio 0% < 5% → single value,
    no GAAP/Adj suffix. Uses GAAP for the displayed number."""
    val_str, yoy_str = renderer._format_eps_dual(
        gaap=0.42, gaap_yoy=90.91, adj=0.42, adj_yoy=90.91,
    )
    assert val_str == "$0.42"
    assert yoy_str == "YoY +90.9%"


def test_format_eps_dual_only_gaap():
    """Adj missing (e.g. yfinance has no Reported EPS for this row).
    Single value, no suffix — same as today."""
    val_str, yoy_str = renderer._format_eps_dual(
        gaap=0.42, gaap_yoy=10.0, adj=None, adj_yoy=None,
    )
    assert val_str == "$0.42"
    assert yoy_str == "YoY +10.0%"


def test_format_eps_dual_only_adj():
    """GAAP missing (rare: EDGAR fetch failed but yfinance earnings_dates
    has Reported EPS). Show Adj with explicit suffix."""
    val_str, yoy_str = renderer._format_eps_dual(
        gaap=None, gaap_yoy=None, adj=1.61, adj_yoy=-5.29,
    )
    assert val_str == "$1.61 Adj"
    assert yoy_str == "YoY -5.3% (Adj)"


def test_format_eps_dual_neither_returns_em_dash():
    val_str, yoy_str = renderer._format_eps_dual(
        gaap=None, gaap_yoy=None, adj=None, adj_yoy=None,
    )
    assert val_str == "—"
    assert yoy_str == "YoY —"


def test_format_eps_dual_near_zero_gaap_does_not_blow_up():
    """FROG-style: GAAP -0.07, Adj 0.27 → abs(diff)/max(abs(gaap), 0.01)
    = 0.34/0.07 = 486% → both shown. The 0.01 floor prevents division
    blowup near zero but keeps the >5% test meaningful for tiny GAAP."""
    val_str, yoy_str = renderer._format_eps_dual(
        gaap=-0.07, gaap_yoy=56.25, adj=0.27, adj_yoy=22.73,
    )
    assert val_str == "$-0.07 GAAP / $0.27 Adj"
    assert yoy_str == "YoY GAAP +56.3% / Adj +22.7%"


def test_format_eps_dual_dual_with_one_yoy_missing():
    """Both values present but one YoY None (e.g. Adj has no prior-year row).
    Value-line shows both numbers; YoY-line shows '—' on the missing side."""
    val_str, yoy_str = renderer._format_eps_dual(
        gaap=0.71, gaap_yoy=-13.41, adj=1.61, adj_yoy=None,
    )
    assert val_str == "$0.71 GAAP / $1.61 Adj"
    assert yoy_str == "YoY GAAP -13.4% / Adj —"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_renderer.py::test_format_eps_dual_shows_both_when_materially_different tests/test_report_renderer.py::test_format_eps_dual_collapses_when_close tests/test_report_renderer.py::test_format_eps_dual_only_gaap tests/test_report_renderer.py::test_format_eps_dual_only_adj tests/test_report_renderer.py::test_format_eps_dual_neither_returns_em_dash tests/test_report_renderer.py::test_format_eps_dual_near_zero_gaap_does_not_blow_up tests/test_report_renderer.py::test_format_eps_dual_dual_with_one_yoy_missing -v`

Expected: 7 FAIL — `AttributeError: module 'report.renderer' has no attribute '_format_eps_dual'`.

- [ ] **Step 3: Implement the helper**

Add to `report/renderer.py`. Insert immediately before `def _render_quarterly` (around line 656). Use `_fmt_pct` and `math` which are already imported in this file:

```python
EPS_DUAL_DIFF_THRESHOLD = 0.05  # show both GAAP and Adj when relative diff > 5%
EPS_DUAL_GAAP_FLOOR = 0.01      # avoid div-by-near-zero when GAAP ≈ 0


def _eps_usable(v: float | None) -> bool:
    return isinstance(v, (int, float)) and not (
        isinstance(v, float) and math.isnan(v)
    )


def _format_eps_dual(
    gaap: float | None,
    gaap_yoy: float | None,
    adj: float | None,
    adj_yoy: float | None,
) -> tuple[str, str]:
    """Return (value_str, yoy_str) for the Latest Quarter EPS row.

    Branching:
      - both usable + materially different → "$G GAAP / $A Adj" + "YoY GAAP X / Adj Y"
      - both usable + close                → "$G" + "YoY X"  (no GAAP/Adj suffix)
      - only GAAP                          → "$G" + "YoY X"  (no suffix — status quo)
      - only Adj                           → "$A Adj" + "YoY X (Adj)"
      - neither                            → "—" + "YoY —"
    """
    g_ok, a_ok = _eps_usable(gaap), _eps_usable(adj)
    if not g_ok and not a_ok:
        return ("—", "YoY —")
    if g_ok and not a_ok:
        return (f"${gaap:,.2f}", f"YoY {_fmt_pct(gaap_yoy)}")
    if a_ok and not g_ok:
        return (f"${adj:,.2f} Adj", f"YoY {_fmt_pct(adj_yoy)} (Adj)")
    # Both usable — material-difference test
    denom = max(abs(gaap), EPS_DUAL_GAAP_FLOOR)
    if abs(adj - gaap) / denom <= EPS_DUAL_DIFF_THRESHOLD:
        return (f"${gaap:,.2f}", f"YoY {_fmt_pct(gaap_yoy)}")
    val_str = f"${gaap:,.2f} GAAP / ${adj:,.2f} Adj"
    yoy_str = (
        f"YoY GAAP {_fmt_pct(gaap_yoy)} / Adj {_fmt_pct(adj_yoy)}"
    )
    return (val_str, yoy_str)
```

- [ ] **Step 4: Run helper tests to verify they pass**

Run: `uv run pytest tests/test_report_renderer.py -k "_format_eps_dual" -v`

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add report/renderer.py tests/test_report_renderer.py
git commit -m "$(cat <<'EOF'
feat(report): add _format_eps_dual renderer helper

Pure-formatter helper that decides whether to show GAAP and Adj
side-by-side or a single value, based on a 5% relative-difference
threshold. Wiring into the actual quarterly section follows in the
next commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire `_format_eps_dual` into the HTML quarterly section

**Files:**
- Modify: `report/renderer.py:656-691` (the `_render_quarterly` function)
- Test: `tests/test_report_renderer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_report_renderer.py`:

```python
def test_html_quarterly_shows_dual_eps_when_materially_different():
    """HTML render must display both GAAP and Adj EPS when they diverge."""
    d = _fake_data("AKAM")
    d["eps_latest_q"] = 0.71
    d["eps_latest_q_yoy_pct"] = -13.41
    d["eps_latest_q_adj"] = 1.61
    d["eps_latest_q_adj_yoy_pct"] = -5.29
    html = renderer.render_html_document(
        market="us", date_iso="2026-05-07", enriched=[d],
        prose_sections=["### 公司速览\nbody"], truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "$0.71 GAAP / $1.61 Adj" in html
    assert "YoY GAAP -13.4% / Adj -5.3%" in html


def test_html_quarterly_falls_back_to_single_eps_when_close():
    """When GAAP ≈ Adj (or Adj missing), preserve the existing single-value
    rendering — no GAAP/Adj suffix, no behavior regression."""
    d = _fake_data("INOD")
    d["eps_latest_q"] = 0.42
    d["eps_latest_q_yoy_pct"] = 90.91
    d["eps_latest_q_adj"] = 0.42
    d["eps_latest_q_adj_yoy_pct"] = 90.91
    html = renderer.render_html_document(
        market="us", date_iso="2026-05-07", enriched=[d],
        prose_sections=["### 公司速览\nbody"], truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "$0.42" in html
    assert "GAAP" not in html  # no suffix when collapsed
    assert "Adj" not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_renderer.py::test_html_quarterly_shows_dual_eps_when_materially_different tests/test_report_renderer.py::test_html_quarterly_falls_back_to_single_eps_when_close -v`

Expected: 2 FAIL — first test won't find "GAAP / $1.61 Adj"; second may pass already. Verify the first one fails.

- [ ] **Step 3: Replace EPS-formatting in `_render_quarterly`**

Edit `report/renderer.py`. Replace lines 656-691 (entire `_render_quarterly` function) with:

```python
def _render_quarterly(data: dict[str, Any]) -> str:
    eps_gaap = data.get("eps_latest_q")
    eps_gaap_yoy = data.get("eps_latest_q_yoy_pct")
    eps_adj = data.get("eps_latest_q_adj")
    eps_adj_yoy = data.get("eps_latest_q_adj_yoy_pct")
    # Yahoo's pre-computed MRQ YoY is a coarser fallback when EDGAR YoY
    # is also missing. We keep it on the GAAP side (Yahoo's `earningsGrowth`
    # is from the same income-statement feed as our GAAP source).
    eps_yoy_src = ""
    if eps_gaap_yoy is None and not _eps_usable(eps_adj_yoy):
        eps_gaap_yoy = data.get("yahoo_earnings_growth_yoy_pct")
        eps_yoy_src = " (Yahoo)" if eps_gaap_yoy is not None else ""

    rev = data.get("revenue_latest_q")
    rev_yoy = data.get("revenue_latest_q_yoy_pct")
    if rev_yoy is None:
        rev_yoy = data.get("yahoo_revenue_growth_yoy_pct")
        rev_yoy_src = " (Yahoo)" if rev_yoy is not None else ""
    else:
        rev_yoy_src = ""

    eps_val_str, eps_yoy_str = _format_eps_dual(
        eps_gaap, eps_gaap_yoy, eps_adj, eps_adj_yoy
    )
    rev_str = _fmt_money(rev)

    # Hot pill triggers if EITHER GAAP or Adj YoY clears the threshold.
    eps_hot = " hot" if (
        _is_hot(eps_gaap_yoy, EPS_HOT_PCT) or _is_hot(eps_adj_yoy, EPS_HOT_PCT)
    ) else ""
    rev_hot = " hot" if _is_hot(rev_yoy, REVENUE_HOT_PCT) else ""

    # YoY pill class follows the more-positive of the two YoYs (so a hot Adj
    # value isn't drowned by a tame GAAP). When dual not shown, fallbacks to
    # whichever is non-None (GAAP-first).
    pill_yoy = eps_gaap_yoy if eps_gaap_yoy is not None else eps_adj_yoy
    if (
        _eps_usable(eps_adj_yoy) and _eps_usable(eps_gaap_yoy)
        and abs(eps_adj_yoy) > abs(eps_gaap_yoy)
    ):
        pill_yoy = eps_adj_yoy
    eps_pill = (
        f'<div class="yoy {_yoy_class(pill_yoy)}{eps_hot}">{eps_yoy_str}{eps_yoy_src}</div>'
    )
    rev_pill = (
        f'<div class="yoy {_yoy_class(rev_yoy)}{rev_hot}">YoY {_fmt_pct(rev_yoy)}{rev_yoy_src}</div>'
    )
    return (
        f'<section class="quarterly">'
        f'<div><div class="qtr-label">Latest Quarter — EPS</div>'
        f'<div class="metric-value{eps_hot}">{eps_val_str}</div>{eps_pill}</div>'
        f'<div><div class="qtr-label">Latest Quarter — Revenue</div>'
        f'<div class="metric-value{rev_hot}">{rev_str}</div>{rev_pill}</div>'
        f"</section>"
    )
```

- [ ] **Step 4: Run new tests**

Run: `uv run pytest tests/test_report_renderer.py::test_html_quarterly_shows_dual_eps_when_materially_different tests/test_report_renderer.py::test_html_quarterly_falls_back_to_single_eps_when_close -v`

Expected: 2 PASS.

- [ ] **Step 5: Run full renderer test file for regressions**

Run: `uv run pytest tests/test_report_renderer.py -v`

Expected: all green. Existing tests don't set `eps_latest_q_adj` so the helper falls through the "only GAAP" branch — preserving today's output.

- [ ] **Step 6: Commit**

```bash
git add report/renderer.py tests/test_report_renderer.py
git commit -m "$(cat <<'EOF'
feat(report): wire dual-EPS rendering into HTML quarterly section

Latest Quarter EPS now shows '\$X GAAP / \$Y Adj' when GAAP and yfinance
'Reported EPS' diverge by >5%, single value otherwise. Hot pill triggers
on either YoY clearing threshold. Markdown variant follows next commit.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wire dual EPS into the markdown variant

**Files:**
- Modify: `report/renderer.py` near line 996 (`render_markdown_document`)
- Test: `tests/test_report_renderer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_report_renderer.py`:

```python
def test_md_latest_quarter_shows_dual_eps_when_materially_different():
    d = _fake_data("AKAM")
    d["eps_latest_q"] = 0.71
    d["eps_latest_q_yoy_pct"] = -13.41
    d["eps_latest_q_adj"] = 1.61
    d["eps_latest_q_adj_yoy_pct"] = -5.29
    md = renderer.render_markdown_document(
        market="us", date_iso="2026-05-07", enriched=[d],
        prose_sections=["### 公司速览\nbody"], truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "EPS $0.71 GAAP / $1.61 Adj (YoY GAAP -13.4% / Adj -5.3%)" in md


def test_md_latest_quarter_falls_back_to_single_eps_when_close():
    d = _fake_data("INOD")
    d["eps_latest_q"] = 0.42
    d["eps_latest_q_yoy_pct"] = 90.91
    d["eps_latest_q_adj"] = 0.42
    d["eps_latest_q_adj_yoy_pct"] = 90.91
    md = renderer.render_markdown_document(
        market="us", date_iso="2026-05-07", enriched=[d],
        prose_sections=["### 公司速览\nbody"], truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "EPS $0.42 (YoY +90.9%)" in md
    assert "GAAP" not in md.split("综合判断")[0]  # no suffix in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_renderer.py::test_md_latest_quarter_shows_dual_eps_when_materially_different tests/test_report_renderer.py::test_md_latest_quarter_falls_back_to_single_eps_when_close -v`

Expected: 2 FAIL — first finds the GAAP-only string `EPS $0.71`.

- [ ] **Step 3: Replace the markdown EPS-formatting block**

Edit `report/renderer.py` lines 996-1018 (inclusive). Replace:

```python
    eps = d.get("eps_latest_q")
    eps_usable = isinstance(eps, (int, float)) and not (isinstance(eps, float) and math.isnan(eps))
    eps_str = f"${eps:,.2f}" if eps_usable else "—"
    rev_str = _fmt_money(d.get("revenue_latest_q"))
    eps_yoy = d.get("eps_latest_q_yoy_pct")
    rev_yoy = d.get("revenue_latest_q_yoy_pct")
    if eps_yoy is None:
        eps_yoy = d.get("yahoo_earnings_growth_yoy_pct")
        eps_src = " (Yahoo)" if eps_yoy is not None else ""
    else:
        eps_src = ""
    if rev_yoy is None:
        rev_yoy = d.get("yahoo_revenue_growth_yoy_pct")
        rev_src = " (Yahoo)" if rev_yoy is not None else ""
    else:
        rev_src = ""
    eps_seg = f"EPS {eps_str} (YoY {_fmt_pct(eps_yoy)}{eps_src})"
    rev_seg = f"Revenue {rev_str} (YoY {_fmt_pct(rev_yoy)}{rev_src})"
    if _is_hot(eps_yoy, EPS_HOT_PCT):
        eps_seg = f"**{eps_seg}**"
    if _is_hot(rev_yoy, REVENUE_HOT_PCT):
        rev_seg = f"**{rev_seg}**"
    qtr = f"**Latest Quarter:**  {eps_seg}  ·  {rev_seg}\n\n"
```

with:

```python
    eps_gaap = d.get("eps_latest_q")
    eps_gaap_yoy = d.get("eps_latest_q_yoy_pct")
    eps_adj = d.get("eps_latest_q_adj")
    eps_adj_yoy = d.get("eps_latest_q_adj_yoy_pct")
    if eps_gaap_yoy is None and not _eps_usable(eps_adj_yoy):
        eps_gaap_yoy = d.get("yahoo_earnings_growth_yoy_pct")
        eps_yoy_src = " (Yahoo)" if eps_gaap_yoy is not None else ""
    else:
        eps_yoy_src = ""
    rev_str = _fmt_money(d.get("revenue_latest_q"))
    rev_yoy = d.get("revenue_latest_q_yoy_pct")
    if rev_yoy is None:
        rev_yoy = d.get("yahoo_revenue_growth_yoy_pct")
        rev_src = " (Yahoo)" if rev_yoy is not None else ""
    else:
        rev_src = ""
    eps_val_str, eps_yoy_str = _format_eps_dual(
        eps_gaap, eps_gaap_yoy, eps_adj, eps_adj_yoy
    )
    eps_seg = f"EPS {eps_val_str} ({eps_yoy_str}{eps_yoy_src})"
    rev_seg = f"Revenue {rev_str} (YoY {_fmt_pct(rev_yoy)}{rev_src})"
    if _is_hot(eps_gaap_yoy, EPS_HOT_PCT) or _is_hot(eps_adj_yoy, EPS_HOT_PCT):
        eps_seg = f"**{eps_seg}**"
    if _is_hot(rev_yoy, REVENUE_HOT_PCT):
        rev_seg = f"**{rev_seg}**"
    qtr = f"**Latest Quarter:**  {eps_seg}  ·  {rev_seg}\n\n"
```

- [ ] **Step 4: Run new tests**

Run: `uv run pytest tests/test_report_renderer.py::test_md_latest_quarter_shows_dual_eps_when_materially_different tests/test_report_renderer.py::test_md_latest_quarter_falls_back_to_single_eps_when_close -v`

Expected: 2 PASS.

- [ ] **Step 5: Run full renderer test file**

Run: `uv run pytest tests/test_report_renderer.py -v`

Expected: all green. The pre-existing markdown bold-test (`test_md_latest_quarter_bolds_when_above_thresholds`) still asserts the exact string `**EPS $1.25 (YoY +30.0%)**`; that fixture sets only the GAAP fields so `_format_eps_dual` returns `($1.25, YoY +30.0%)` and the assertion still holds.

- [ ] **Step 6: Commit**

```bash
git add report/renderer.py tests/test_report_renderer.py
git commit -m "$(cat <<'EOF'
feat(report): wire dual-EPS rendering into markdown variant

Markdown report now mirrors the HTML behavior: 'EPS \$X GAAP / \$Y Adj
(YoY GAAP A% / Adj B%)' when divergent, single value otherwise. Bold
'hot' triggers on either YoY clearing threshold.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Add HTML footnote when any ticker uses dual form + update CLAUDE.md

**Files:**
- Modify: `report/renderer.py` (HTML document assembly, near `render_html_document`)
- Modify: `CLAUDE.md`
- Test: `tests/test_report_renderer.py`

- [ ] **Step 1: Locate the HTML document assembly point**

Run: `grep -n "def render_html_document\|render_html_document\|<footer" /Users/xue/finviz_to_tv/report/renderer.py | head -10`

Open `report/renderer.py` at the matching line and read the surrounding 30-50 lines to understand where to add the footnote (after the snapshot tables / before the closing `</body>` / wherever footer markup currently lives).

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_report_renderer.py`:

```python
def test_html_footnote_appears_when_any_ticker_shows_dual_eps():
    """The 'GAAP / Adjusted' explainer footnote is conditional — it must NOT
    clutter reports where every ticker is single-source, but should appear
    once (not per-ticker) when any ticker shows the dual form."""
    d_dual = _fake_data("AKAM")
    d_dual["eps_latest_q"] = 0.71
    d_dual["eps_latest_q_yoy_pct"] = -13.41
    d_dual["eps_latest_q_adj"] = 1.61
    d_dual["eps_latest_q_adj_yoy_pct"] = -5.29
    d_single = _fake_data("INOD")
    d_single["eps_latest_q"] = 0.42
    d_single["eps_latest_q_yoy_pct"] = 90.91
    html = renderer.render_html_document(
        market="us", date_iso="2026-05-07", enriched=[d_dual, d_single],
        prose_sections=["### 公司速览\nbody", "### 公司速览\nbody"], truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "EPS shows GAAP / Adjusted when they differ" in html
    # Footnote appears exactly once (not duplicated per ticker section).
    assert html.count("EPS shows GAAP / Adjusted when they differ") == 1


def test_html_footnote_absent_when_no_ticker_shows_dual_eps():
    d = _fake_data("INOD")
    d["eps_latest_q"] = 0.42
    d["eps_latest_q_yoy_pct"] = 90.91
    html = renderer.render_html_document(
        market="us", date_iso="2026-05-07", enriched=[d],
        prose_sections=["### 公司速览\nbody"], truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "EPS shows GAAP / Adjusted when they differ" not in html
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_report_renderer.py::test_html_footnote_appears_when_any_ticker_shows_dual_eps tests/test_report_renderer.py::test_html_footnote_absent_when_no_ticker_shows_dual_eps -v`

Expected: first FAIL (no footnote text yet), second PASS (string is genuinely absent).

- [ ] **Step 4: Add a small dual-detector helper near the formatter**

Add to `report/renderer.py`, immediately after `_format_eps_dual`:

```python
def _is_eps_dual(d: dict[str, Any]) -> bool:
    """True iff this ticker's data would render as the dual GAAP / Adj form.
    Mirror of the threshold logic in _format_eps_dual; kept as a separate
    pure function so the document-level header can decide whether to emit
    the explainer footnote without re-running the renderer."""
    g = d.get("eps_latest_q")
    a = d.get("eps_latest_q_adj")
    if not _eps_usable(g) or not _eps_usable(a):
        return False
    denom = max(abs(g), EPS_DUAL_GAAP_FLOOR)
    return abs(a - g) / denom > EPS_DUAL_DIFF_THRESHOLD
```

- [ ] **Step 5: Wire the footnote into HTML document assembly**

Locate `render_html_document` in `report/renderer.py` (use the line number from Step 1). Find the place where per-ticker sections are joined into the document body (typically a `"".join(...)` over ticker sections or a string template). Just before the closing `</body>` (or wherever the document-level footer / model-label string is appended), add:

```python
    eps_footnote_html = ""
    if any(_is_eps_dual(d) for d in enriched):
        eps_footnote_html = (
            '<p class="eps-footnote" style="font-size:0.85em;color:#666;'
            'margin-top:1em;">EPS shows GAAP / Adjusted when they differ '
            'materially. GAAP is from SEC 10-Q. Adjusted is the consensus '
            'headline (yfinance "Reported EPS").</p>'
        )
```

Then splice `eps_footnote_html` into the assembled document just before the existing footer/closing tags. If the function uses an f-string template, add `{eps_footnote_html}` at the appropriate spot. If it uses string concatenation, append the variable. Keep changes minimal — do not refactor the function.

- [ ] **Step 6: Run footnote tests**

Run: `uv run pytest tests/test_report_renderer.py::test_html_footnote_appears_when_any_ticker_shows_dual_eps tests/test_report_renderer.py::test_html_footnote_absent_when_no_ticker_shows_dual_eps -v`

Expected: 2 PASS.

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest tests/ -v`

Expected: all green.

- [ ] **Step 8: Update CLAUDE.md**

Open `/Users/xue/finviz_to_tv/CLAUDE.md`. Find the paragraph in the "Daily CANSLIM Report" section that lists the EDGAR-sourced fundamentals (look for the sentence containing "Per-ticker structured data (Market Cap, EPS, Revenue, **5-year annual YoY**, ..."). Append immediately after that sentence, in the same paragraph:

```
Latest-quarter EPS additionally pulls **Adjusted (consensus headline)** EPS from yfinance `earnings_dates`'s "Reported EPS" column; when GAAP and Adjusted diverge by more than 5% (relative to GAAP, with a 0.01 floor) the report shows both side by side (`$X GAAP / $Y Adj`) and adds a one-line footnote under the snapshot tables. This pairs the strict EDGAR GAAP number with whatever TV/news widgets show as headline, since TV's display follows the company's press-release headline (which is non-GAAP for most SaaS/tech and GAAP for most loss-makers — there's no single feed that picks correctly per ticker, so we show both).
```

- [ ] **Step 9: Commit**

```bash
git add report/renderer.py tests/test_report_renderer.py CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(report): conditional GAAP/Adj explainer footnote + docs

HTML output now appends a one-line footnote under the snapshot tables
whenever any ticker in the report renders the dual GAAP / Adj form.
Single-source reports stay clean. CLAUDE.md updated to describe the
dual-EPS convention.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: End-to-end smoke test against today's data

**Files:**
- No code changes. Manual verification.

- [ ] **Step 1: Re-run the report on the existing 2026-05-07 US sample**

Run: `uv run python -m report --market us --date 2026-05-07 2>&1 | tail -40`

(If the entry-point flag differs, check `report/__main__.py`; otherwise run whichever command produces today's `output/Reports/2026_05_07_us.{md,html}`.)

Expected: report regenerates without error.

- [ ] **Step 2: Spot-check the 6 reference tickers**

Run: `grep -E "^## (AKAM|DDOG|FROG|FLNC|INOD|MRNA)" /Users/xue/finviz_to_tv/output/Reports/2026_05_07_us.md`

Then `grep -A 2 "EPS (latest Q)\|Latest Quarter — EPS" /Users/xue/finviz_to_tv/output/Reports/2026_05_07_us.md | head -60` and confirm each ticker matches the worked-examples table in the spec:
- AKAM: `$0.71 GAAP / $1.61 Adj`
- DDOG: `$0.15 GAAP / $0.60 Adj`
- FROG: `$-0.07 GAAP / $0.27 Adj`
- FLNC: `$-0.16 GAAP / $-0.12 Adj`
- INOD: `$0.42` (single)
- MRNA: `$-3.40` (single)

If any mismatch, debug — likely a NaN-handling edge case or a yfinance schema drift.

- [ ] **Step 3: Verify HTML footnote presence**

Run: `grep "EPS shows GAAP / Adjusted" /Users/xue/finviz_to_tv/output/Reports/2026_05_07_us.html`

Expected: one match (since AKAM/DDOG/FROG/FLNC all use dual form).

- [ ] **Step 4: Commit if any minor follow-up tweaks were needed**

Only commit if Step 2/3 surfaced a real bug. Otherwise the work is complete.

```bash
git status   # confirm working tree clean
```

---

## Self-Review

**Spec coverage:** ✓ Schema fields (Task 1) ✓ extraction logic (Task 1) ✓ display threshold (Task 2) ✓ HTML rendering (Task 3) ✓ Markdown rendering (Task 4) ✓ conditional footnote (Task 5) ✓ tests for all branches (Tasks 1–5) ✓ docs (Task 5) ✓ smoke validation (Task 6).

**Placeholders:** None. Every code step has full code; every test step has full assertions; the only "find this place" instruction (Task 5 Step 1) explicitly hands the engineer a `grep` to locate the splice point.

**Type consistency:** New fields `eps_latest_q_adj` / `eps_latest_q_adj_yoy_pct` are referenced consistently across enrich.py, renderer helper, HTML wiring, markdown wiring, and the dual-detector helper. Helper signature `_format_eps_dual(gaap, gaap_yoy, adj, adj_yoy) -> tuple[str, str]` matches all call sites. Threshold constants `EPS_DUAL_DIFF_THRESHOLD` and `EPS_DUAL_GAAP_FLOOR` are defined once at module scope (Task 2) and used by both `_format_eps_dual` (Task 2) and `_is_eps_dual` (Task 5).
