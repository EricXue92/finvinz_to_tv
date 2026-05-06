# HK Longs / Leaders / RS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Futu-only EOD scanner for Hong Kong Main Board equities that mirrors the US Longs (3 splits) + Leaders + conditional RS strategy set, alongside the existing HK Shorts.

**Architecture:** New `hk_eod.py` module owns the full HK pipeline (extracts existing HK Shorts code from `main.py`, adds new long-side strategies). New `hk_rs.py` computes a local IBD-style RS percentile against HSI. `main.py` is reduced to orchestration and config loading. All HK price/volume/cap data sourced from Futu OpenAPI — no yfinance for HK.

**Tech Stack:** Python 3.12, `futu-api`, `pandas`, `openpyxl` (for HKEX xlsx universe), `pytest` (added as dev dependency for the new pure-logic unit tests).

**Spec:** [`docs/superpowers/specs/2026-05-06-hk-longs-leaders-rs-design.md`](../specs/2026-05-06-hk-longs-leaders-rs-design.md)

---

## File Map

**Create:**
- `hk_eod.py` — orchestrator + Futu fetchers + metrics builder + strategy filters + dedup
- `hk_rs.py` — local RS percentile calculation
- `tests/__init__.py`
- `tests/test_hk_eod.py` — unit tests for `build_metrics_frame`, dedup helpers
- `tests/test_hk_rs.py` — unit tests for `compute_rs_table`

**Modify:**
- `main.py` — remove `fetch_hkex_equities`, `filter_hk_shorts`, and the inline HK Shorts call site; add `from hk_eod import run_hk_eod` and a single call after the IPO block
- `config.toml` — add `[hk_settings]`, `[[hk_longs]]` × 3, `[[hk_leaders]]` × 5, `[hk_rs]`, futu group mappings
- `pyproject.toml` — add `pytest` dev dependency
- `CLAUDE.md` — document the new HK long-side groups
- `README.md` — document the new HK groups

---

### Task 1: Bootstrap test infrastructure

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Add pytest as dev dependency**

Edit `pyproject.toml` to add a `[dependency-groups]` block:

```toml
[project]
name = "finviz-to-tv"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "finviz>=2.0.0",
    "futu-api>=9.3.5308",
    "openpyxl>=3.1.5",
    "yfinance>=0.2.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
]
```

- [ ] **Step 2: Sync dev dependencies**

Run: `uv sync --group dev`
Expected: pytest installed without error.

- [ ] **Step 3: Create empty package marker**

Write `tests/__init__.py` with empty content.

- [ ] **Step 4: Write a smoke test**

Write `tests/test_smoke.py`:

```python
def test_pytest_runs():
    assert 1 + 1 == 2
```

- [ ] **Step 5: Run it**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock tests/__init__.py tests/test_smoke.py
git commit -m "chore(tests): bootstrap pytest dev dependency + smoke test"
```

---

### Task 2: Create `hk_eod.py` skeleton + extract `fetch_hkex_equities`

**Files:**
- Create: `hk_eod.py`
- Modify: `main.py:171-186` (remove `fetch_hkex_equities`)

- [ ] **Step 1: Create `hk_eod.py` with the extracted function**

Write `hk_eod.py`:

```python
"""Hong Kong Main Board EOD pipeline. Owns the full HK scan: universe fetch,
Futu data, metrics, strategy filters, dedup, write."""

from __future__ import annotations

import logging
from io import BytesIO
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

HKEX_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/"
    "ListOfSecurities.xlsx"
)


def fetch_hkex_equities() -> list[str]:
    """Download the HKEX securities xlsx, parse with openpyxl, return Main
    Board equity stock codes as 5-digit strings (e.g., '00700')."""
    from openpyxl import load_workbook

    req = Request(HKEX_SECURITIES_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        data = resp.read()
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)

    # Skip 2 leading metadata rows; row 3 has the header.
    next(rows, None)
    next(rows, None)
    header = next(rows, None)
    if header is None:
        return []
    code_idx = next(
        (i for i, h in enumerate(header) if h and "stock code" in str(h).lower()),
        0,
    )
    sub_idx = next(
        (i for i, h in enumerate(header) if h and "sub-category" in str(h).lower()),
        None,
    )

    codes: list[str] = []
    for row in rows:
        if not row or row[code_idx] is None:
            continue
        if sub_idx is not None:
            sub = row[sub_idx]
            if sub != "Equity Securities (Main Board)":
                continue
        raw = str(row[code_idx]).strip()
        if not raw.isdigit():
            continue
        codes.append(raw.zfill(5))
    return codes
```

- [ ] **Step 2: Remove `fetch_hkex_equities` and `HKEX_SECURITIES_URL` from `main.py`**

Delete lines 166-186 (the `HKEX_SECURITIES_URL` constant and `fetch_hkex_equities` function) from `main.py`.

- [ ] **Step 3: Update the import inside `filter_hk_shorts` in main.py**

`filter_hk_shorts` calls `fetch_hkex_equities()` directly at line 285. Change that call to import from `hk_eod`:

```python
from hk_eod import fetch_hkex_equities  # at top of filter_hk_shorts, or in main.py imports
```

For Step 3, add `from hk_eod import fetch_hkex_equities` near the top of `main.py` (with the other module imports). The existing call site at line 285 stays the same.

- [ ] **Step 4: Verify nothing else uses `HKEX_SECURITIES_URL`**

Run: `grep -n "HKEX_SECURITIES_URL\|fetch_hkex_equities" /Users/xue/finviz_to_tv/main.py`
Expected: only the import statement and the call inside `filter_hk_shorts`.

- [ ] **Step 5: Smoke run — call the function once**

Run:
```bash
uv run python -c "from hk_eod import fetch_hkex_equities; codes = fetch_hkex_equities(); print(len(codes), codes[:5])"
```
Expected: ~2,400 codes printed, e.g., `2413 ['00001', '00002', '00003', '00004', '00005']`.

- [ ] **Step 6: Commit**

```bash
git add hk_eod.py main.py
git commit -m "refactor(hk): extract fetch_hkex_equities into hk_eod.py"
```

---

### Task 3: Move `filter_hk_shorts` from `main.py` to `hk_eod.py`

**Files:**
- Modify: `hk_eod.py` (add `filter_hk_shorts` + its yfinance helpers)
- Modify: `main.py` (delete `filter_hk_shorts` + its helpers, import from `hk_eod`)

- [ ] **Step 1: Identify the helpers `filter_hk_shorts` depends on**

Run: `grep -n "_yf_download_with_retry\|_get_market_cap\|_get_closes_volumes\|_get_ohlc\|_trim_today\|_retry_sparse_in_batch" /Users/xue/finviz_to_tv/main.py | head -20`

Expected: each helper is defined once in `main.py` and called from `filter_hk_shorts` plus the US Longs/Leaders/Shorts pipelines. We will NOT move these out of `main.py` because they're shared with the US pipeline. Instead, `hk_eod.filter_hk_shorts` will import them from `main`.

- [ ] **Step 2: Append `filter_hk_shorts` to `hk_eod.py`**

Open `main.py`, copy the entire body of `filter_hk_shorts` (lines 278-491). Paste it at the bottom of `hk_eod.py`. Add the necessary imports at the top of `hk_eod.py`:

```python
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from futu_sync import get_market_caps_futu
```

And inside `filter_hk_shorts`, add this lazy import to break the circular dependency with `main.py`:

```python
def filter_hk_shorts(
    config: dict, futu_cfg: dict | None = None
) -> tuple[int, list[str]]:
    from main import (
        _yf_download_with_retry,
        _get_market_cap,
        _get_closes_volumes,
        _get_ohlc,
        _trim_today,
    )
    # ... rest of the original body unchanged
```

- [ ] **Step 3: Delete the original `filter_hk_shorts` from `main.py`**

Delete lines 278-491 of `main.py` (the entire `filter_hk_shorts` function body).

- [ ] **Step 4: Update the import in `main.py`**

Update the existing `from hk_eod import fetch_hkex_equities` line in `main.py` to:

```python
from hk_eod import fetch_hkex_equities, filter_hk_shorts
```

- [ ] **Step 5: Verify call site at line 1697 still works**

Run: `grep -n "filter_hk_shorts" /Users/xue/finviz_to_tv/main.py`
Expected: one import line + one call site (`filter_hk_shorts(hk_shorts_cfg, futu_cfg=...)`).

- [ ] **Step 6: Smoke-run the existing HK Shorts pipeline**

Run: `uv run main.py 2>&1 | grep -A2 "HK Shorts"`
Expected: pipeline reports same `[HK Shorts] Final: N tickers` line as the most recent prior run. (If there is a previous-day file in `output/TV/HK/` to diff against, also do so.)

- [ ] **Step 7: Commit**

```bash
git add hk_eod.py main.py
git commit -m "refactor(hk): move filter_hk_shorts into hk_eod.py (no-op)"
```

---

### Task 4: Add Futu k-line batch fetcher

**Files:**
- Modify: `hk_eod.py` (add `fetch_hk_klines`)

- [ ] **Step 1: Append the function to `hk_eod.py`**

```python
import pandas as pd
from datetime import date, timedelta


def fetch_hk_klines(
    codes: list[str],
    days: int = 260,
    host: str = "127.0.0.1",
    port: int = 11111,
) -> dict[str, pd.DataFrame] | None:
    """Pull daily OHLCV k-line for a list of HK Futu codes (e.g., 'HK.00700').
    Returns ``{code: DataFrame[time_key, open, close, high, low, volume]}``.
    Returns ``None`` if OpenD is unreachable or the futu SDK is unavailable.
    Tickers that error out individually are skipped silently.
    """
    if not codes:
        return {}
    try:
        from futu import OpenQuoteContext, RET_OK, KLType
    except ImportError:
        logger.warning("[HK] fetch_hk_klines: futu-api not installed")
        return None

    from futu_sync import _opend_reachable
    if not _opend_reachable(host, port):
        logger.warning(
            f"[HK] fetch_hk_klines: OpenD not reachable at {host}:{port}"
        )
        return None

    end = date.today()
    # 260 trading days ≈ 380 calendar days, with margin
    start = end - timedelta(days=int(days * 1.5) + 30)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    result: dict[str, pd.DataFrame] = {}
    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        for i, code in enumerate(codes):
            if i and i % 50 == 0:
                logger.info(f"[HK] k-line: {i}/{len(codes)}")
            try:
                ret, df, _ = ctx.request_history_kline(
                    code, start=start_s, end=end_s,
                    ktype=KLType.K_DAY, max_count=1000,
                )
                if ret != RET_OK or df is None or df.empty:
                    continue
                # Keep only the columns we need; sort ascending by date.
                cols = ["time_key", "open", "high", "low", "close", "volume"]
                df = df[cols].copy()
                df["time_key"] = pd.to_datetime(df["time_key"])
                df = df.sort_values("time_key").reset_index(drop=True)
                result[code] = df
            except Exception:
                continue
        return result
    except Exception as e:
        logger.warning(f"[HK] fetch_hk_klines: unexpected error — {e}")
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
```

- [ ] **Step 2: Smoke-run on a tiny universe**

Run:
```bash
uv run python -c "
from hk_eod import fetch_hk_klines
out = fetch_hk_klines(['HK.00700', 'HK.00005'])
for c, df in out.items():
    print(c, len(df), df['time_key'].iloc[0].date(), '->', df['time_key'].iloc[-1].date())
"
```
Expected: two entries, each with ~250 rows of daily k-line spanning roughly the past year.

- [ ] **Step 3: Commit**

```bash
git add hk_eod.py
git commit -m "feat(hk): add Futu daily k-line batch fetcher"
```

---

### Task 5: Add HSI snapshot helper for conditional RS trigger

**Files:**
- Modify: `hk_eod.py` (add `hsi_day_change_pct`)

- [ ] **Step 1: Append the helper to `hk_eod.py`**

```python
def hsi_day_change_pct(
    host: str = "127.0.0.1", port: int = 11111
) -> float | None:
    """Return today's HSI day change in percent, derived from
    ``(last_price - prev_close_price) / prev_close_price * 100``. Uses
    Futu code ``HK.800000`` for HSI. Returns ``None`` on any failure
    (caller should treat None as 'trigger condition not met')."""
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError:
        return None

    from futu_sync import _opend_reachable
    if not _opend_reachable(host, port):
        return None

    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        ret, data = ctx.get_market_snapshot(["HK.800000"])
        if ret != RET_OK or data is None or data.empty:
            return None
        row = data.iloc[0]
        last = float(row.get("last_price") or 0)
        prev = float(row.get("prev_close_price") or 0)
        if prev <= 0:
            return None
        return (last - prev) / prev * 100.0
    except Exception:
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
```

- [ ] **Step 2: Smoke-run**

Run:
```bash
uv run python -c "from hk_eod import hsi_day_change_pct; print(hsi_day_change_pct())"
```
Expected: a small float (e.g. `-0.42`) during/after HK trading hours, possibly `None` outside hours if Futu zeroes the snapshot fields.

- [ ] **Step 3: Commit**

```bash
git add hk_eod.py
git commit -m "feat(hk): add HSI day-change helper for conditional RS trigger"
```

---

### Task 6: Build the metrics frame

**Files:**
- Modify: `hk_eod.py` (add `build_metrics_frame`)
- Create: `tests/test_hk_eod.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_hk_eod.py`:

```python
import pandas as pd
import pytest

from hk_eod import build_metrics_frame


def _make_kline(closes, volumes=None, highs=None, lows=None):
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c * 1.02 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    dates = pd.date_range(end="2026-05-05", periods=n, freq="B")
    return pd.DataFrame({
        "time_key": dates,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def test_build_metrics_frame_basic():
    # 252 days of flat $50 close, then today's close at $52.5 (gap +5%, RVol 2x)
    closes = [50.0] * 251 + [52.5]
    volumes = [1_000_000] * 251 + [2_000_000]
    klines = {"HK.00001": _make_kline(closes, volumes=volumes)}
    caps = {"HK.00001": 5_000_000_000.0}
    df = build_metrics_frame(klines, caps)

    row = df.loc["HK.00001"]
    assert row["market_cap"] == 5_000_000_000.0
    assert row["last_price"] == pytest.approx(52.5)
    assert row["prev_close"] == pytest.approx(50.0)
    assert row["gap_pct"] == pytest.approx(5.0)
    assert row["rvol"] == pytest.approx(2.0)
    assert row["avg_vol_20d"] == pytest.approx(1_050_000)  # 19×1M + 1×2M / 20
    assert row["avg_dollar_vol_20d"] == pytest.approx(52.5 * 1_050_000)
    assert row["sma50"] == pytest.approx((49 * 50.0 + 52.5) / 50)
    assert row["sma200"] == pytest.approx((199 * 50.0 + 52.5) / 200)
    assert row["above_sma50"] is True or row["above_sma50"] == True
    assert row["above_sma200"] is True or row["above_sma200"] == True
    assert row["perf_4w"] == pytest.approx(5.0)  # 20 trading days back was 50.0


def test_build_metrics_frame_skips_short_history():
    # Only 30 days of data — not enough for SMA200, but SMA50 should still work
    closes = [50.0] * 29 + [52.0]
    klines = {"HK.00002": _make_kline(closes)}
    caps = {"HK.00002": 1_000_000_000.0}
    df = build_metrics_frame(klines, caps)
    row = df.loc["HK.00002"]
    assert pd.isna(row["sma200"])
    assert row["above_sma200"] is False
    assert pd.notna(row["sma50"]) is False  # < 50 days
```

- [ ] **Step 2: Run the test (should fail — function not yet defined)**

Run: `uv run pytest tests/test_hk_eod.py -v`
Expected: ImportError or AttributeError: `cannot import name 'build_metrics_frame' from 'hk_eod'`.

- [ ] **Step 3: Implement `build_metrics_frame` in `hk_eod.py`**

```python
def build_metrics_frame(
    klines: dict[str, pd.DataFrame],
    market_caps: dict[str, float],
) -> pd.DataFrame:
    """Reduce a {code: kline_df} dict + caps to a metrics DataFrame indexed by
    code. Tickers without enough history for a given metric get NaN/False.

    Columns:
      market_cap, last_price, prev_close, gap_pct, rvol, avg_vol_20d,
      avg_dollar_vol_20d, adr_pct, sma50, sma200, above_sma50, above_sma200,
      perf_4w, perf_13w, perf_26w, perf_ytd, perf_52w, consecutive_up_days
    """
    rows: list[dict] = []
    today_year = pd.Timestamp.today().year
    for code, df in klines.items():
        if df is None or df.empty or len(df) < 2:
            continue
        closes = df["close"].astype(float).values
        volumes = df["volume"].astype(float).values
        highs = df["high"].astype(float).values
        lows = df["low"].astype(float).values
        times = df["time_key"]
        n = len(closes)

        last = float(closes[-1])
        prev = float(closes[-2])
        gap = (last - prev) / prev * 100.0 if prev > 0 else float("nan")
        avg_vol_20 = float(volumes[-20:].mean()) if n >= 20 else float("nan")
        rvol = (
            float(volumes[-1] / volumes[-21:-1].mean())
            if n >= 21 and volumes[-21:-1].mean() > 0
            else float("nan")
        )
        avg_dv_20 = last * avg_vol_20 if n >= 20 else float("nan")

        if n >= 20:
            adr = float(((highs[-20:] - lows[-20:]) / closes[-20:]).mean()) * 100
        else:
            adr = float("nan")

        sma50 = float(closes[-50:].mean()) if n >= 50 else float("nan")
        sma200 = float(closes[-200:].mean()) if n >= 200 else float("nan")
        above_sma50 = bool(n >= 50 and last > sma50)
        above_sma200 = bool(n >= 200 and last > sma200)

        def _perf(days: int) -> float:
            if n <= days:
                return float("nan")
            past = closes[-days - 1]
            return (last - past) / past * 100.0 if past > 0 else float("nan")

        perf_4w = _perf(20)
        perf_13w = _perf(65)
        perf_26w = _perf(130)
        perf_52w = _perf(252) if n > 252 else float("nan")

        # YTD: find earliest close in the current year
        ytd_mask = times.dt.year == today_year
        if ytd_mask.any():
            first_ytd = float(closes[ytd_mask.values][0])
            perf_ytd = (last - first_ytd) / first_ytd * 100.0 if first_ytd > 0 else float("nan")
        else:
            perf_ytd = float("nan")

        # Consecutive up days from the tail
        cu = 0
        for i in range(n - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                cu += 1
            else:
                break

        rows.append({
            "code": code,
            "market_cap": market_caps.get(code, float("nan")),
            "last_price": last,
            "prev_close": prev,
            "gap_pct": gap,
            "rvol": rvol,
            "avg_vol_20d": avg_vol_20,
            "avg_dollar_vol_20d": avg_dv_20,
            "adr_pct": adr,
            "sma50": sma50,
            "sma200": sma200,
            "above_sma50": above_sma50,
            "above_sma200": above_sma200,
            "perf_4w": perf_4w,
            "perf_13w": perf_13w,
            "perf_26w": perf_26w,
            "perf_ytd": perf_ytd,
            "perf_52w": perf_52w,
            "consecutive_up_days": cu,
        })
    return pd.DataFrame(rows).set_index("code") if rows else pd.DataFrame()
```

- [ ] **Step 4: Re-run the test**

Run: `uv run pytest tests/test_hk_eod.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add hk_eod.py tests/test_hk_eod.py
git commit -m "feat(hk): metrics-frame builder + unit tests"
```

---

### Task 7: Implement `hk_rs.py` — local RS percentile vs HSI

**Files:**
- Create: `hk_rs.py`
- Create: `tests/test_hk_rs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hk_rs.py`:

```python
import pandas as pd
import numpy as np
import pytest

from hk_rs import compute_rs_table, filter_by_rs


def _flat_then_jump(start_price: float, jump_pct: float, n: int = 260):
    closes = [start_price] * (n - 1) + [start_price * (1 + jump_pct / 100)]
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-05", periods=n, freq="B"),
        "close": closes,
    })


def test_compute_rs_table_relative_to_hsi():
    # Universe of 5 tickers with monotonically increasing 12mo returns.
    klines = {
        f"HK.000{i:02d}": _flat_then_jump(100.0, jump_pct=10 + i * 5)
        for i in range(1, 6)
    }
    # HSI flat → relative score = absolute score, percentiles 0..99 should
    # rank ascending by jump_pct.
    hsi = _flat_then_jump(20000.0, jump_pct=0)
    table = compute_rs_table(klines, hsi)

    # Index should match input codes; rs_percentile in [0, 99]
    assert set(table.index) == set(klines.keys())
    assert table["rs_percentile"].between(0, 99).all()
    # Highest jump should get the highest percentile
    top = table["rs_percentile"].idxmax()
    assert top == "HK.00005"


def test_filter_by_rs_passthrough_for_missing():
    table = pd.DataFrame({"rs_percentile": [95, 50]}, index=["HK.AAA", "HK.BBB"])
    out = filter_by_rs(["HK.AAA", "HK.BBB", "HK.CCC"], table, threshold=90)
    # AAA passes; BBB fails; CCC missing → kept (matches rs_rating.py policy)
    assert set(out) == {"HK.AAA", "HK.CCC"}


def test_filter_by_rs_none_table_passthrough():
    out = filter_by_rs(["HK.AAA", "HK.BBB"], None, threshold=90)
    assert out == ["HK.AAA", "HK.BBB"]
```

- [ ] **Step 2: Run tests (expected to fail — module not yet present)**

Run: `uv run pytest tests/test_hk_rs.py -v`
Expected: ModuleNotFoundError: No module named 'hk_rs'.

- [ ] **Step 3: Implement `hk_rs.py`**

```python
"""Local IBD-style Relative Strength percentile for the HK universe.

Mirrors rs_rating.py's contract but computes percentiles in-process from
Futu k-line data rather than reading the Fred6725 CSV. HSI is the benchmark.
"""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)


def _score_from_kline(df: pd.DataFrame) -> float | None:
    """Compute 0.4*R3 + 0.2*R6 + 0.2*R9 + 0.2*R12 from a k-line DataFrame
    sorted ascending by ``time_key``. Returns None if there are fewer than
    253 rows (need ~21 trading days × 12 months + today)."""
    if df is None or len(df) < 253:
        return None
    closes = df["close"].astype(float).values
    last = closes[-1]
    if last <= 0:
        return None

    weights = [(3, 0.4), (6, 0.2), (9, 0.2), (12, 0.2)]
    score = 0.0
    for months, w in weights:
        idx = -1 - months * 21
        if abs(idx) > len(closes):
            return None
        past = closes[idx]
        if past <= 0:
            return None
        score += w * ((last / past) - 1.0)
    return score


def compute_rs_table(
    klines: dict[str, pd.DataFrame],
    hsi_kline: pd.DataFrame,
) -> pd.DataFrame:
    """Return DataFrame indexed by Futu code with column ``rs_percentile``
    (0-99). Tickers without enough history are excluded."""
    hsi_score = _score_from_kline(hsi_kline)
    if hsi_score is None:
        # If HSI itself doesn't have history, fall back to absolute scores.
        hsi_score = 0.0

    scores: dict[str, float] = {}
    for code, df in klines.items():
        s = _score_from_kline(df)
        if s is None:
            continue
        scores[code] = s - hsi_score

    if not scores:
        return pd.DataFrame(columns=["rs_percentile"])

    series = pd.Series(scores, name="relative_score")
    pct = series.rank(method="average", pct=True) * 99
    return pd.DataFrame({"rs_percentile": pct.round().astype(int)})


def filter_by_rs(
    tickers: list[str],
    table: pd.DataFrame | None,
    threshold: int,
) -> list[str]:
    """Keep tickers with rs_percentile >= threshold. Tickers missing from
    ``table`` are KEPT (passthrough — same policy as rs_rating.py for IPOs
    and recent listings without 12mo history). If ``table`` is None or
    empty, all tickers are returned unchanged (failure passthrough)."""
    if table is None or table.empty or threshold <= 0:
        return list(tickers)
    out = []
    for t in tickers:
        if t not in table.index:
            out.append(t)
            continue
        if int(table.loc[t, "rs_percentile"]) >= threshold:
            out.append(t)
    return out


def cache_path(today: date, output_dir: Path) -> Path:
    return output_dir / "state" / f"hk_rs_rating_{today.isoformat()}.csv"


def save_cache(table: pd.DataFrame, today: date, output_dir: Path) -> None:
    p = cache_path(today, output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(p, index_label="code")


def load_cache(today: date, output_dir: Path) -> pd.DataFrame | None:
    p = cache_path(today, output_dir)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, index_col="code")
    except Exception:
        return None
```

- [ ] **Step 4: Re-run tests**

Run: `uv run pytest tests/test_hk_rs.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add hk_rs.py tests/test_hk_rs.py
git commit -m "feat(hk): local IBD-style RS percentile vs HSI + tests"
```

---

### Task 8: Implement strategy filters

**Files:**
- Modify: `hk_eod.py` (add `apply_strategy_filters`)

- [ ] **Step 1: Append `apply_strategy_filters` to `hk_eod.py`**

```python
HK_STRATEGY_PRIORITY = ["EarningsGap", "HighVolume", "GapUp", "Leaders", "RS"]


def apply_strategy_filters(
    metrics: pd.DataFrame,
    settings: dict,
    longs_cfg: list[dict],
    leaders_cfg: list[dict],
    rs_enabled: bool,
) -> dict[str, list[str]]:
    """Apply every strategy gate against the metrics frame and return a dict
    of {strategy_name: [code, ...]}. Codes are still in Futu format
    (``HK.00700``); caller converts to TradingView ``HKEX:00700`` later.

    Strategies returned: EarningsGap, HighVolume, GapUp, Leaders, RS
    (the last is always returned but empty when rs_enabled is False).
    """
    if metrics.empty:
        return {s: [] for s in HK_STRATEGY_PRIORITY}

    cap = settings.get("min_market_cap", 300_000_000)
    dvol = settings.get("min_dollar_volume", 100_000_000)
    avg_vol = settings.get("min_avg_volume", 500_000)
    adr = settings.get("min_adr_percent", 4.0)
    price = settings.get("min_price", 20.0)

    # Universal long-side baseline
    base = (
        (metrics["market_cap"] >= cap)
        & (metrics["avg_vol_20d"] >= avg_vol)
        & (metrics["avg_dollar_vol_20d"] >= dvol)
        & (metrics["adr_pct"] >= adr)
        & (metrics["last_price"] >= price)
    )

    # Per-strategy parameter lookup
    by_key = {item.get("key"): item for item in longs_cfg}
    eg = by_key.get("earnings_gap", {})
    hv = by_key.get("high_volume", {})
    gu = by_key.get("gap_up", {})

    eg_min_rvol = float(eg.get("min_relative_volume", 3))
    eg_min_gap = float(eg.get("min_gap_percent", 3.0))
    hv_min_rvol = float(hv.get("min_relative_volume", 3))
    gu_min_gap = float(gu.get("min_gap_percent", 5.0))

    earnings_gap_mask = base & (metrics["gap_pct"] >= eg_min_gap) & (metrics["rvol"] >= eg_min_rvol)
    high_volume_mask = base & (metrics["rvol"] >= hv_min_rvol)
    gap_up_mask = base & (metrics["gap_pct"] >= gu_min_gap)

    # Leaders: baseline + above SMA50 & SMA200 + any of the perf windows
    perf_any = (
        (metrics["perf_4w"] >= _leader_threshold(leaders_cfg, "min_perf_4w"))
        | (metrics["perf_13w"] >= _leader_threshold(leaders_cfg, "min_perf_13w"))
        | (metrics["perf_26w"] >= _leader_threshold(leaders_cfg, "min_perf_26w"))
        | (metrics["perf_ytd"] >= _leader_threshold(leaders_cfg, "min_perf_ytd"))
        | (metrics["perf_52w"] >= _leader_threshold(leaders_cfg, "min_perf_52w"))
    ).fillna(False)

    leaders_mask = (
        base
        & metrics["above_sma50"]
        & metrics["above_sma200"]
        & perf_any
    )

    # RS group: baseline + above-SMA50/200, no perf window. Always computed,
    # caller decides whether to actually emit it based on HSI trigger.
    rs_mask = base & metrics["above_sma50"] & metrics["above_sma200"]

    return {
        "EarningsGap": metrics.index[earnings_gap_mask].tolist(),
        "HighVolume": metrics.index[high_volume_mask].tolist(),
        "GapUp": metrics.index[gap_up_mask].tolist(),
        "Leaders": metrics.index[leaders_mask].tolist(),
        "RS": metrics.index[rs_mask].tolist() if rs_enabled else [],
    }


def _leader_threshold(leaders_cfg: list[dict], key: str) -> float:
    """Find the threshold for a given perf window across the [[hk_leaders]]
    list. Returns +inf if no matching entry (so the OR short-circuits)."""
    for item in leaders_cfg:
        if key in item:
            return float(item[key])
    return float("inf")
```

- [ ] **Step 2: Add a test for the filter**

Append to `tests/test_hk_eod.py`:

```python
from hk_eod import apply_strategy_filters


def test_apply_strategy_filters_earnings_gap_priority():
    # Construct a metrics frame manually
    df = pd.DataFrame([
        # Passes everything + gap 5% + rvol 3 → EarningsGap
        dict(market_cap=1e9, last_price=25.0, prev_close=23.81, gap_pct=5.0,
             rvol=3.0, avg_vol_20d=1e6, avg_dollar_vol_20d=2e8, adr_pct=5.0,
             sma50=22.0, sma200=20.0, above_sma50=True, above_sma200=True,
             perf_4w=10.0, perf_13w=20.0, perf_26w=30.0, perf_ytd=40.0,
             perf_52w=50.0, consecutive_up_days=2),
        # Cap too small → drops out everywhere
        dict(market_cap=1e8, last_price=25.0, prev_close=23.81, gap_pct=5.0,
             rvol=3.0, avg_vol_20d=1e6, avg_dollar_vol_20d=2e8, adr_pct=5.0,
             sma50=22.0, sma200=20.0, above_sma50=True, above_sma200=True,
             perf_4w=10.0, perf_13w=20.0, perf_26w=30.0, perf_ytd=40.0,
             perf_52w=50.0, consecutive_up_days=2),
    ], index=["HK.00001", "HK.00002"])

    settings = dict(min_market_cap=3e8, min_dollar_volume=1e8,
                    min_avg_volume=5e5, min_adr_percent=4.0, min_price=20.0)
    longs = [
        {"key": "earnings_gap", "min_relative_volume": 3, "min_gap_percent": 3.0},
        {"key": "high_volume", "min_relative_volume": 3},
        {"key": "gap_up", "min_gap_percent": 5.0},
    ]
    leaders = [
        {"min_perf_4w": 30}, {"min_perf_13w": 50}, {"min_perf_26w": 100},
        {"min_perf_ytd": 100}, {"min_perf_52w": 150},
    ]
    out = apply_strategy_filters(df, settings, longs, leaders, rs_enabled=True)

    assert out["EarningsGap"] == ["HK.00001"]
    assert out["HighVolume"] == ["HK.00001"]
    assert out["GapUp"] == ["HK.00001"]
    assert out["Leaders"] == []  # perf_4w 10 < 30, etc.
    assert out["RS"] == ["HK.00001"]
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_hk_eod.py -v`
Expected: 3 passed (the 2 prior + the new filter test).

- [ ] **Step 4: Commit**

```bash
git add hk_eod.py tests/test_hk_eod.py
git commit -m "feat(hk): per-strategy filter masks against metrics frame"
```

---

### Task 9: Within-day cross-strategy priority dedup

**Files:**
- Modify: `hk_eod.py` (add `dedup_by_priority`)

- [ ] **Step 1: Append the helper**

```python
def dedup_by_priority(
    raw: dict[str, list[str]],
    priority: list[str] | None = None,
) -> dict[str, list[str]]:
    """Given {strategy: [code,...]}, walk in priority order and drop codes
    from later strategies that already appeared in an earlier one. Default
    priority: EarningsGap > HighVolume > GapUp > Leaders > RS."""
    order = priority or HK_STRATEGY_PRIORITY
    seen: set[str] = set()
    out: dict[str, list[str]] = {}
    for name in order:
        codes = [c for c in raw.get(name, []) if c not in seen]
        out[name] = codes
        seen.update(codes)
    return out
```

- [ ] **Step 2: Add a test**

Append to `tests/test_hk_eod.py`:

```python
from hk_eod import dedup_by_priority


def test_dedup_by_priority_strips_lower_priority_duplicates():
    raw = {
        "EarningsGap": ["A", "B"],
        "HighVolume":  ["B", "C"],     # B already in EG → dropped
        "GapUp":       ["C", "D"],     # C already in HV → dropped
        "Leaders":     ["D", "E"],     # D already in GU → dropped
        "RS":          ["A", "F"],     # A already in EG → dropped
    }
    out = dedup_by_priority(raw)
    assert out == {
        "EarningsGap": ["A", "B"],
        "HighVolume":  ["C"],
        "GapUp":       ["D"],
        "Leaders":     ["E"],
        "RS":          ["F"],
    }
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_hk_eod.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add hk_eod.py tests/test_hk_eod.py
git commit -m "feat(hk): within-day priority dedup helper"
```

---

### Task 10: Wire `run_hk_eod` orchestrator

**Files:**
- Modify: `hk_eod.py` (add `run_hk_eod`)
- Modify: `main.py` (replace HK Shorts call site with `run_hk_eod`)

- [ ] **Step 1: Append the orchestrator to `hk_eod.py`**

```python
from pathlib import Path


def _to_tv(code: str) -> str:
    """``HK.00700`` → ``HKEX:00700``."""
    return "HKEX:" + code.replace("HK.", "")


def run_hk_eod(
    config: dict,
    output_dir: Path,
    today_iso: str,
    write_watchlist,         # callable from main.py
    write_webull,            # callable from main.py (_write_webull)
    futu_sync,               # callable from main.py (_futu_sync)
    load_seen,               # callable from main.py (_load_seen)
    persist_seen,            # callable from main.py (_persist_seen)
    eod_seen_path,           # callable from main.py (_eod_seen_path)
    dedup_seen,              # callable from main.py (_dedup_seen)
) -> None:
    """Top-level HK EOD pipeline. Runs the existing HK Shorts (unchanged)
    plus the new long-side strategies (EarningsGap, HighVolume, GapUp,
    Leaders, conditional RS). Writes 6 dated .txt files, mirrors to Webull,
    and Futu-syncs each one."""
    hk_settings = config.get("hk_settings") or {}
    hk_longs = config.get("hk_longs") or []
    hk_leaders = config.get("hk_leaders") or []
    futu_cfg = config.get("futu") or {}
    hk_output_dir = output_dir / "TV" / "HK"
    hk_output_dir.mkdir(parents=True, exist_ok=True)

    fmt = config.get("settings", {}).get("output_format", "comma")
    host = futu_cfg.get("host", "127.0.0.1")
    port = int(futu_cfg.get("port", 11111))

    # --- HK Shorts (existing pipeline, unchanged) ---
    hk_shorts_cfg = config.get("hk_shorts")
    if hk_shorts_cfg:
        hk_shorts_cfg.setdefault("min_adr_percent",
                                 config.get("settings", {}).get("min_adr_percent", 4.0))
        hk_shorts_cfg.setdefault("adr_days",
                                 config.get("settings", {}).get("adr_days", 20))
        try:
            total, hk_shorts_tv = filter_hk_shorts(hk_shorts_cfg, futu_cfg=futu_cfg)
            sorted_hk = sorted(hk_shorts_tv)
            dated = hk_output_dir / f"{today_iso}_Shorts.txt"
            write_watchlist(sorted_hk, dated, fmt)
            logger.info(f"[HK Shorts] {len(sorted_hk)} -> {dated}")
            write_webull(sorted_hk, dated, output_dir)
            futu_sync(config, "hk_shorts", sorted_hk, "HK")
        except Exception as e:
            logger.warning(f"[HK Shorts] Failed: {e}")

    # --- HK Long-side: Futu-only ---
    logger.info("[HK Longs] Fetching universe...")
    codes_5d = fetch_hkex_equities()
    codes = [f"HK.{c}" for c in codes_5d]
    logger.info(f"  Universe: {len(codes)} codes")

    logger.info("[HK Longs] Fetching daily k-line via Futu (10-15 min)...")
    klines = fetch_hk_klines(codes, days=260, host=host, port=port)
    if klines is None:
        logger.warning("[HK Longs] OpenD unreachable — writing empty files for the day")
        klines = {}

    logger.info("[HK Longs] Fetching market caps via Futu snapshot...")
    from futu_sync import get_market_caps_futu
    # get_market_caps_futu wants TradingView format (HKEX:00700)
    tv_codes = [_to_tv(c) for c in codes if c in klines]
    futu_caps = get_market_caps_futu(tv_codes, market="HK", host=host, port=port) or {}
    # Re-key to Futu format so the metrics frame index lines up with klines
    caps = {f"HK.{tv.replace('HKEX:', '')}": v for tv, v in futu_caps.items()}

    logger.info("[HK Longs] Building metrics frame...")
    metrics = build_metrics_frame(klines, caps)
    logger.info(f"  Metrics: {len(metrics)} tickers with usable history")

    # --- RS table ---
    from hk_rs import (
        compute_rs_table, filter_by_rs, save_cache, load_cache,
    )
    today_d = pd.Timestamp(today_iso).date()
    rs_table = load_cache(today_d, output_dir)
    if rs_table is None and klines:
        hsi_data = fetch_hk_klines(["HK.800000"], days=260, host=host, port=port) or {}
        hsi_kline = hsi_data.get("HK.800000")
        if hsi_kline is not None and not hsi_kline.empty:
            rs_table = compute_rs_table(klines, hsi_kline)
            save_cache(rs_table, today_d, output_dir)
        else:
            logger.warning("[HK Longs] HSI k-line fetch failed — RS gate disabled")

    # --- Apply per-strategy filters ---
    rs_trigger = hk_settings.get("hsi_rs_trigger", -1.5)
    hsi_change = hsi_day_change_pct(host=host, port=port)
    rs_enabled = hsi_change is not None and hsi_change <= rs_trigger
    logger.info(
        f"[HK Longs] HSI day-change={hsi_change} (trigger {rs_trigger}); "
        f"RS group {'ENABLED' if rs_enabled else 'skipped'}"
    )

    raw = apply_strategy_filters(metrics, hk_settings, hk_longs, hk_leaders, rs_enabled)

    # --- RS percentile gate (after raw masks) ---
    threshold = int(hk_settings.get("min_rs_percentile_longs", 90))
    raw = {
        name: filter_by_rs(codes, rs_table, threshold)
        for name, codes in raw.items()
    }

    # --- Within-day cross-strategy priority dedup ---
    dedup = dedup_by_priority(raw)

    # --- Cross-day master dedup ---
    seen_path = eod_seen_path(output_dir, "HK")
    seen = load_seen(seen_path)
    final: dict[str, list[str]] = {}
    for name, codes in dedup.items():
        tag = f"[HK {name}]"
        # Convert to TV format for the seen file (matches write_watchlist input)
        tv = sorted(_to_tv(c) for c in codes)
        tv = dedup_seen(tag, tv, seen, seen_path)
        final[name] = tv

    # --- Write outputs + Futu sync ---
    futu_key = {
        "EarningsGap": "hk_longs_earnings_gap",
        "HighVolume":  "hk_longs_high_volume",
        "GapUp":       "hk_longs_gap_up",
        "Leaders":     "hk_leaders",
        "RS":          "hk_rs",
    }
    for name, tv in final.items():
        dated = hk_output_dir / f"{today_iso}_{name}.txt"
        write_watchlist(tv, dated, fmt)
        logger.info(f"[HK {name}] {len(tv)} -> {dated}")
        write_webull(tv, dated, output_dir)
        futu_sync(config, futu_key[name], tv, "HK")
```

- [ ] **Step 2: Wire `run_hk_eod` into `main.py`**

In `main.py`, find the existing `# --- HK Shorts ---` block (around line 1690-1709). Replace the entire block with a single call:

```python
# --- HK EOD pipeline (Shorts + Longs/Leaders/RS) ---
from hk_eod import run_hk_eod
try:
    run_hk_eod(
        config=config,
        output_dir=output_dir,
        today_iso=str(today),
        write_watchlist=write_watchlist,
        write_webull=_write_webull,
        futu_sync=_futu_sync,
        load_seen=_load_seen,
        persist_seen=_persist_seen,
        eod_seen_path=_eod_seen_path,
        dedup_seen=_dedup_seen,
    )
except Exception as e:
    logger.warning(f"[HK EOD] Pipeline failed: {e}")
```

- [ ] **Step 3: Verify Python syntax**

Run: `uv run python -c "import hk_eod; import main"`
Expected: no error.

- [ ] **Step 4: Run unit tests**

Run: `uv run pytest tests/ -v`
Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add hk_eod.py main.py
git commit -m "feat(hk): run_hk_eod orchestrator + main.py wiring"
```

---

### Task 11: Add HK config block to `config.toml`

**Files:**
- Modify: `config.toml`

- [ ] **Step 1: Append the HK long-side config**

After the existing `[hk_shorts]` block (or in its commented-out region), insert:

```toml
# ----------------------------- HK Long-side -----------------------------

[hk_settings]
min_market_cap          = 300_000_000     # HKD
min_dollar_volume       = 100_000_000     # HKD
min_avg_volume          = 500_000         # shares/day
min_adr_percent         = 4.0
min_price               = 20.0            # HKD
min_rs_percentile_longs = 90
hsi_rs_trigger          = -1.5            # HSI day-change %

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

[hk_rs]
name = "HK Relative Strength"
```

- [ ] **Step 2: Add Futu group mappings to the existing `[futu.groups]` block**

Find the existing `[futu.groups]` section. Append:

```toml
hk_longs_earnings_gap = "HKEarningsGap"
hk_longs_high_volume  = "HKHighVolume"
hk_longs_gap_up       = "HKGapUp"
hk_leaders            = "HKLeaders"
hk_rs                 = "HKRS"
```

- [ ] **Step 3: Add the new groups to `append_only_groups`**

Find the existing `[futu] append_only_groups = [...]` list and append the 5 new entries (preserving the existing content):

```toml
append_only_groups = [
    "EarningsGap", "HighVolume", "GapUp", "NewHigh52W", "TopGainers",
    "Leaders", "Shorts", "RS", "HKShorts", "IPO",
    "HKEarningsGap", "HKHighVolume", "HKGapUp", "HKLeaders", "HKRS",
]
```

- [ ] **Step 4: Uncomment the existing `[hk_shorts]` block** (if currently commented)

The current `[hk_shorts]` is commented out (`# ` prefix on every line). Uncomment lines 147-160 so the HK Shorts pipeline actually runs. Set `min_market_cap = 300_000_000` (matching the new universal HK threshold) instead of the previous `2_000_000_000`. Keep `min_avg_volume = 1_000_000` (the user explicitly wanted Shorts to keep its 1M floor).

- [ ] **Step 5: Verify TOML parses**

Run: `uv run python -c "import tomllib; tomllib.load(open('config.toml','rb')); print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add config.toml
git commit -m "feat(hk): config blocks for HK long-side + futu group mappings"
```

---

### Task 12: End-to-end smoke run

**Files:** none (operational)

- [ ] **Step 1: Confirm OpenD is running**

Run: `nc -zv 127.0.0.1 11111 2>&1 | head -1`
Expected: `Connection to 127.0.0.1 port 11111 [tcp/*] succeeded!`. If not, start FutuOpenD before continuing.

- [ ] **Step 2: Confirm the 5 new Futu custom groups exist in the Futu PC client**

These must be created manually by the user (Futu API can't create groups). Required group names: `HKEarningsGap`, `HKHighVolume`, `HKGapUp`, `HKLeaders`, `HKRS`.

If any are missing, the per-group Futu sync will log a warning but the .txt artifact will still be written correctly.

- [ ] **Step 3: Run the EOD pipeline**

Run: `uv run main.py 2>&1 | tee /tmp/hk_eod_smoke.log`
Expected:
- `[HK Shorts] ... -> output/TV/HK/<date>_Shorts.txt` (existing pipeline still works)
- `[HK Longs] Universe: ~2400 codes`
- `[HK Longs] HSI day-change=...` line
- One `[HK EarningsGap] N -> ...`, `[HK HighVolume] N -> ...`, `[HK GapUp] N -> ...`, `[HK Leaders] N -> ...`, and (if HSI ≤ −1.5%) `[HK RS] N -> ...` line
- No exceptions logged

- [ ] **Step 4: Inspect the new .txt files**

Run:
```bash
ls -la output/TV/HK/$(date +%Y_%m_%d)_*.txt
head output/TV/HK/$(date +%Y_%m_%d)_Leaders.txt
head output/TV/HK/$(date +%Y_%m_%d)_EarningsGap.txt
```
Expected: 5–6 files (depending on whether RS triggered today). All in TradingView format `HKEX:NNNNN`, comma-separated.

- [ ] **Step 5: Sanity-check one ticker per group**

Pick the first ticker from `Leaders.txt` (e.g., `HKEX:01810`). Verify on TradingView or the Futu PC client that it actually has:
- Market cap ≥ HK$300M
- Above SMA50 and SMA200
- Some perf window (4w/13w/26w/YTD/52w) above its threshold

If a ticker visibly fails one of these gates, capture the discrepancy in a follow-up task and stop.

- [ ] **Step 6: Verify Webull mirror was written**

Run: `head -3 output/Webull/HK/$(date +%Y_%m_%d)_Leaders.txt`
Expected: one ticker per line, no commas (the Webull format).

- [ ] **Step 7: Verify Futu sync (if all 5 groups exist in Futu client)**

In the Futu PC client, refresh the custom watchlist groups. Each of `HKEarningsGap`, `HKHighVolume`, `HKGapUp`, `HKLeaders`, `HKRS` should now contain today's surviving tickers.

- [ ] **Step 8: Verify the cross-day master file was created/updated**

Run:
```bash
ls -la output/state/eod_seen_HK.txt
wc -l output/state/eod_seen_HK.txt
```
Expected: file exists, line count matches the union of today's HK long-side outputs.

- [ ] **Step 9: Commit any incidental fixes from steps 4-8**

If smoke testing surfaced bugs, fix inline and commit. If everything passes, no commit needed.

---

### Task 13: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update `CLAUDE.md` Architecture section**

In the "Architecture" section, update the description of the screener groups. Replace the existing seven-group bullet list with one that adds five HK long-side groups. Specifically:

- Update the opening line: "Python tool: `main.py` (entry point + EOD/morning-gap orchestration), `hk_eod.py` (HK pipeline: Shorts + Longs/Leaders/RS), `hk_rs.py` (local IBD-style RS vs HSI), `rs_rating.py` (US IBD RS table fetcher), ..."
- Add five new HK long-side group bullets (HK EarningsGap, HK HighVolume, HK GapUp, HK Leaders, HK RS conditional) under the existing HK Shorts bullet, with HKD-native thresholds and the pattern-based EarningsGap detection note.
- Update the "Key mechanisms / Cross-group dedup" subsection to mention `output/state/eod_seen_HK.txt` (independent of `eod_seen_US.txt`).
- Update the IBD RS section to note the HK pipeline uses a separate local RS computation against HSI in `hk_rs.py`, cached at `output/state/hk_rs_rating_<date>.csv`.
- Update the Futu config example to include the 5 new HK groups.

- [ ] **Step 2: Update `README.md`**

Add a sub-section under the existing HK section describing the 5 new long-side groups + the conditional RS group. Include the threshold table from the design spec.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(hk): document new HK long-side scanner groups"
```

---

## Self-Review

Spec coverage check:

| Spec section | Covered by task |
|---|---|
| New `hk_eod.py` module | Tasks 2, 3, 4, 5, 6, 8, 9, 10 |
| New `hk_rs.py` module | Task 7 |
| Futu k-line + snapshot data source | Tasks 4, 5, 10 |
| Universal baseline (cap/$vol/avg vol/ADR/price/RS) | Task 8 |
| Per-strategy gates (EG, HV, GU, Leaders, RS) | Task 8 |
| Within-day priority dedup | Task 9 |
| Cross-day `eod_seen_HK.txt` master | Task 10 (uses existing `_dedup_seen`) |
| HSI conditional RS trigger | Tasks 5, 10 |
| OpenD-unreachable fallback (empty .txt) | Task 10 (handled by `klines=None` branch) |
| HK Shorts retains 1M avg-vol floor | Task 11 step 4 |
| Config additions | Task 11 |
| Webull mirrors | Task 10 (via injected `_write_webull`) |
| Futu sync to 5 new append-only groups | Task 11 step 3 + Task 10 |
| Documentation | Task 13 |
| End-to-end verification | Task 12 |

No gaps. Type consistency: `dict[str, list[str]]` for strategy outputs is used consistently across Tasks 8, 9, 10. Function names `compute_rs_table`, `filter_by_rs`, `apply_strategy_filters`, `dedup_by_priority`, `build_metrics_frame`, `fetch_hk_klines`, `hsi_day_change_pct`, `run_hk_eod` are stable across tasks.
