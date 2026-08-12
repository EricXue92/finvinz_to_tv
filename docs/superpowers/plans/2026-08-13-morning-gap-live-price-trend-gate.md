# Morning Gap Live-Price Trend Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the morning-gap SMA50/SMA200 trend gate compare against the live pre-market/intraday price instead of yesterday's close, and let a gap ≥ 10% exempt SMA50 (never SMA200).

**Architecture:** The two Futu discovery functions start returning a `dict[str, GapQuote]` (price + gap per ticker) instead of a bare `list[str]`. `_filter_sma_trend` gains three optional keyword parameters (`live_prices`, `gaps`, `bypass_gap_pct`) that change only the _comparison basis_ and the SMA50 requirement — the moving averages themselves are still computed from completed daily bars via `_trim_today`. With all three omitted the function behaves exactly as it does today, which is the rollback path.

**Tech Stack:** Python 3.12, `uv` for deps, pytest, pandas, futu-api, yfinance.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-13-morning-gap-live-price-trend-gate-design.md`.
- Run tests with `uv run python -m pytest` — plain `uv run pytest` fails to spawn in this repo.
- Moving averages MUST keep being computed from `_trim_today`-trimmed completed bars. Only the comparison basis changes.
- SMA200 is never exemptible. The bypass applies to SMA50 only.
- Discovery failures stay soft: `pre_price` → `last_price` → prev close, never raise. A ticker missing from `gaps` takes the strict path.
- `discover_*_candidates` keep returning `None` for failure and an empty container for "no candidates", so caller branches `if discovery is None` and `if not tickers` keep their meaning.
- EOD paths must not change. `_filter_sma_trend` has exactly two callers, both morning-gap (`main.py:808`, `main.py:994`).
- Do not touch `pre_market_gap_futu` or `_filter_pre_market_gap` — both are already dead code with no call sites.

---

### Task 1: `GapQuote` and the trend-gate parameters

The core logic, testable with zero Futu/network involvement. Discovery plumbing comes in Task 2 so this task can be reviewed on its own.

**Files:**

- Modify: `futu_sync.py` (add `GapQuote` near the top, after the `logger` line at line 15)
- Modify: `main.py:1342-1387` (`_filter_sma_trend`)
- Test: `tests/test_morning_gap_sma.py` (create)

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces:
  - `futu_sync.GapQuote(price: float, gap: float)` — a `NamedTuple`; Task 2 constructs these, Task 3 reads `.price` / `.gap`.
  - `main._filter_sma_trend(tickers, daily_data, today_date, sma_short=50, sma_long=200, market_open=True, single=None, live_prices=None, gaps=None, bypass_gap_pct=None) -> list[str]` — Task 3 calls it with the three new keywords.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_morning_gap_sma.py`:

```python
"""Trend-gate behavior for morning-gap: live-price basis + big-gap SMA50 bypass.

Fixtures mirror the real 2026-08-12 pre-market scan, where the gate dropped 67
of 85 survivors — including NBIS and CRWV, both of which gapped hard and
reclaimed their moving averages on the very bar `_trim_today` removes.
"""

import pandas as pd
import pytest

from main import _filter_sma_trend

TODAY = pd.Timestamp("2026-08-12").date()


def _frame(ticker: str, closes: list[float]) -> pd.DataFrame:
    """yfinance batch shape: MultiIndex columns (ticker, field).

    The last row is dated TODAY so `_trim_today` drops it, exactly as it does
    during a live intraday scan.
    """
    idx = pd.date_range(end="2026-08-12", periods=len(closes), freq="B")
    cols = pd.MultiIndex.from_product([[ticker], ["Close"]])
    return pd.DataFrame({(ticker, "Close"): closes}, index=idx, columns=cols)


def _flat(ticker: str, level: float, last_completed: float, today: float):
    """250 bars: a flat run at `level`, yesterday's close, then today's partial
    bar. SMA50 and SMA200 both come out at ~`level`."""
    closes = [level] * 248 + [last_completed, today]
    return _frame(ticker, closes)


def test_live_price_rescues_nbis_shape():
    """NBIS 2026-08-12: prev close 193.23 under SMA50, live 238 over both."""
    data = _flat("NBIS", 200.0, 193.23, 238.25)
    kept = _filter_sma_trend(
        ["NBIS"], data, TODAY, single=False,
        live_prices={"NBIS": 238.25},
    )
    assert kept == ["NBIS"]


def test_live_price_rescues_crwv_shape():
    """CRWV 2026-08-12: prev close 90.32 under both MAs, live 106 over both."""
    data = _flat("CRWV", 95.0, 90.32, 106.46)
    kept = _filter_sma_trend(
        ["CRWV"], data, TODAY, single=False,
        live_prices={"CRWV": 106.46},
    )
    assert kept == ["CRWV"]


def test_no_live_prices_falls_back_to_prev_close():
    """Omitting live_prices reproduces today's behavior byte-for-byte."""
    data = _flat("CRWV", 95.0, 90.32, 106.46)
    assert _filter_sma_trend(["CRWV"], data, TODAY, single=False) == []


def test_live_price_missing_for_ticker_falls_back():
    """A ticker absent from live_prices takes the prev-close path, not a crash."""
    data = _flat("CRWV", 95.0, 90.32, 106.46)
    kept = _filter_sma_trend(
        ["CRWV"], data, TODAY, single=False, live_prices={"OTHER": 500.0},
    )
    assert kept == []


def test_nonpositive_live_price_falls_back():
    """pre_price arrives as 0 when Futu reports N/A — must not be trusted."""
    data = _flat("NBIS", 200.0, 193.23, 238.25)
    kept = _filter_sma_trend(
        ["NBIS"], data, TODAY, single=False, live_prices={"NBIS": 0.0},
    )
    assert kept == []


def test_bypass_exempts_sma50_when_gap_large():
    """gap 12%, live above SMA200 but below SMA50 → kept."""
    # 200 bars at 100 then 50 bars at 300 → SMA200 ~150, SMA50 ~300.
    closes = [100.0] * 199 + [300.0] * 49 + [180.0, 190.0]
    data = _frame("XYZ", closes)
    kept = _filter_sma_trend(
        ["XYZ"], data, TODAY, single=False,
        live_prices={"XYZ": 190.0}, gaps={"XYZ": 12.0}, bypass_gap_pct=10.0,
    )
    assert kept == ["XYZ"]


def test_bypass_never_exempts_sma200():
    """gap 12% but live below SMA200 → still dropped."""
    closes = [100.0] * 199 + [300.0] * 49 + [90.0, 95.0]
    data = _frame("XYZ", closes)
    kept = _filter_sma_trend(
        ["XYZ"], data, TODAY, single=False,
        live_prices={"XYZ": 95.0}, gaps={"XYZ": 12.0}, bypass_gap_pct=10.0,
    )
    assert kept == []


def test_gap_below_bypass_threshold_still_gated():
    """gap 8% under a 10% threshold → SMA50 still enforced."""
    closes = [100.0] * 199 + [300.0] * 49 + [180.0, 190.0]
    data = _frame("XYZ", closes)
    kept = _filter_sma_trend(
        ["XYZ"], data, TODAY, single=False,
        live_prices={"XYZ": 190.0}, gaps={"XYZ": 8.0}, bypass_gap_pct=10.0,
    )
    assert kept == []


def test_bypass_disabled_when_threshold_zero_or_none():
    """bypass_gap_pct 0 / None both mean 'no exemption'."""
    closes = [100.0] * 199 + [300.0] * 49 + [180.0, 190.0]
    data = _frame("XYZ", closes)
    for pct in (0, None):
        kept = _filter_sma_trend(
            ["XYZ"], data, TODAY, single=False,
            live_prices={"XYZ": 190.0}, gaps={"XYZ": 12.0}, bypass_gap_pct=pct,
        )
        assert kept == [], f"bypass_gap_pct={pct!r} must not exempt SMA50"


def test_ticker_missing_from_gaps_takes_strict_path():
    closes = [100.0] * 199 + [300.0] * 49 + [180.0, 190.0]
    data = _frame("XYZ", closes)
    kept = _filter_sma_trend(
        ["XYZ"], data, TODAY, single=False,
        live_prices={"XYZ": 190.0}, gaps={}, bypass_gap_pct=10.0,
    )
    assert kept == []


def test_insufficient_history_still_dropped():
    """Under 200 completed bars → dropped, regardless of live price or gap."""
    data = _frame("NEW", [100.0] * 60 + [500.0])
    kept = _filter_sma_trend(
        ["NEW"], data, TODAY, single=False,
        live_prices={"NEW": 500.0}, gaps={"NEW": 50.0}, bypass_gap_pct=10.0,
    )
    assert kept == []


def test_gapquote_namedtuple_fields():
    from futu_sync import GapQuote

    q = GapQuote(price=106.46, gap=17.9)
    assert q.price == 106.46
    assert q.gap == 17.9
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_morning_gap_sma.py -v`

Expected: `test_gapquote_namedtuple_fields` fails with `ImportError: cannot import name 'GapQuote'`; the bypass and live-price tests fail with `TypeError: _filter_sma_trend() got an unexpected keyword argument 'live_prices'`. `test_no_live_prices_falls_back_to_prev_close` should already PASS — it pins current behavior.

- [ ] **Step 3: Add `GapQuote` to `futu_sync.py`**

Change the import line near the top of `futu_sync.py` from `from typing import Literal` to:

```python
from typing import Literal, NamedTuple
```

Then insert after `logger = logging.getLogger(__name__)`:

```python
class GapQuote(NamedTuple):
    """One discovery survivor's live comparison basis and its gap.

    ``price`` is what the trader is looking at right now — the pre-market
    print during a negative-offset scan, the last trade during a post-open
    one. The morning-gap trend gate compares against it instead of the
    previous close, which is blind to the very move that surfaced the ticker.
    ``gap`` is the same percentage the discovery threshold was applied to.
    """

    price: float
    gap: float
```

- [ ] **Step 4: Rewrite `_filter_sma_trend` in `main.py`**

Replace the whole function at `main.py:1342-1387` with:

```python
def _filter_sma_trend(
    tickers: list[str],
    daily_data,
    today_date,
    sma_short: int = 50,
    sma_long: int = 200,
    market_open: bool = True,
    single: bool | None = None,
    live_prices: dict[str, float] | None = None,
    gaps: dict[str, float] | None = None,
    bypass_gap_pct: float | None = None,
) -> list[str]:
    """Keep tickers trading above both SMA50 and SMA200. Replaces Finviz
    `ta_sma50_pa`/`ta_sma200_pa` for the Futu-discovery path. Strict: tickers
    with insufficient history (< sma_long bars after trimming today's partial
    bar) are dropped.

    The moving averages are always computed from completed bars — a half-formed
    daily bar must never enter a 50/200-day mean. Only the *comparison basis*
    is configurable: pass `live_prices` to compare the pre-market / intraday
    price instead of the last completed close, which is what the trader is
    actually looking at during a morning-gap scan. A ticker absent from
    `live_prices`, or carrying a non-positive value there, falls back to the
    close.

    `bypass_gap_pct` exempts a big gapper from the SMA50 requirement only —
    the SMA200 floor is never waived. This targets the long-term-uptrend /
    mid-term-pullback / event-gap shape without admitting bottom-bounces in
    genuine downtrends. Tickers missing from `gaps` take the strict path.

    With all three new parameters omitted, behavior is identical to the
    pre-2026-08-13 gate.
    """
    if not tickers:
        return []
    if single is None:
        single = len(tickers) == 1

    result = []
    for ticker in tickers:
        try:
            if single:
                closes = daily_data["Close"].dropna()
            else:
                closes = daily_data[ticker]["Close"].dropna()
            closes = _trim_today(closes, market_open, today_date)
            if len(closes) < sma_long:
                logger.warning(
                    f"  {ticker}: insufficient daily bars for SMA{sma_long} "
                    f"({len(closes)}<{sma_long}), dropping"
                )
                continue
            last_close = float(closes.iloc[-1])
            sma_s = float(closes.iloc[-sma_short:].mean())
            sma_l = float(closes.iloc[-sma_long:].mean())

            ref, basis = last_close, "close"
            live = (live_prices or {}).get(ticker)
            if live is not None:
                try:
                    live = float(live)
                except (TypeError, ValueError):
                    live = None
                if live is not None and live > 0:
                    ref, basis = live, "live"

            gap = (gaps or {}).get(ticker)
            bypass = (
                bypass_gap_pct is not None
                and bypass_gap_pct > 0
                and gap is not None
                and gap >= bypass_gap_pct
            )

            if ref < sma_l:
                logger.info(
                    f"  {ticker}: {basis} {ref:.2f} below SMA{sma_long}={sma_l:.2f}"
                    f"{' (gap bypass does not waive SMA200)' if bypass else ''}"
                    ", dropping"
                )
                continue
            if ref < sma_s and not bypass:
                logger.info(
                    f"  {ticker}: {basis} {ref:.2f} below SMA{sma_short}={sma_s:.2f}"
                    ", dropping"
                )
                continue
            if ref < sma_s and bypass:
                logger.info(
                    f"  {ticker}: {basis} {ref:.2f} below SMA{sma_short}={sma_s:.2f} "
                    f"but gap {gap:.1f}% >= {bypass_gap_pct}%, SMA{sma_short} waived"
                )
            result.append(ticker)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"  {ticker}: SMA trend check failed ({e}), dropping")

    return result
```

- [ ] **Step 5: Run the new tests and the full suite**

Run: `uv run python -m pytest tests/test_morning_gap_sma.py -v`
Expected: all 12 PASS.

Run: `uv run python -m pytest tests/ -q`
Expected: 380 passed (368 existing + 12 new), 0 failed.

- [ ] **Step 6: Commit**

```bash
git add tests/test_morning_gap_sma.py main.py futu_sync.py
git commit -m "feat(morning_gap): trend gate accepts live-price basis + SMA50 gap bypass

_filter_sma_trend gains live_prices / gaps / bypass_gap_pct, all optional
and all defaulting to the previous behavior. Moving averages still come
from completed bars; only the comparison basis moves. SMA200 is never
waived. Adds GapQuote to futu_sync for the next task.

First tests for _filter_sma_trend (previously zero coverage)."
```

---

### Task 2: Discovery returns `dict[str, GapQuote]`

**Files:**

- Modify: `futu_sync.py:368-491` (`discover_morning_gap_candidates`)
- Modify: `futu_sync.py:493-604` (`discover_hk_morning_gap_candidates`)
- Modify: `main.py:750-765` and `main.py:938-955` (call sites, minimally — just enough to keep the pipeline running; the trend-gate wiring is Task 3)
- Test: `tests/test_morning_gap_sma.py` (append)

**Interfaces:**

- Consumes: `futu_sync.GapQuote` from Task 1.
- Produces:
  - `discover_morning_gap_candidates(...) -> dict[str, GapQuote] | None`
  - `discover_hk_morning_gap_candidates(...) -> dict[str, GapQuote] | None`
  - Both keyed by the same ticker strings they used to return in a list, in the same order. Task 3 reads `.price` and `.gap` off the values.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_morning_gap_sma.py`:

```python
class _FakeSnapshotCtx:
    """Stands in for futu.OpenQuoteContext. Returns one basicinfo frame and
    one snapshot frame, both built from plain dicts."""

    def __init__(self, basic_rows, snap_rows):
        self._basic = pd.DataFrame(basic_rows)
        self._snap = pd.DataFrame(snap_rows)
        self.closed = False

    def get_stock_basicinfo(self, **kwargs):
        return 0, self._basic

    def get_market_snapshot(self, codes):
        return 0, self._snap[self._snap["code"].isin(codes)]

    def close(self):
        self.closed = True


def _install_fake_futu(monkeypatch, basic_rows, snap_rows):
    """Patch out the futu import, the TCP probe, and OpenQuoteContext."""
    import sys
    import types

    import futu_sync

    ctx = _FakeSnapshotCtx(basic_rows, snap_rows)
    fake = types.SimpleNamespace(
        OpenQuoteContext=lambda host, port: ctx,
        RET_OK=0,
        Market=types.SimpleNamespace(US="US", HK="HK"),
        SecurityType=types.SimpleNamespace(STOCK="STOCK"),
    )
    monkeypatch.setitem(sys.modules, "futu", fake)
    monkeypatch.setattr(futu_sync, "_opend_reachable", lambda h, p, **kw: True)
    return ctx


US_BASIC = [
    {"code": "US.NBIS", "exchange_type": "US_NASDAQ", "delisting": False},
    {"code": "US.SMALL", "exchange_type": "US_NASDAQ", "delisting": False},
]


def test_us_discovery_premarket_returns_pre_price_and_gap(monkeypatch):
    from futu_sync import discover_morning_gap_candidates

    _install_fake_futu(monkeypatch, US_BASIC, [
        {"code": "US.NBIS", "total_market_val": 5e10, "last_price": 193.23,
         "prev_close_price": 193.23, "pre_price": 238.25,
         "pre_change_rate": 23.3, "pre_volume": 100000},
        {"code": "US.SMALL", "total_market_val": 1e6, "last_price": 5.0,
         "prev_close_price": 4.0, "pre_price": 5.0,
         "pre_change_rate": 25.0, "pre_volume": 100},
    ])
    out = discover_morning_gap_candidates(
        min_gap_pct=5.0, min_market_cap=3e8, min_price=20.0,
        pre_market=True, exchanges=["US_NASDAQ"],
    )
    assert list(out) == ["NBIS"]                 # SMALL fails cap + price
    assert out["NBIS"].price == pytest.approx(238.25)
    assert out["NBIS"].gap == pytest.approx(23.3)


def test_us_discovery_premarket_falls_back_to_last_price(monkeypatch):
    """pre_price arrives as the string 'N/A' outside the pre-auction window."""
    from futu_sync import discover_morning_gap_candidates

    _install_fake_futu(monkeypatch, US_BASIC[:1], [
        {"code": "US.NBIS", "total_market_val": 5e10, "last_price": 193.23,
         "prev_close_price": 193.23, "pre_price": "N/A",
         "pre_change_rate": 23.3, "pre_volume": 100000},
    ])
    out = discover_morning_gap_candidates(
        min_gap_pct=5.0, min_market_cap=3e8, min_price=20.0,
        pre_market=True, exchanges=["US_NASDAQ"],
    )
    assert out["NBIS"].price == pytest.approx(193.23)


def test_us_discovery_postopen_uses_last_price_and_derived_gap(monkeypatch):
    from futu_sync import discover_morning_gap_candidates

    _install_fake_futu(monkeypatch, US_BASIC[:1], [
        {"code": "US.NBIS", "total_market_val": 5e10, "last_price": 238.25,
         "prev_close_price": 193.23, "pre_price": "N/A",
         "pre_change_rate": "N/A", "pre_volume": 0},
    ])
    out = discover_morning_gap_candidates(
        min_gap_pct=5.0, min_market_cap=3e8, min_price=20.0,
        pre_market=False, exchanges=["US_NASDAQ"],
    )
    assert out["NBIS"].price == pytest.approx(238.25)
    assert out["NBIS"].gap == pytest.approx(23.30, abs=0.01)


def test_hk_discovery_returns_quotes(monkeypatch):
    from futu_sync import discover_hk_morning_gap_candidates

    _install_fake_futu(
        monkeypatch,
        [{"code": "HK.00700", "exchange_type": "HK_MAINBOARD", "delisting": False}],
        [{"code": "HK.00700", "total_market_val": 5e11, "last_price": 660.0,
          "prev_close_price": 600.0}],
    )
    out = discover_hk_morning_gap_candidates(
        min_gap_pct=5.0, min_market_cap=3e8, min_price=20.0,
        exchanges=["HK_MAINBOARD"],
    )
    assert list(out) == ["0700.HK"]
    assert out["0700.HK"].price == pytest.approx(660.0)
    assert out["0700.HK"].gap == pytest.approx(10.0)


def test_discovery_returns_none_when_opend_unreachable(monkeypatch):
    import futu_sync
    from futu_sync import discover_morning_gap_candidates

    monkeypatch.setattr(futu_sync, "_opend_reachable", lambda h, p, **kw: False)
    out = discover_morning_gap_candidates(
        min_gap_pct=5.0, min_market_cap=3e8, min_price=20.0,
        pre_market=True, exchanges=["US_NASDAQ"],
    )
    assert out is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_morning_gap_sma.py -k discovery -v`
Expected: FAIL — `TypeError: list indices must be integers or slices, not str` (the functions still return lists), except `test_discovery_returns_none_when_opend_unreachable` which already passes.

- [ ] **Step 3: Change the US discovery function**

In `futu_sync.py`, change the signature's return annotation at line 376 from
`) -> list[str] | None:` to `) -> dict[str, GapQuote] | None:`.

Change the docstring's closing paragraph (lines 390-392) to:

```
    Returns ``{plain_ticker: GapQuote}`` (e.g. ``{"TWLO": GapQuote(...)}``,
    not ``"US.TWLO"``), insertion-ordered. ``price`` is ``pre_price`` during a
    pre-market scan (falling back to ``last_price`` when Futu reports
    ``N/A``) and ``last_price`` post-open. Returns ``None`` on any failure so
    callers can decide whether to fall back. Logs a single warning per
    failure mode.
```

Replace `survivors: list[str] = []` (line 433) with:

```python
        survivors: dict[str, GapQuote] = {}
```

Replace the pre-market / post-open branch and the append (lines 455-473) with:

```python
                if pre_market:
                    try:
                        pre_vol = float(row.get("pre_volume", 0) or 0)
                        gap = float(row.get("pre_change_rate"))
                    except (TypeError, ValueError):
                        continue
                    if pre_vol <= 0 or gap < min_gap_pct:
                        continue
                    # pre_price is "N/A" outside the pre-auction window; the
                    # last regular-session trade is the honest fallback.
                    try:
                        basis = float(row.get("pre_price"))
                    except (TypeError, ValueError):
                        basis = 0.0
                    if basis <= 0:
                        basis = price
                else:
                    try:
                        prev_close = float(row.get("prev_close_price", 0) or 0)
                        if prev_close <= 0:
                            continue
                        gap = (price - prev_close) / prev_close * 100.0
                    except (TypeError, ValueError):
                        continue
                    if gap < min_gap_pct:
                        continue
                    basis = price
                survivors[code[len("US."):]] = GapQuote(price=basis, gap=gap)
```

Note `return []` at line 431 becomes `return {}`.

- [ ] **Step 4: Change the HK discovery function**

In `futu_sync.py`, change the return annotation at line 500 to
`) -> dict[str, GapQuote] | None:`.

Change the docstring's closing paragraph (lines 510-512) to:

```
    Returns ``{yfinance_ticker: GapQuote}`` (e.g. ``{"0700.HK": GapQuote(...)}``)
    so the keys feed directly into the existing HK yfinance metrics pipeline.
    ``price`` is ``last_price`` — HK has no usable pre-market basis. Returns
    ``None`` on any failure.
```

Replace `survivors: list[str] = []` (line 553) with:

```python
        survivors: dict[str, GapQuote] = {}
```

Replace `survivors.append(f"{n:04d}.HK")` (line 587) with:

```python
                survivors[f"{n:04d}.HK"] = GapQuote(price=price, gap=gap)
```

Note `return []` at line 551 becomes `return {}`.

- [ ] **Step 5: Keep both call sites working**

In `main.py`, at line 762, replace:

```python
    tickers = discovery
```

with:

```python
    quotes = discovery
    tickers = list(quotes)
```

At `main.py:952`, replace:

```python
    tickers = discovery
```

with:

```python
    quotes = discovery
    tickers = list(quotes)
```

(`quotes` is unused until Task 3 — that is expected and the pipeline behavior is unchanged by this task.)

- [ ] **Step 6: Run the tests**

Run: `uv run python -m pytest tests/test_morning_gap_sma.py -v`
Expected: all PASS (17 tests).

Run: `uv run python -m pytest tests/ -q`
Expected: 385 passed, 0 failed.

- [ ] **Step 7: Commit**

```bash
git add futu_sync.py main.py tests/test_morning_gap_sma.py
git commit -m "refactor(morning_gap): discovery returns {ticker: GapQuote}

Both US and HK discovery now carry the live comparison basis and the gap
alongside each survivor, so the trend gate can use them. Keys and their
order are unchanged, None still means failure, {} still means no
candidates — callers keep their existing branches."
```

---

### Task 3: Wire the config knobs into both pipelines

**Files:**

- Modify: `config.toml` (`[morning_gap]` around line 158, `[hk_morning_gap]` around line 263)
- Modify: `main.py:808` (US Phase 3c) and `main.py:994` (HK Phase 3c)
- Modify: `CLAUDE.md` (RS gating / morning-gap notes)
- Test: `tests/test_morning_gap_sma.py` (append)

**Interfaces:**

- Consumes: `GapQuote` values from Task 2 (`quotes` local in both run functions), and the `live_prices` / `gaps` / `bypass_gap_pct` keywords from Task 1.
- Produces: nothing downstream — this is the last task.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_morning_gap_sma.py`:

```python
def test_config_exposes_live_price_and_bypass_knobs():
    """Both morning-gap sections must ship the new knobs with the agreed
    defaults, so a revert is a config edit rather than a code change."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    for section in ("morning_gap", "hk_morning_gap"):
        s = cfg[section]
        assert s["sma_use_live_price"] is True, section
        assert s["sma_bypass_gap_percent"] == 10.0, section
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_morning_gap_sma.py::test_config_exposes_live_price_and_bypass_knobs -v`
Expected: FAIL with `KeyError: 'sma_use_live_price'`.

- [ ] **Step 3: Add the config knobs**

In `config.toml`, insert into `[morning_gap]` immediately before the
`# ADR% 默认继承 [settings]` comment line:

```toml
# 趋势闸 (SMA50/SMA200) 的比较基准: true = 盘前/实时价, false = 昨收 (旧行为)。
# 均线本身始终用已完成日线计算,不受此开关影响。
sma_use_live_price = true
# gap ≥ 此值时豁免 SMA50 (SMA200 永不豁免); 0 = 关闭豁免。
sma_bypass_gap_percent = 10.0
```

In `config.toml`, insert into `[hk_morning_gap]` immediately before the
`# min_adr_percent / adr_days 默认继承 [hk_settings]` comment line:

```toml
# 同美股: 趋势闸比较基准用实时价 (港股仅盘后, 即 last_price), gap ≥ 10% 豁免 SMA50。
sma_use_live_price = true
sma_bypass_gap_percent = 10.0
```

- [ ] **Step 4: Wire the US pipeline**

In `main.py`, replace the Phase 3c block at lines 802-806:

```python
    # Phase 3c: SMA50/SMA200 trend gate (replaces Finviz ta_sma50_pa / ta_sma200_pa).
    tickers = _filter_sma_trend(tickers, daily_data, today_et)
    logger.info(f"  {len(tickers)} after SMA50/SMA200 trend filter")
    if not tickers:
        return offset, []
```

with:

```python
    # Phase 3c: SMA50/SMA200 trend gate (replaces Finviz ta_sma50_pa / ta_sma200_pa).
    # Compared against the pre-market / live price rather than yesterday's close —
    # a reversal's first day is by definition the day it has not yet reclaimed its
    # averages, and that gap bar is exactly what `_trim_today` removes. A gap above
    # `sma_bypass_gap_percent` waives SMA50 only; SMA200 is never waived.
    use_live = config.get("sma_use_live_price", True)
    bypass_pct = config.get("sma_bypass_gap_percent", 0)
    tickers = _filter_sma_trend(
        tickers, daily_data, today_et,
        live_prices={t: q.price for t, q in quotes.items()} if use_live else None,
        gaps={t: q.gap for t, q in quotes.items()},
        bypass_gap_pct=bypass_pct,
    )
    logger.info(
        f"  {len(tickers)} after SMA50/SMA200 trend filter "
        f"(basis={'live' if use_live else 'prev close'}, "
        f"SMA50 bypass at gap>={bypass_pct}%)"
    )
    if not tickers:
        return offset, []
```

- [ ] **Step 5: Wire the HK pipeline**

In `main.py`, replace the Phase 3c block at lines 993-997:

```python
    # Phase 3c: SMA50/SMA200 trend gate
    tickers = _filter_sma_trend(tickers, daily_data, today_hk)
    logger.info(f"  {len(tickers)} after SMA50/SMA200 trend filter")
    if not tickers:
        return offset, []
```

with:

```python
    # Phase 3c: SMA50/SMA200 trend gate — same live-price basis and SMA50 gap
    # bypass as US. HK is post-open only, so the basis is always `last_price`.
    use_live = config.get("sma_use_live_price", True)
    bypass_pct = config.get("sma_bypass_gap_percent", 0)
    tickers = _filter_sma_trend(
        tickers, daily_data, today_hk,
        live_prices={t: q.price for t, q in quotes.items()} if use_live else None,
        gaps={t: q.gap for t, q in quotes.items()},
        bypass_gap_pct=bypass_pct,
    )
    logger.info(
        f"  {len(tickers)} after SMA50/SMA200 trend filter "
        f"(basis={'live' if use_live else 'prev close'}, "
        f"SMA50 bypass at gap>={bypass_pct}%)"
    )
    if not tickers:
        return offset, []
```

- [ ] **Step 6: Document the invariant in `CLAUDE.md`**

Add to the "Invariants (don't break these)" list, after the **HK data-day rule** bullet:

```markdown
- **Morning-gap trend gate uses the live price:** `_filter_sma_trend` compares
  the pre-market print (US negative offsets) or `last_price` (US positive
  offsets, all HK) against SMA50/SMA200 — **not** the previous close. The
  averages themselves still come from `_trim_today`-trimmed completed bars;
  only the comparison basis moved. `[*_morning_gap].sma_bypass_gap_percent`
  waives **SMA50 only** for big gappers — SMA200 is never waived. Both knobs
  are per-market config; `sma_use_live_price = false` restores the old basis.
  Consequence, and it is intended: the gate drifts intraday, so a ticker
  clearing it on any single scan gets pushed (cross-day master records
  anything that ever surfaced). Spec:
  `docs/superpowers/specs/2026-08-13-morning-gap-live-price-trend-gate-design.md`.
```

- [ ] **Step 7: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: 386 passed, 0 failed.

- [ ] **Step 8: Verify against real data with a dry run**

Morning-gap self-validates its ET/HKT window and clean-exits outside it, so
outside the scan window this only confirms the code paths import and the
window check still works:

Run: `uv run main.py --mode morning-gap`
Expected: either `[Morning Gap] Not in scan window, exiting` (outside the
window — fine), or a funnel log whose Phase 3c line now reads
`... after SMA50/SMA200 trend filter (basis=live, SMA50 bypass at gap>=10.0%)`.

- [ ] **Step 9: Commit**

```bash
git add config.toml main.py CLAUDE.md tests/test_morning_gap_sma.py
git commit -m "feat(morning_gap): trend gate compares live price, gap>=10% waives SMA50

Wires sma_use_live_price / sma_bypass_gap_percent into both the US and HK
morning-gap pipelines. Fixes the 2026-08-12 miss where NBIS (+23.3%) and
CRWV (+17.9%) were dropped against a stale 8/11 close — the gate discarded
67 of 85 survivors that day, systematically the reversal setups.

EOD is untouched; _filter_sma_trend has no other callers."
```

---

## Verification

After Task 3, confirm all of the following before calling the work done:

- [ ] `uv run python -m pytest tests/ -q` → 386 passed
- [ ] `git log --oneline -3` shows the three task commits
- [ ] `grep -n "sma_use_live_price\|sma_bypass_gap_percent" config.toml` → 4 hits (2 per section)
- [ ] `grep -rn "_filter_sma_trend" --include="*.py" .` → still only `main.py:808`-ish, `main.py:994`-ish, and the definition, plus the test file. No EOD caller appeared.
- [ ] On the next live scan, the Phase 3c log line shows `basis=live` and the survivor count is materially higher than the ~18/85 seen on 2026-08-12.
