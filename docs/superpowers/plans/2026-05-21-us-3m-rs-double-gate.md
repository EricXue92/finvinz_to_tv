# US 长线 RS 双闸门 + IPO 阶梯 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在美股 EOD 流水线上加第二层 3M RS 闸门(对 Leaders / RS / Shorts 三组)+ 把 US IPO 改造为镜像 HK 的"按数据深度分级"阶梯。

**Architecture:** 新建 `us_rs_3m.py`(从 Fred6725 CSV 拿 universe,yfinance 拉 6mo k 线,本地算 `WEIGHTS_3M = 0.5·R21+0.3·R42+0.2·R63` vs SPY 的百分位)和 `us_ipo.py`(纯函数阶梯)。`main.py` 在 3 个既有 RS 闸门后串联 3M 调用,IPO 段落用 `filter_us_ipo_candidates` 替换无过滤直通。3M 表存 `raw_score` 列以便 IPO 反查 Fred6725 分布。

**Tech Stack:** Python 3.12+ / pandas / numpy / yfinance / pytest

**Spec:** `docs/superpowers/specs/2026-05-21-us-3m-rs-double-gate-design.md`

---

## File Structure

**Create:**
- `us_rs_3m.py` — 3M RS 算法 + universe fetcher + cache + filter (~150 行)
- `us_ipo.py` — `filter_us_ipo_candidates` + `_build_ipo_metrics` (~120 行)
- `tests/test_us_rs_3m.py` — 单元测试 (~150 行)
- `tests/test_us_ipo.py` — 单元测试 (~200 行)

**Modify:**
- `main.py` — RS 表注入 + 3 个调用点串联 + IPO 段落改造 + `run_screener` capture caps (~40 行净增)
- `config.toml` — 加 `min_rs_percentile_3m` / `min_market_cap` / `min_price` / `min_avg_volume`
- `cleanup.py` — 加 `rs_rating_3m_*.csv` 4 天保留 glob
- `CLAUDE.md` — RS 节 + IPO 节文档更新

**Unchanged:**
- `rs_rating.py` / `hk_rs.py` / `hk_eod.py` / `report/*` / `futu_sync.py`

---

## Phase 1: us_rs_3m.py 核心模块

### Task 1: 创建模块骨架 + `_score_from_kline`

**Files:**
- Create: `us_rs_3m.py`
- Test: `tests/test_us_rs_3m.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_us_rs_3m.py`:

```python
from datetime import date as _date

import pandas as pd
import pytest

from us_rs_3m import (
    WEIGHTS_3M,
    _score_from_kline,
)


def _flat_then_jump(start_price: float, jump_pct: float, n: int = 90) -> pd.DataFrame:
    closes = [start_price] * (n - 1) + [start_price * (1 + jump_pct / 100)]
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=n, freq="B"),
        "close": closes,
    })


def test_score_from_kline_happy_path():
    # 70 行平盘 + 末尾跳 +10% → 0.5·0.1 + 0.3·0.1 + 0.2·0.1 = 0.10
    df = _flat_then_jump(100.0, jump_pct=10, n=70)
    score, reason = _score_from_kline(df)
    assert reason == "ok"
    assert abs(score - 0.10) < 1e-9


def test_score_from_kline_no_data():
    df = pd.DataFrame({"time_key": [], "close": []})
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "no_data"


def test_score_from_kline_short_history():
    # 63 行 < 64(= max(months)*21 + 1)→ short_history
    df = _flat_then_jump(100.0, jump_pct=10, n=63)
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "short_history"


def test_score_from_kline_zero_last():
    df = _flat_then_jump(100.0, jump_pct=-100, n=70)  # 末行价 = 0
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "zero_last"


def test_score_from_kline_zero_past():
    # 中间某天价 = 0(很罕见,但要测)
    closes = [100.0] * 70
    closes[-22] = 0.0  # R21 lookback 点
    df = pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=70, freq="B"),
        "close": closes,
    })
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "zero_past"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_us_rs_3m.py -v
```

Expected: 5 FAILED with `ModuleNotFoundError: No module named 'us_rs_3m'`

- [ ] **Step 3: Write minimal implementation**

Create `us_rs_3m.py`:

```python
"""Local IBD-style Relative Strength percentile for the US universe (3-month).

Complements `rs_rating.py` (Fred6725-CSV-based 12-month RS) with a locally
computed short-window layer benchmarked against SPY. Universe is the same
~6100 tickers as `rs_rating.py` (Fred6725 CSV); SPY k-line comes from yfinance.

The 3M table is consumed twice:
  1. Long-side 3M gate on Leaders / conditional RS / Shorts
  2. US IPO ladder's 3M RS filter (via raw_score column for out-of-universe
     percentile lookup)
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

WEIGHTS_3M: list[tuple[int, float]] = [(1, 0.5), (2, 0.3), (3, 0.2)]


def _score_from_kline(
    df: pd.DataFrame,
    weights: list[tuple[int, float]] = WEIGHTS_3M,
) -> tuple[float | None, str]:
    """Compute Σ wᵢ·Rᵢ from a k-line DataFrame sorted ascending by ``time_key``.

    Returns (score, reason); score is None on rejection. Reason ∈
    {"ok", "no_data", "short_history", "zero_last", "zero_past"}.

    Minimum rows = max(months) * 21 + 1 (64 for WEIGHTS_3M).
    """
    if df is None or df.empty:
        return None, "no_data"
    max_months = max(m for m, _ in weights)
    min_rows = max_months * 21 + 1
    if len(df) < min_rows:
        return None, "short_history"
    closes = df["close"].astype(float).values
    last = float(closes[-1])
    if last <= 0:
        return None, "zero_last"

    score = 0.0
    for months, w in weights:
        idx = -1 - months * 21
        if abs(idx) > len(closes):
            return None, "short_history"
        past = float(closes[idx])
        if past <= 0:
            return None, "zero_past"
        score += w * ((last / past) - 1.0)
    return score, "ok"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_rs_3m.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add us_rs_3m.py tests/test_us_rs_3m.py
git commit -m "feat(us_rs_3m): _score_from_kline algorithm + WEIGHTS_3M"
```

---

### Task 2: `compute_us_rs_3m_table` — raw_score + rs_percentile

**Files:**
- Modify: `us_rs_3m.py`
- Modify: `tests/test_us_rs_3m.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_us_rs_3m.py`:

```python
from us_rs_3m import compute_us_rs_3m_table


def test_compute_table_relative_to_spy():
    klines = {
        f"T{i:02d}": _flat_then_jump(100.0, jump_pct=5 + i * 2, n=70)
        for i in range(1, 6)
    }
    spy = _flat_then_jump(400.0, jump_pct=0, n=70)
    table = compute_us_rs_3m_table(klines, spy)

    assert set(table.index) == set(klines.keys())
    assert "rs_percentile" in table.columns
    assert "raw_score" in table.columns
    assert table["rs_percentile"].between(0, 99).all()
    # 跳幅最大的应在最高百分位
    assert table["rs_percentile"].idxmax() == "T05"
    # raw_score 单调递增(因为 jump_pct 递增)
    ordered = table.sort_values("raw_score").index.tolist()
    assert ordered == sorted(klines.keys())


def test_compute_table_excludes_short_history():
    # 50 行 < 64 → 应被排除
    klines = {
        "GOOD": _flat_then_jump(100.0, jump_pct=10, n=70),
        "SHORT": _flat_then_jump(100.0, jump_pct=10, n=50),
    }
    spy = _flat_then_jump(400.0, jump_pct=0, n=70)
    table = compute_us_rs_3m_table(klines, spy)
    assert "GOOD" in table.index
    assert "SHORT" not in table.index


def test_compute_table_empty_when_all_short():
    klines = {"T01": _flat_then_jump(100.0, jump_pct=10, n=50)}
    spy = _flat_then_jump(400.0, jump_pct=0, n=70)
    table = compute_us_rs_3m_table(klines, spy)
    assert table.empty
    assert list(table.columns) == ["raw_score", "rs_percentile"]


def test_compute_table_spy_failure_falls_back_to_absolute(caplog):
    # SPY 数据不够 → fallback 到 spy_score=0(即绝对分数排名),记 warning
    klines = {"T01": _flat_then_jump(100.0, jump_pct=10, n=70)}
    spy = _flat_then_jump(400.0, jump_pct=0, n=50)  # < 64 → short_history
    with caplog.at_level("WARNING"):
        table = compute_us_rs_3m_table(klines, spy)
    assert "T01" in table.index
    assert any("SPY" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_us_rs_3m.py -v -k "compute_table"
```

Expected: 4 FAILED with `ImportError`/`AttributeError` on `compute_us_rs_3m_table`

- [ ] **Step 3: Add implementation to `us_rs_3m.py`**

Append:

```python
def compute_us_rs_3m_table(
    klines: dict[str, pd.DataFrame],
    spy_kline: pd.DataFrame,
) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with columns ``raw_score`` and
    ``rs_percentile`` (0-99).

    Tickers without enough history (< 64 rows) are excluded. ``raw_score``
    is retained alongside the percentile so the US IPO ladder can score
    out-of-universe candidates against the same Fred6725 distribution.
    """
    spy_score, spy_reason = _score_from_kline(spy_kline)
    if spy_score is None:
        logger.warning(
            f"[US RS 3M] SPY score rejected ({spy_reason}) — falling back to "
            f"absolute scores (effectively un-relativised)."
        )
        spy_score = 0.0

    scores: dict[str, float] = {}
    reasons: dict[str, int] = {}
    for ticker, df in klines.items():
        s, reason = _score_from_kline(df)
        reasons[reason] = reasons.get(reason, 0) + 1
        if s is None:
            continue
        scores[ticker] = s - spy_score

    logger.info(
        f"[US RS 3M] computed: {len(scores)}/{len(klines)} klines scored. "
        f"Reason breakdown: {dict(sorted(reasons.items(), key=lambda x: -x[1]))}"
    )

    if not scores:
        return pd.DataFrame(columns=["raw_score", "rs_percentile"])

    series = pd.Series(scores, name="raw_score")
    pct = series.rank(method="average", pct=True) * 99
    return pd.DataFrame({
        "raw_score": series,
        "rs_percentile": pct.round().astype(int),
    })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_rs_3m.py -v
```

Expected: 9 PASSED (5 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add us_rs_3m.py tests/test_us_rs_3m.py
git commit -m "feat(us_rs_3m): compute_us_rs_3m_table with raw_score column"
```

---

### Task 3: `filter_by_rs` + cache helpers

**Files:**
- Modify: `us_rs_3m.py`
- Modify: `tests/test_us_rs_3m.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_us_rs_3m.py`:

```python
from us_rs_3m import (
    cache_path,
    filter_by_rs,
    load_cache,
    save_cache,
)


def test_filter_by_rs_keeps_at_or_above_threshold():
    table = pd.DataFrame({
        "raw_score": [0.2, 0.1, 0.05],
        "rs_percentile": [95, 90, 50],
    }, index=["AAA", "BBB", "CCC"])
    out = filter_by_rs(["AAA", "BBB", "CCC"], table, threshold=90)
    assert set(out) == {"AAA", "BBB"}


def test_filter_by_rs_missing_passthrough():
    table = pd.DataFrame({"raw_score": [0.2], "rs_percentile": [95]}, index=["AAA"])
    out = filter_by_rs(["AAA", "ZZZ"], table, threshold=90)
    # ZZZ not in table → kept-as-missing (US long-side passthrough policy)
    assert set(out) == {"AAA", "ZZZ"}


def test_filter_by_rs_none_table_passthrough():
    out = filter_by_rs(["AAA", "BBB"], None, threshold=90)
    assert out == ["AAA", "BBB"]


def test_filter_by_rs_threshold_zero_passthrough():
    table = pd.DataFrame({
        "raw_score": [0.05],
        "rs_percentile": [10],
    }, index=["LOW"])
    out = filter_by_rs(["LOW"], table, threshold=0)
    assert out == ["LOW"]


def test_cache_path():
    p = cache_path(_date(2026, 5, 21), Path("/tmp/out"))
    assert p == Path("/tmp/out/state/rs_rating_3m_2026-05-21.csv")


def test_save_and_load_cache_roundtrip(tmp_path):
    df = pd.DataFrame({
        "raw_score": [0.2, 0.05],
        "rs_percentile": [95, 50],
    }, index=["AAA", "BBB"])
    df.index.name = "ticker"
    save_cache(df, _date(2026, 5, 21), tmp_path)

    loaded = load_cache(_date(2026, 5, 21), tmp_path)
    assert loaded is not None
    assert list(loaded.index) == ["AAA", "BBB"]
    assert loaded.loc["AAA", "rs_percentile"] == 95
    assert abs(loaded.loc["AAA", "raw_score"] - 0.2) < 1e-9
```

Make sure `Path` is imported at the top of the test file:

```python
from pathlib import Path
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_us_rs_3m.py -v -k "filter_by_rs or cache_path or save_and_load"
```

Expected: 6 FAILED with `ImportError`

- [ ] **Step 3: Add implementation to `us_rs_3m.py`**

Append:

```python
def filter_by_rs(
    tickers: list[str],
    table: pd.DataFrame | None,
    threshold: int,
) -> list[str]:
    """Keep tickers with rs_percentile >= threshold.

    Missing-from-table → KEPT (US passthrough policy, mirrors rs_rating.py
    and hk_rs.filter_by_rs). Threshold ≤ 0 → passthrough.
    """
    if table is None or table.empty or threshold <= 0:
        return list(tickers)
    out: list[str] = []
    for t in tickers:
        if t not in table.index:
            out.append(t)
            continue
        if int(table.loc[t, "rs_percentile"]) >= threshold:
            out.append(t)
    return out


def cache_path(today: date, output_dir: Path) -> Path:
    return output_dir / "state" / f"rs_rating_3m_{today.isoformat()}.csv"


def save_cache(table: pd.DataFrame, today: date, output_dir: Path) -> None:
    p = cache_path(today, output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(p, index_label="ticker")


def load_cache(today: date, output_dir: Path) -> pd.DataFrame | None:
    p = cache_path(today, output_dir)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, index_col="ticker")
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_rs_3m.py -v
```

Expected: 15 PASSED total

- [ ] **Step 5: Commit**

```bash
git add us_rs_3m.py tests/test_us_rs_3m.py
git commit -m "feat(us_rs_3m): filter_by_rs + cache save/load helpers"
```

---

### Task 4: yfinance batch fetcher

**Files:**
- Modify: `us_rs_3m.py`
- Modify: `tests/test_us_rs_3m.py`

`fetch_us_klines_yf` batches yfinance downloads exactly like `hk_eod.fetch_hk_klines_yf` does — 500 tickers per batch, retries via shared `main._yf_download_with_retry`, sparse-batch single-ticker retry via `_retry_sparse_in_batch`. The output shape is `{ticker: DataFrame[time_key, close]}` (we don't need OHLCV; just close is enough for `_score_from_kline`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_us_rs_3m.py`:

```python
def test_fetch_us_klines_yf_shapes(monkeypatch):
    """Smoke test that the fetcher returns the right DataFrame shape.

    We don't hit real yfinance — we stub _yf_download_with_retry to return
    a yfinance-shaped multiindex DataFrame.
    """
    import us_rs_3m

    def _fake_download(tickers, **kwargs):
        # Mimic yfinance batch shape: MultiIndex columns (ticker, field)
        idx = pd.date_range(end="2026-05-21", periods=80, freq="B")
        cols = pd.MultiIndex.from_product(
            [tickers, ["Open", "High", "Low", "Close", "Volume"]]
        )
        data = pd.DataFrame(100.0, index=idx, columns=cols)
        return data

    monkeypatch.setattr("us_rs_3m._yf_download_with_retry", _fake_download, raising=False)
    klines = us_rs_3m.fetch_us_klines_yf(["AAPL", "MSFT"], period="6mo", batch_size=500)
    assert set(klines.keys()) == {"AAPL", "MSFT"}
    for t, df in klines.items():
        assert "close" in df.columns
        assert "time_key" in df.columns
        assert len(df) == 80


def test_fetch_us_klines_yf_empty_input():
    import us_rs_3m
    assert us_rs_3m.fetch_us_klines_yf([], period="6mo") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_us_rs_3m.py -v -k "fetch_us_klines_yf"
```

Expected: 2 FAILED with `AttributeError: module 'us_rs_3m' has no attribute 'fetch_us_klines_yf'`

- [ ] **Step 3: Add implementation to `us_rs_3m.py`**

Append:

```python
def _yf_download_with_retry(tickers, **kwargs):
    """Indirection layer so tests can monkeypatch this attribute.

    At runtime, lazily import the helper from main.py (avoids circular import:
    main.py imports from us_rs_3m).
    """
    from main import _yf_download_with_retry as _impl
    return _impl(tickers, **kwargs)


def fetch_us_klines_yf(
    tickers: list[str],
    period: str = "6mo",
    batch_size: int = 500,
) -> dict[str, pd.DataFrame]:
    """Batch-download daily closes for US tickers via yfinance.

    Returns ``{ticker: DataFrame[time_key, close]}``. Tickers that fail the
    batch retry or come back as all-NaN are silently dropped (callers treat
    them as "not in 3M table" via the kept-as-missing policy).

    Mirrors hk_eod.fetch_hk_klines_yf structure: 500-ticker batches with
    threads=True, no inter-batch sleep (yfinance handles rate-limits via
    its own backoff).
    """
    if not tickers:
        return {}

    result: dict[str, pd.DataFrame] = {}
    n_batches = (len(tickers) - 1) // batch_size + 1
    for bidx, start in enumerate(range(0, len(tickers), batch_size), start=1):
        batch = tickers[start:start + batch_size]
        logger.info(f"[US RS 3M] yfinance batch {bidx}/{n_batches} ({len(batch)} tickers)...")
        batch_data = _yf_download_with_retry(
            batch, period=period, progress=False, group_by="ticker", threads=True,
        )
        if batch_data is None or batch_data.empty:
            logger.warning(f"[US RS 3M]   batch failed; skipping {len(batch)} tickers")
            continue
        for t in batch:
            try:
                if len(batch) == 1:
                    closes = batch_data["Close"].dropna()
                else:
                    closes = batch_data[t]["Close"].dropna()
            except (KeyError, AttributeError):
                continue
            if closes.empty:
                continue
            result[t] = pd.DataFrame({
                "time_key": closes.index,
                "close": closes.values,
            })
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_rs_3m.py -v
```

Expected: 17 PASSED

- [ ] **Step 5: Commit**

```bash
git add us_rs_3m.py tests/test_us_rs_3m.py
git commit -m "feat(us_rs_3m): fetch_us_klines_yf batch downloader"
```

---

### Task 5: Top-level `build_3m_table` orchestrator

**Files:**
- Modify: `us_rs_3m.py`
- Modify: `tests/test_us_rs_3m.py`

Single entry point that the EOD pipeline calls. Composes: get universe → fetch yfinance → compute table → save cache. Returns the DataFrame or None on failure.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_us_rs_3m.py`:

```python
def test_build_3m_table_orchestration(monkeypatch, tmp_path):
    """Verify build_3m_table composes universe→fetch→compute→cache."""
    import us_rs_3m

    def _fake_universe():
        return ["AAA", "BBB", "CCC"]

    def _fake_fetch(tickers, period="6mo", batch_size=500):
        return {
            t: _flat_then_jump(100.0, jump_pct=5 + i * 5, n=70)
            for i, t in enumerate(tickers)
        }

    def _fake_spy(period="6mo"):
        return _flat_then_jump(400.0, jump_pct=0, n=70)

    monkeypatch.setattr(us_rs_3m, "fetch_universe_from_rs_csv", _fake_universe, raising=False)
    monkeypatch.setattr(us_rs_3m, "fetch_us_klines_yf", _fake_fetch)
    monkeypatch.setattr(us_rs_3m, "_fetch_spy_kline", _fake_spy, raising=False)

    table = us_rs_3m.build_3m_table(tmp_path, _date(2026, 5, 21))
    assert table is not None
    assert set(table.index) == {"AAA", "BBB", "CCC"}
    # Cache written
    cache = tmp_path / "state" / "rs_rating_3m_2026-05-21.csv"
    assert cache.exists()


def test_build_3m_table_uses_cache(monkeypatch, tmp_path):
    """Re-running on the same day reads cache instead of refetching."""
    import us_rs_3m

    df = pd.DataFrame({
        "raw_score": [0.2, 0.1],
        "rs_percentile": [95, 50],
    }, index=["AAA", "BBB"])
    df.index.name = "ticker"
    us_rs_3m.save_cache(df, _date(2026, 5, 21), tmp_path)

    def _no_fetch_allowed(*args, **kwargs):
        pytest.fail("fetch should not be called when cache exists")

    monkeypatch.setattr(us_rs_3m, "fetch_us_klines_yf", _no_fetch_allowed)
    table = us_rs_3m.build_3m_table(tmp_path, _date(2026, 5, 21))
    assert table is not None
    assert "AAA" in table.index
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_us_rs_3m.py -v -k "build_3m_table"
```

Expected: 2 FAILED with `AttributeError: module 'us_rs_3m' has no attribute 'build_3m_table'`

- [ ] **Step 3: Add implementation to `us_rs_3m.py`**

Append:

```python
def fetch_universe_from_rs_csv(rs_table_12m: dict[str, int] | None) -> list[str]:
    """Return the ticker list from the Fred6725 12M table.

    We piggyback on the existing 12M fetch (already done at top of EOD)
    instead of re-downloading the CSV. ``rs_table_12m`` is the dict
    returned by ``rs_rating.fetch_rs_table``.
    """
    if not rs_table_12m:
        return []
    return sorted(rs_table_12m.keys())


def _fetch_spy_kline(period: str = "6mo") -> pd.DataFrame | None:
    """Fetch SPY closes via the same retrying yfinance helper as the universe.
    Returns a DataFrame with time_key + close, or None on failure.
    """
    klines = fetch_us_klines_yf(["SPY"], period=period, batch_size=1)
    return klines.get("SPY")


def build_3m_table(
    output_dir: Path,
    today: date,
    rs_table_12m: dict[str, int] | None = None,
) -> pd.DataFrame | None:
    """Orchestrator: load cache or (universe → fetch → compute → save).

    Returns None when both cache and fetch fail. Universe is derived from
    the Fred6725 12M table (must be passed in by the EOD pipeline).
    """
    cached = load_cache(today, output_dir)
    if cached is not None and not cached.empty:
        logger.info(f"[US RS 3M] Using cached table: {len(cached)} tickers")
        return cached

    tickers = fetch_universe_from_rs_csv(rs_table_12m)
    if not tickers:
        logger.warning("[US RS 3M] No universe (Fred6725 12M table empty/None); skipping 3M build")
        return None

    klines = fetch_us_klines_yf(tickers, period="6mo")
    if not klines:
        logger.warning("[US RS 3M] yfinance batch returned no data; 3M layer will passthrough")
        return None

    spy = _fetch_spy_kline(period="6mo")
    if spy is None:
        logger.warning("[US RS 3M] SPY fetch failed; computing absolute scores (un-relativised)")
        spy = pd.DataFrame({"time_key": [], "close": []})

    table = compute_us_rs_3m_table(klines, spy)
    if table.empty:
        logger.warning("[US RS 3M] No tickers scored; 3M layer will passthrough")
        return None

    save_cache(table, today, output_dir)
    logger.info(f"[US RS 3M] Built {len(table)} ticker table → cache")
    return table
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_rs_3m.py -v
```

Expected: 19 PASSED

- [ ] **Step 5: Commit**

```bash
git add us_rs_3m.py tests/test_us_rs_3m.py
git commit -m "feat(us_rs_3m): build_3m_table orchestrator (universe→fetch→compute→cache)"
```

---

## Phase 2: main.py 3M RS 闸门集成

### Task 6: 顶层配置 + 3M 表构建

**Files:**
- Modify: `main.py:1394-1466`

- [ ] **Step 1: Read existing top-of-EOD section**

```bash
sed -n '1394,1466p' main.py
```

Expected: shows the `min_rs_percentile` / `min_rs_percentile_longs` lookup and the `fetch_rs_table` call.

- [ ] **Step 2: Modify `main.py` to load config + build 3M table**

At line ~1394-1400, add `min_rs_percentile_3m` lookup. Replace:

```python
    min_rs_percentile = settings.get("min_rs_percentile", 0)
    min_rs_percentile_longs = settings.get("min_rs_percentile_longs", 0)
```

with:

```python
    min_rs_percentile = settings.get("min_rs_percentile", 0)
    min_rs_percentile_longs = settings.get("min_rs_percentile_longs", 0)
    min_rs_percentile_3m = settings.get("min_rs_percentile_3m", 0)
```

At line ~1459-1466, after the existing 12M `rs_table = fetch_rs_table(...)` call, add the 3M build:

```python
        rs_table = (
            fetch_rs_table(output_dir, today)
            if max(min_rs_percentile, min_rs_percentile_longs) > 0
            else None
        )

        # --- IBD-style 3M RS table (local, vs SPY) ---
        # Built once per run. Triggered only when the 3M knob is non-zero.
        # Universe = Fred6725 12M table's tickers (~6100). Cached to
        # state/rs_rating_3m_<date>.csv with raw_score column for IPO
        # ladder out-of-universe percentile lookup.
        import us_rs_3m  # local import: us_rs_3m imports main._yf_download_with_retry
        rs_table_3m = (
            us_rs_3m.build_3m_table(
                output_dir,
                today_date := datetime.strptime(today, "%Y_%m_%d").date(),
                rs_table_12m=rs_table,
            )
            if min_rs_percentile_3m > 0
            else None
        )
```

If `datetime` / `date` isn't already imported at the top of `main.py`, verify and add as needed. (It almost certainly already is — `today` is built earlier in the function.)

- [ ] **Step 3: Run a smoke check**

```bash
uv run python -c "from main import _yf_download_with_retry; import us_rs_3m; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Run existing tests to confirm no regression**

```bash
uv run pytest tests/test_us_rs_3m.py tests/test_smoke.py -v
```

Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(main): wire us_rs_3m.build_3m_table at top of us-eod"
```

---

### Task 7: 串联 3M 闸门到 Leaders / RS / Shorts

**Files:**
- Modify: `main.py:1540-1543` (Leaders)
- Modify: `main.py:1606-1609` (RS group)
- Modify: `main.py:318-356` (filter_shorts signature)
- Modify: `main.py:1567-1580` (filter_shorts caller)

- [ ] **Step 1: Modify `filter_shorts` signature (line 318)**

In `main.py` around line 318, find:

```python
def filter_shorts(
    filters: list[str],
    signal: str | None,
    ...
    futu_cfg: dict | None = None,
    rs_table: dict[str, int] | None = None,
    min_rs_percentile: int = 0,
) -> tuple[int, list[str]]:
```

Add two new kwargs at the end:

```python
def filter_shorts(
    filters: list[str],
    signal: str | None,
    ...
    futu_cfg: dict | None = None,
    rs_table: dict[str, int] | None = None,
    min_rs_percentile: int = 0,
    rs_table_3m: "pd.DataFrame | None" = None,
    min_rs_percentile_3m: int = 0,
) -> tuple[int, list[str]]:
```

(The forward-ref string for `pd.DataFrame` avoids requiring pandas import at function-signature time if not already imported.)

Around line 355-361 in the existing 12M RS block:

```python
    if min_rs_percentile > 0:
        kept = filter_by_rs(tickers, rs_table, min_rs_percentile, "  [Shorts]")
        kept_set = set(kept)
        market_caps = {t: c for t, c in market_caps.items() if t in kept_set}
        tickers = kept
        if not tickers:
            return total, []
```

Add a 3M block immediately after:

```python
    if min_rs_percentile_3m > 0 and rs_table_3m is not None and tickers:
        import us_rs_3m
        kept = us_rs_3m.filter_by_rs(tickers, rs_table_3m, min_rs_percentile_3m)
        kept_set = set(kept)
        market_caps = {t: c for t, c in market_caps.items() if t in kept_set}
        dropped = len(tickers) - len(kept)
        logger.info(
            f"  [Shorts] {len(kept)} after RS_3M >= {min_rs_percentile_3m} "
            f"(dropped {dropped})"
        )
        tickers = kept
        if not tickers:
            return total, []
```

- [ ] **Step 2: Modify Leaders cascade (line 1540-1543)**

Find:

```python
                if min_rs_percentile > 0 and tickers:
                    tickers = filter_by_rs(
                        tickers, rs_table, min_rs_percentile, f"  [Leaders/{name}]"
                    )
```

Append a 3M cascade immediately after:

```python
                if min_rs_percentile > 0 and tickers:
                    tickers = filter_by_rs(
                        tickers, rs_table, min_rs_percentile, f"  [Leaders/{name}]"
                    )
                if min_rs_percentile_3m > 0 and rs_table_3m is not None and tickers:
                    before = len(tickers)
                    tickers = us_rs_3m.filter_by_rs(
                        tickers, rs_table_3m, min_rs_percentile_3m
                    )
                    logger.info(
                        f"  [Leaders/{name}] {len(tickers)} after RS_3M >= "
                        f"{min_rs_percentile_3m} (dropped {before - len(tickers)})"
                    )
```

- [ ] **Step 3: Modify RS group cascade (line 1606-1609)**

Find:

```python
                    if min_rs_percentile_longs > 0 and found:
                        found = filter_by_rs(
                            found, rs_table, min_rs_percentile_longs, "  [RS]"
                        )
```

Append:

```python
                    if min_rs_percentile_longs > 0 and found:
                        found = filter_by_rs(
                            found, rs_table, min_rs_percentile_longs, "  [RS]"
                        )
                    if min_rs_percentile_3m > 0 and rs_table_3m is not None and found:
                        before = len(found)
                        found = us_rs_3m.filter_by_rs(
                            found, rs_table_3m, min_rs_percentile_3m
                        )
                        logger.info(
                            f"  [RS] {len(found)} after RS_3M >= "
                            f"{min_rs_percentile_3m} (dropped {before - len(found)})"
                        )
```

- [ ] **Step 4: Pass 3M kwargs into `filter_shorts` call (line 1567-1580)**

Find:

```python
                total, shorts_tickers = filter_shorts(
                    ...
                    rs_table=rs_table,
                    min_rs_percentile=min_rs_percentile_longs,
                )
```

Add the 3M kwargs:

```python
                total, shorts_tickers = filter_shorts(
                    ...
                    rs_table=rs_table,
                    min_rs_percentile=min_rs_percentile_longs,
                    rs_table_3m=rs_table_3m,
                    min_rs_percentile_3m=min_rs_percentile_3m,
                )
```

- [ ] **Step 5: Run regression**

```bash
uv run python -c "from main import filter_shorts; import inspect; sig = inspect.signature(filter_shorts); print('rs_table_3m' in sig.parameters, 'min_rs_percentile_3m' in sig.parameters)"
```

Expected: `True True`

```bash
uv run pytest tests/ -v 2>&1 | tail -20
```

Expected: All existing tests pass (no regression).

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(main): cascade 3M RS gate into Leaders, RS, filter_shorts"
```

---

## Phase 3: us_ipo.py 阶梯模块

### Task 8: 模块骨架 + `_build_ipo_metrics` 辅助函数

**Files:**
- Create: `us_ipo.py`
- Create: `tests/test_us_ipo.py`

`_build_ipo_metrics` is a focused, US-IPO-only metrics computation (a lean copy of HK's `build_metrics_frame` keeping only the columns the IPO ladder needs).

- [ ] **Step 1: Write the failing test**

Create `tests/test_us_ipo.py`:

```python
import math

import numpy as np
import pandas as pd
import pytest


def _make_kline(closes: list[float], highs=None, lows=None, volumes=None) -> pd.DataFrame:
    n = len(closes)
    highs = highs if highs is not None else [c * 1.02 for c in closes]
    lows = lows if lows is not None else [c * 0.98 for c in closes]
    volumes = volumes if volumes is not None else [1_000_000.0] * n
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=n, freq="B"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def test_build_ipo_metrics_short_history_returns_nans():
    from us_ipo import _build_ipo_metrics
    klines = {"NEW": _make_kline([25.0] * 15)}
    caps = {"NEW": 1e9}
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["NEW"]
    assert row["market_cap"] == 1e9
    assert row["last_price"] == 25.0
    assert math.isnan(row["avg_vol_20d"])
    assert math.isnan(row["sma50"])
    assert row["above_sma50"] is False
    assert row["above_sma200"] is False


def test_build_ipo_metrics_20day_metrics_populated():
    from us_ipo import _build_ipo_metrics
    klines = {"OK": _make_kline([20.0] * 25)}
    caps = {"OK": 5e9}
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["OK"]
    assert row["avg_vol_20d"] == 1_000_000.0
    assert abs(row["avg_dollar_vol_20d"] - 20.0 * 1_000_000.0) < 1e-6
    assert row["adr_pct"] > 0  # (high - low) / close ≠ 0


def test_build_ipo_metrics_sma50_populated_at_50_days():
    from us_ipo import _build_ipo_metrics
    klines = {"OK": _make_kline([20.0] * 55)}
    caps = {"OK": 5e9}
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["OK"]
    assert not math.isnan(row["sma50"])
    assert math.isnan(row["sma200"])  # < 200 days


def test_build_ipo_metrics_missing_cap_is_nan():
    from us_ipo import _build_ipo_metrics
    klines = {"NOCAP": _make_kline([25.0] * 30)}
    caps = {}  # no cap for NOCAP
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["NOCAP"]
    assert math.isnan(row["market_cap"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_us_ipo.py -v
```

Expected: 4 FAILED with `ModuleNotFoundError: No module named 'us_ipo'`

- [ ] **Step 3: Write implementation**

Create `us_ipo.py`:

```python
"""US IPO candidate filter — mirrors HK's filter_hk_ipo_candidates.

IPO candidates = tickers that passed a Finviz long-side screener but were
dropped by yfinance for missing/insufficient daily history. They go through
a conditional ladder based on data depth so a 30-day-old IPO can still surface
while a 200-day-old one is held to nearly the full long-side baseline.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _build_ipo_metrics(
    klines: dict[str, pd.DataFrame],
    finviz_caps: dict[str, float],
) -> pd.DataFrame:
    """Compute IPO-ladder metrics frame from k-lines.

    Columns: market_cap, last_price, avg_vol_20d, avg_dollar_vol_20d,
    adr_pct, sma50, sma200, above_sma50, above_sma200, n_rows.

    Tickers shorter than the relevant window get NaN for that window's
    metrics (matching hk_eod.build_metrics_frame semantics). Caller checks
    NaN via pd.notna in the ladder.
    """
    rows: list[dict] = []
    for t, df in klines.items():
        if df is None or df.empty:
            continue
        closes = df["close"].astype(float).values
        highs = df["high"].astype(float).values if "high" in df.columns else closes
        lows = df["low"].astype(float).values if "low" in df.columns else closes
        volumes = (
            df["volume"].astype(float).values if "volume" in df.columns
            else np.full(len(df), float("nan"))
        )
        n = len(closes)
        last = float(closes[-1])

        avg_vol_20 = float(volumes[-20:].mean()) if n >= 20 else float("nan")
        avg_dv_20 = last * avg_vol_20 if n >= 20 else float("nan")
        if n >= 20:
            adr = float(((highs[-20:] - lows[-20:]) / closes[-20:]).mean()) * 100
        else:
            adr = float("nan")
        sma50 = float(closes[-50:].mean()) if n >= 50 else float("nan")
        sma200 = float(closes[-200:].mean()) if n >= 200 else float("nan")
        above_sma50 = bool(n >= 50 and last > sma50)
        above_sma200 = bool(n >= 200 and last > sma200)

        rows.append({
            "ticker": t,
            "market_cap": finviz_caps.get(t, float("nan")),
            "last_price": last,
            "avg_vol_20d": avg_vol_20,
            "avg_dollar_vol_20d": avg_dv_20,
            "adr_pct": adr,
            "sma50": sma50,
            "sma200": sma200,
            "above_sma50": above_sma50,
            "above_sma200": above_sma200,
            "n_rows": n,
        })

    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).set_index("ticker")
    for col in ("above_sma50", "above_sma200"):
        if col in result.columns:
            result[col] = result[col].astype(bool)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_ipo.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add us_ipo.py tests/test_us_ipo.py
git commit -m "feat(us_ipo): _build_ipo_metrics — focused metrics frame for IPO ladder"
```

---

### Task 9: `filter_us_ipo_candidates` — min_history + cap + price

**Files:**
- Modify: `us_ipo.py`
- Modify: `tests/test_us_ipo.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_us_ipo.py`:

```python
def _us_settings_default():
    return {
        "min_market_cap": 300_000_000,
        "min_price": 10.0,
        "min_avg_volume": 500_000,
        "min_dollar_volume": 100_000_000,
        "min_adr_percent": 4.0,
        "min_rs_percentile_3m": 90,
    }


def test_filter_drops_when_history_below_20_days():
    from us_ipo import filter_us_ipo_candidates
    klines = {"NEW1": _make_kline([25.0] * 15)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines,
        finviz_caps={"NEW1": 5e9},
        rs_table_3m_full=None,
        spy_kline=None,
        settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["min_history"] == 1


def test_filter_drops_when_cap_below_threshold():
    from us_ipo import filter_us_ipo_candidates
    # 25 行历史(过 20 floor),但 cap < $300M
    klines = {"SMALL": _make_kline([25.0] * 25)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines,
        finviz_caps={"SMALL": 100_000_000},  # $100M < $300M
        rs_table_3m_full=None,
        spy_kline=None,
        settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["cap"] == 1


def test_filter_drops_when_cap_missing():
    from us_ipo import filter_us_ipo_candidates
    klines = {"NOCAP": _make_kline([25.0] * 25)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines,
        finviz_caps={},  # missing
        rs_table_3m_full=None,
        spy_kline=None,
        settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["cap"] == 1


def test_filter_drops_when_price_below_threshold():
    from us_ipo import filter_us_ipo_candidates
    # 25 行历史 + cap OK 但 price < $10
    klines = {"PENNY": _make_kline([5.0] * 25)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines,
        finviz_caps={"PENNY": 5e9},
        rs_table_3m_full=None,
        spy_kline=None,
        settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["price"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_us_ipo.py -v -k "filter_drops_when"
```

Expected: 4 FAILED with `ImportError`

- [ ] **Step 3: Append `filter_us_ipo_candidates` to `us_ipo.py`**

```python
def filter_us_ipo_candidates(
    klines: dict[str, pd.DataFrame],
    finviz_caps: dict[str, float],
    rs_table_3m_full: pd.DataFrame | None,
    spy_kline: pd.DataFrame | None,
    settings: dict,
) -> tuple[list[str], dict[str, int]]:
    """Filter US IPO candidates through the depth-conditional ladder.

    Drop buckets (first hit wins, mirrors HK filter_hk_ipo_candidates):
      - len(df) < 20                                   → drops['min_history']
      - cap < min_market_cap                           → drops['cap']
      - price < min_price                              → drops['price']
      - if n >= 20:
          avg_vol_20d < min_avg_volume                 → drops['avg_vol']
          avg_dollar_vol_20d < min_dollar_volume       → drops['dvol']
          adr_pct < min_adr_percent                    → drops['adr']
      - if n >= 50: not above SMA50                    → drops['sma50']
      - if n >= 200: not above SMA200                  → drops['sma200']
      - if n >= 64 and rs_table_3m_full and threshold>0:
          compute IPO 3M score vs SPY; rank against
          rs_table_3m_full['raw_score'] distribution;
          pct < min_rs_percentile_3m                   → drops['rs_3m']
    """
    min_cap = float(settings.get("min_market_cap", 300_000_000))
    min_price = float(settings.get("min_price", 10.0))
    min_avg_vol = float(settings.get("min_avg_volume", 500_000))
    min_dvol = float(settings.get("min_dollar_volume", 100_000_000))
    min_adr = float(settings.get("min_adr_percent", 4.0))
    rs_threshold = int(settings.get("min_rs_percentile_3m", 0))

    drops: dict[str, int] = {
        "min_history": 0,
        "cap": 0, "price": 0,
        "avg_vol": 0, "dvol": 0, "adr": 0,
        "sma50": 0, "sma200": 0,
        "rs_3m": 0,
    }
    metrics = _build_ipo_metrics(klines, finviz_caps)
    if metrics.empty:
        # All k-lines were empty/None — count as min_history drops
        drops["min_history"] = len(klines)
        return [], drops

    kept: list[str] = []
    for t, df in klines.items():
        if df is None or df.empty or len(df) < 20:
            drops["min_history"] += 1
            continue
        if t not in metrics.index:
            drops["min_history"] += 1
            continue
        row = metrics.loc[t]
        cap = row["market_cap"]
        if not (pd.notna(cap) and cap >= min_cap):
            drops["cap"] += 1
            continue
        price = row["last_price"]
        if not (pd.notna(price) and price >= min_price):
            drops["price"] += 1
            continue
        # ≥ 20 day metrics — guaranteed non-NaN since len(df) >= 20
        if pd.notna(row["avg_vol_20d"]) and row["avg_vol_20d"] < min_avg_vol:
            drops["avg_vol"] += 1
            continue
        if pd.notna(row["avg_dollar_vol_20d"]) and row["avg_dollar_vol_20d"] < min_dvol:
            drops["dvol"] += 1
            continue
        if pd.notna(row["adr_pct"]) and row["adr_pct"] < min_adr:
            drops["adr"] += 1
            continue
        if pd.notna(row["sma50"]) and not bool(row["above_sma50"]):
            drops["sma50"] += 1
            continue
        if pd.notna(row["sma200"]) and not bool(row["above_sma200"]):
            drops["sma200"] += 1
            continue
        # 3M RS gate placeholder (Task 11 fills in the body)
        kept.append(t)

    return kept, drops
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_ipo.py -v
```

Expected: 8 PASSED (4 from Task 8 + 4 new)

- [ ] **Step 5: Commit**

```bash
git add us_ipo.py tests/test_us_ipo.py
git commit -m "feat(us_ipo): filter_us_ipo_candidates — min_history/cap/price gates"
```

---

### Task 10: 添加 vol / dvol / ADR / SMA 闸门测试 + 验证

**Files:**
- Modify: `tests/test_us_ipo.py`

The implementation already includes these gates (added in Task 9 along with the cap/price gates). This task adds the tests that verify them.

- [ ] **Step 1: Write the tests**

Append to `tests/test_us_ipo.py`:

```python
def test_filter_drops_when_avg_vol_below_threshold():
    from us_ipo import filter_us_ipo_candidates
    klines = {"LOWVOL": _make_kline([25.0] * 25, volumes=[100_000.0] * 25)}  # 100K < 500K
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"LOWVOL": 5e9},
        rs_table_3m_full=None, spy_kline=None, settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["avg_vol"] == 1


def test_filter_drops_when_dvol_below_threshold():
    from us_ipo import filter_us_ipo_candidates
    # price=$15 × avg_vol=600K = $9M dollar vol < $100M
    klines = {"LOWDV": _make_kline([15.0] * 25, volumes=[600_000.0] * 25)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"LOWDV": 5e9},
        rs_table_3m_full=None, spy_kline=None, settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["dvol"] == 1


def test_filter_drops_when_adr_below_threshold():
    from us_ipo import filter_us_ipo_candidates
    # Flat closes with tiny H/L spread → ADR < 4%
    closes = [25.0] * 25
    highs = [25.05] * 25
    lows = [24.95] * 25
    klines = {"FLAT": _make_kline(closes, highs=highs, lows=lows, volumes=[5_000_000.0] * 25)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"FLAT": 5e9},
        rs_table_3m_full=None, spy_kline=None, settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["adr"] == 1


def test_filter_drops_when_below_sma50():
    from us_ipo import filter_us_ipo_candidates
    # First 49 closes = 30 (avg pulls sma50 high), last close = 25 below sma50
    closes = [30.0] * 49 + [25.0]
    klines = {"DEC": _make_kline(closes, highs=[c * 1.05 for c in closes],
                                  lows=[c * 0.95 for c in closes],
                                  volumes=[5_000_000.0] * 50)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"DEC": 5e9},
        rs_table_3m_full=None, spy_kline=None, settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["sma50"] == 1


def test_filter_keeps_clean_20_to_49_day_ticker():
    from us_ipo import filter_us_ipo_candidates
    # 30 行,price=25,vol=1M,wide H/L → 全部 ≥20-day gate pass; < 50 → SMA 跳过
    closes = [25.0] * 30
    highs = [27.0] * 30
    lows = [23.0] * 30
    klines = {"FRESH": _make_kline(closes, highs=highs, lows=lows,
                                    volumes=[5_000_000.0] * 30)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"FRESH": 5e9},
        rs_table_3m_full=None, spy_kline=None, settings=_us_settings_default(),
    )
    assert kept == ["FRESH"]
    assert sum(drops.values()) == 0
```

- [ ] **Step 2: Run tests to verify they pass (no impl change needed)**

```bash
uv run pytest tests/test_us_ipo.py -v
```

Expected: 13 PASSED (8 from before + 5 new)

- [ ] **Step 3: Commit**

```bash
git add tests/test_us_ipo.py
git commit -m "test(us_ipo): vol/dvol/adr/sma50 gate coverage"
```

---

### Task 11: 3M RS 闸门 (raw_score percentile 反查)

**Files:**
- Modify: `us_ipo.py`
- Modify: `tests/test_us_ipo.py`

The IPO 3M gate scores each candidate against SPY using `us_rs_3m._score_from_kline`, then percentile-ranks the score against the Fred6725 `raw_score` distribution stored in the table. This lets an out-of-universe IPO (< 120 days) be ranked alongside the established universe.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_us_ipo.py`:

```python
def test_filter_skips_rs_gate_when_below_64_days():
    """Tickers with 30 days history don't trigger the RS gate even if
    the table would have rejected them."""
    from us_ipo import filter_us_ipo_candidates
    closes = [25.0] * 30
    klines = {"YOUNG": _make_kline(closes, highs=[27.0] * 30, lows=[23.0] * 30,
                                    volumes=[5_000_000.0] * 30)}
    # Table has YOUNG with a very low raw_score — but len < 64 so the gate
    # shouldn't even fire.
    rs_table = pd.DataFrame({
        "raw_score": [0.5, 0.4, 0.3],
        "rs_percentile": [99, 95, 90],
    }, index=["A", "B", "C"])
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"YOUNG": 5e9},
        rs_table_3m_full=rs_table,
        spy_kline=_make_kline([400.0] * 80),
        settings=_us_settings_default(),
    )
    assert kept == ["YOUNG"]
    assert drops["rs_3m"] == 0


def test_filter_passes_rs_gate_when_score_at_high_percentile():
    """A 70-day IPO with +20% jump beats most Fred6725 scores → ≥90 percentile → kept."""
    from us_ipo import filter_us_ipo_candidates
    # 70 行 +20% jump in 21d window
    closes = [100.0] * 50 + [120.0] * 20  # last 21d shows 20% jump vs 21 days ago
    klines = {"WINNER": _make_kline(closes, highs=[c * 1.05 for c in closes],
                                     lows=[c * 0.95 for c in closes],
                                     volumes=[10_000_000.0] * 70)}
    # Fred6725-like distribution: scores 0.0 to 0.1 → WINNER's much higher
    # score lands in the top
    rs_table = pd.DataFrame({
        "raw_score": np.linspace(-0.1, 0.05, 100),
        "rs_percentile": np.linspace(0, 99, 100).astype(int),
    }, index=[f"F{i:03d}" for i in range(100)])
    spy = _make_kline([400.0] * 80)
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"WINNER": 5e9},
        rs_table_3m_full=rs_table, spy_kline=spy,
        settings=_us_settings_default(),
    )
    assert kept == ["WINNER"]
    assert drops["rs_3m"] == 0


def test_filter_drops_when_rs_score_below_threshold():
    """A 70-day IPO scoring lower than the 90th-percentile cutoff → drop."""
    from us_ipo import filter_us_ipo_candidates
    # 70 行 flat → score ≈ 0
    closes = [25.0] * 70
    klines = {"LOSER": _make_kline(closes, highs=[27.0] * 70, lows=[23.0] * 70,
                                    volumes=[10_000_000.0] * 70)}
    # Fred6725 distribution mostly above 0 → LOSER's ≈0 score lands at low percentile
    rs_table = pd.DataFrame({
        "raw_score": np.linspace(0.05, 0.5, 100),
        "rs_percentile": np.linspace(0, 99, 100).astype(int),
    }, index=[f"F{i:03d}" for i in range(100)])
    spy = _make_kline([400.0] * 80)
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"LOSER": 5e9},
        rs_table_3m_full=rs_table, spy_kline=spy,
        settings=_us_settings_default(),
    )
    assert kept == []
    assert drops["rs_3m"] == 1


def test_filter_passes_when_rs_table_is_none():
    """rs_table_3m_full=None → 3M gate skipped (passthrough); other gates still apply."""
    from us_ipo import filter_us_ipo_candidates
    closes = [25.0] * 70
    klines = {"PASS": _make_kline(closes, highs=[27.0] * 70, lows=[23.0] * 70,
                                   volumes=[10_000_000.0] * 70)}
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"PASS": 5e9},
        rs_table_3m_full=None, spy_kline=None,
        settings=_us_settings_default(),
    )
    assert kept == ["PASS"]
    assert drops["rs_3m"] == 0


def test_filter_passes_when_rs_threshold_is_zero():
    """min_rs_percentile_3m=0 → gate fully disabled."""
    from us_ipo import filter_us_ipo_candidates
    closes = [25.0] * 70
    klines = {"X": _make_kline(closes, highs=[27.0] * 70, lows=[23.0] * 70,
                                volumes=[10_000_000.0] * 70)}
    rs_table = pd.DataFrame({
        "raw_score": [0.5],
        "rs_percentile": [99],
    }, index=["X"])
    settings = _us_settings_default()
    settings["min_rs_percentile_3m"] = 0
    kept, drops = filter_us_ipo_candidates(
        klines=klines, finviz_caps={"X": 5e9},
        rs_table_3m_full=rs_table, spy_kline=_make_kline([400.0] * 80),
        settings=settings,
    )
    assert kept == ["X"]
    assert drops["rs_3m"] == 0
```

- [ ] **Step 2: Run tests to verify they fail (or only partially pass)**

```bash
uv run pytest tests/test_us_ipo.py -v -k "rs"
```

Expected: Tests for `score_below_threshold` FAIL (currently the gate body is the placeholder `# 3M RS gate placeholder`).

- [ ] **Step 3: Replace the `# 3M RS gate placeholder` in `us_ipo.py`**

Find this in `filter_us_ipo_candidates`:

```python
        # 3M RS gate placeholder (Task 11 fills in the body)
        kept.append(t)
```

Replace with:

```python
        # 3M RS gate — only fires for n >= 64 with a usable table.
        if (
            len(df) >= 64
            and rs_threshold > 0
            and rs_table_3m_full is not None
            and not rs_table_3m_full.empty
            and spy_kline is not None
        ):
            from us_rs_3m import _score_from_kline
            spy_score, _ = _score_from_kline(spy_kline)
            spy_score = spy_score if spy_score is not None else 0.0
            ipo_score, ipo_reason = _score_from_kline(df)
            if ipo_score is None:
                # Should be impossible (we already gated on len(df) >= 64),
                # but log defensively and drop into rs_3m bucket.
                drops["rs_3m"] += 1
                continue
            relative_score = ipo_score - spy_score
            distribution = rs_table_3m_full["raw_score"].astype(float).sort_values().values
            # Percentile of relative_score against Fred6725 distribution
            # searchsorted("right") gives the count of distribution <= score.
            rank = np.searchsorted(distribution, relative_score, side="right")
            pct = int(round(rank / max(len(distribution), 1) * 99))
            if pct < rs_threshold:
                drops["rs_3m"] += 1
                continue
        kept.append(t)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_us_ipo.py -v
```

Expected: 18 PASSED total

- [ ] **Step 5: Commit**

```bash
git add us_ipo.py tests/test_us_ipo.py
git commit -m "feat(us_ipo): 3M RS gate via raw_score percentile lookup"
```

---

## Phase 4: main.py IPO 集成

### Task 12: 捕获 Finviz market caps 到外部 dict

**Files:**
- Modify: `main.py:296-306` (`run_screener`)
- Modify: `main.py:1483-1515` (Longs section caller)
- Modify: `main.py:1534-1553` (Leaders section caller)
- Modify: `main.py:1597-1614` (RS group section caller)

`run_screener` will accept an optional `capture_caps: dict | None`. When provided, it switches to `table="Ownership"` and populates the dict with `parse_number("Market Cap")` per ticker. The return value is unchanged (just tickers).

- [ ] **Step 1: Modify `run_screener` (line 296-306)**

Replace:

```python
def run_screener(filters: list[str], signal: str | None = None) -> list[str]:
    """Run a Finviz screener and return list of tickers. Empty result is
    a valid outcome (returns []) — Finviz raises NoResults in that case."""
    kwargs = {"filters": filters}
    if signal:
        kwargs["signal"] = signal
    try:
        stock_list = Screener(**kwargs)
    except NoResults:
        return []
    return [stock["Ticker"] for stock in stock_list.data]
```

with:

```python
def run_screener(
    filters: list[str],
    signal: str | None = None,
    capture_caps: dict[str, float] | None = None,
) -> list[str]:
    """Run a Finviz screener and return list of tickers. Empty result is
    a valid outcome (returns []) — Finviz raises NoResults in that case.

    If ``capture_caps`` is provided, the screener uses ``table="Ownership"``
    so each row carries a "Market Cap" column; the parsed cap (USD) is
    stored in ``capture_caps[ticker]``. Used to feed the US IPO ladder
    after the long-side pipeline.
    """
    kwargs: dict = {"filters": filters}
    if signal:
        kwargs["signal"] = signal
    if capture_caps is not None:
        kwargs["table"] = "Ownership"
    try:
        stock_list = Screener(**kwargs)
    except NoResults:
        return []
    tickers: list[str] = []
    for stock in stock_list.data:
        t = stock["Ticker"]
        tickers.append(t)
        if capture_caps is not None:
            try:
                capture_caps[t] = parse_number(stock["Market Cap"])
            except (KeyError, ValueError):
                pass
    return tickers
```

- [ ] **Step 2: Allocate `ipo_finviz_caps` dict above the Longs section**

Just before the Longs loop (~line 1478), find:

```python
        ipo_drops: set[str] = set()
        ipo_seen_path = _eod_seen_path(output_dir, "IPO")
        ipo_seen = _load_seen(ipo_seen_path)
```

Add:

```python
        ipo_drops: set[str] = set()
        ipo_finviz_caps: dict[str, float] = {}
        ipo_seen_path = _eod_seen_path(output_dir, "IPO")
        ipo_seen = _load_seen(ipo_seen_path)
```

- [ ] **Step 3: Pass `capture_caps` in each `run_screener` call**

In the Longs loop (line 1488), Leaders loop (line 1538), and RS group (line 1604), change every `run_screener(...)` invocation to pass `capture_caps=ipo_finviz_caps`:

```python
                tickers = run_screener(
                    screener_cfg["filters"],
                    screener_cfg.get("signal"),
                    capture_caps=ipo_finviz_caps,
                )
```

Apply this change to all three call sites (Longs, Leaders, RS). Shorts uses its own internal `Screener(table="Ownership")` already and exposes caps via the `market_caps` dict inside `filter_shorts` — for IPO purposes the Shorts cap capture is not strictly needed (Shorts tickers go to Shorts.txt, not IPO.txt), so don't touch `filter_shorts`.

- [ ] **Step 4: Run a smoke check**

```bash
uv run python -c "
from main import run_screener
import inspect
sig = inspect.signature(run_screener)
assert 'capture_caps' in sig.parameters
print('ok')
"
```

Expected: `ok`

- [ ] **Step 5: Run regression**

```bash
uv run pytest tests/ -v 2>&1 | tail -10
```

Expected: All existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(main): capture Finviz market caps in run_screener for IPO ladder"
```

---

### Task 13: 集成 IPO 阶梯到 main.py

**Files:**
- Modify: `main.py:1676-1701`

- [ ] **Step 1: Modify the "Write IPO list" section**

Find the existing block (around line 1676-1701):

```python
        # --- Write IPO list ---
        # Tickers dropped by yfinance across the long-side pipeline
        # ...
        if rs_table and ipo_drops:
            non_ipo = {t for t in ipo_drops if t.upper() in rs_table}
            if non_ipo:
                logger.info(
                    f"[IPO] Dropping {len(non_ipo)} tickers with RS history "
                    f"(transient yfinance gaps, not IPOs): {sorted(non_ipo)}"
                )
                ipo_drops -= non_ipo
        sorted_ipo = sorted(ipo_drops)
        sorted_ipo = _dedup_seen("[IPO]", sorted_ipo, ipo_seen, ipo_seen_path)
        dated = us_output_dir / f"{today}_IPO.txt"
        write_watchlist(sorted_ipo, dated, fmt)
        logger.info(f"[IPO] {len(sorted_ipo)} tickers -> {dated}")
        _write_webull(sorted_ipo, dated, output_dir)
        _futu_sync(config, "ipo", sorted_ipo, "US")
```

Replace with:

```python
        # --- Write IPO list ---
        # Tickers dropped by yfinance across the long-side pipeline are
        # passed through a depth-conditional ladder (mirror of HK's
        # filter_hk_ipo_candidates): 20-day floor, cap/price/vol/dv/ADR,
        # SMA50/200, 3M RS ≥ 90.
        if rs_table and ipo_drops:
            non_ipo = {t for t in ipo_drops if t.upper() in rs_table}
            if non_ipo:
                logger.info(
                    f"[IPO] Dropping {len(non_ipo)} tickers with RS history "
                    f"(transient yfinance gaps, not IPOs): {sorted(non_ipo)}"
                )
                ipo_drops -= non_ipo

        ipo_kept: list[str] = []
        if ipo_drops:
            import us_ipo
            ipo_tickers = sorted(ipo_drops)
            logger.info(f"[IPO] Fetching k-lines for {len(ipo_tickers)} candidates...")
            ipo_klines = us_rs_3m.fetch_us_klines_yf(
                ipo_tickers, period="1y", batch_size=500,
            )
            spy_kline = None
            if rs_table_3m is not None and min_rs_percentile_3m > 0:
                # Reuse the same SPY data the 3M table used. Cheap re-fetch
                # since it's a single ticker and yfinance caches it briefly.
                spy_kline = us_rs_3m._fetch_spy_kline(period="6mo")
            ipo_kept, ipo_drop_counts = us_ipo.filter_us_ipo_candidates(
                klines=ipo_klines,
                finviz_caps=ipo_finviz_caps,
                rs_table_3m_full=rs_table_3m,
                spy_kline=spy_kline,
                settings=settings,
            )
            logger.info(
                f"[IPO] {len(ipo_kept)}/{len(ipo_tickers)} kept; drops={ipo_drop_counts}"
            )

        sorted_ipo = sorted(ipo_kept)
        sorted_ipo = _dedup_seen("[IPO]", sorted_ipo, ipo_seen, ipo_seen_path)
        dated = us_output_dir / f"{today}_IPO.txt"
        write_watchlist(sorted_ipo, dated, fmt)
        logger.info(f"[IPO] {len(sorted_ipo)} tickers -> {dated}")
        _write_webull(sorted_ipo, dated, output_dir)
        _futu_sync(config, "ipo", sorted_ipo, "US")
```

- [ ] **Step 2: Verify imports**

`us_rs_3m` is imported earlier (Task 6) but only inside the `if` block. To keep the IPO block independent, ensure `us_rs_3m` is also imported at the top of the EOD function before the IPO section — or move the `import us_rs_3m` from Task 6 to the top of `main.py`'s imports section.

Easiest: at the top of `main.py` (with the other imports around line 30-50), add:

```python
import us_rs_3m
import us_ipo
```

Then remove the `import us_rs_3m` line you added inline in Task 6 (and don't add a fresh `import us_ipo` inline here — they're now top-level).

- [ ] **Step 3: Smoke check**

```bash
uv run python -c "import main; print('imports ok')"
```

Expected: `imports ok`

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/ -v 2>&1 | tail -15
```

Expected: All tests pass; no new failures.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(main): wire us_ipo.filter_us_ipo_candidates into EOD pipeline"
```

---

## Phase 5: Plumbing

### Task 14: cleanup.py + config.toml + 单元测试

**Files:**
- Modify: `cleanup.py`
- Modify: `config.toml`
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Inspect current cleanup rules**

```bash
grep -n "rs_rating\|hk_rs_rating\|_FALLBACK\|cleanup_old" /Users/xue/finviz_to_tv/cleanup.py
```

Expected: Existing rule for `rs_rating_*.csv` with a 4-day retention.

- [ ] **Step 2: Add the test**

Append to `tests/test_cleanup.py`:

```python
def test_cleanup_keeps_recent_rs_rating_3m(tmp_path):
    """rs_rating_3m_*.csv follows the same 4-day window as rs_rating_*.csv."""
    from cleanup import cleanup_old_outputs
    state = tmp_path / "output" / "state"
    state.mkdir(parents=True)
    today = "2026_05_21"
    keep = state / "rs_rating_3m_2026_05_20.csv"  # 1 day old
    drop = state / "rs_rating_3m_2026_05_10.csv"  # 11 days old
    keep.write_text("ticker,raw_score,rs_percentile\nAAA,0.2,95\n")
    drop.write_text("ticker,raw_score,rs_percentile\nAAA,0.2,95\n")

    cleanup_old_outputs(tmp_path / "output", today)
    assert keep.exists()
    assert not drop.exists()
```

- [ ] **Step 3: Run the test to verify failure**

```bash
uv run pytest tests/test_cleanup.py::test_cleanup_keeps_recent_rs_rating_3m -v
```

Expected: FAILED (old `rs_rating_3m_*.csv` not yet covered by cleanup rules)

- [ ] **Step 4: Modify `cleanup.py`**

Locate the existing block that processes `rs_rating_*.csv` with a 4-day window. Right next to it, add an identical rule for `rs_rating_3m_*.csv`. Example (the actual block in your cleanup.py may differ slightly — keep the surrounding code):

```python
    # rs_rating CSV: 4-day window to support rs_rating._FALLBACK_MAX_AGE_DAYS=3
    _cleanup_glob(state_dir, "rs_rating_*.csv", today, max_age_days=4)
    _cleanup_glob(state_dir, "rs_rating_3m_*.csv", today, max_age_days=4)
```

If your existing `cleanup.py` uses a different helper / pattern, mirror its style exactly.

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/test_cleanup.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Update `config.toml`**

In the `[settings]` section, add the new keys near the existing RS settings:

```toml
[settings]
delay = 8
min_dollar_volume = 100_000_000  # Longs 过滤: price * avg volume >= 100M

# IBD RS Rating thresholds (Fred6725 12-month CSV, vs SPY)
# Leaders 用 min_rs_percentile (top 10%). Longs (5 splits), conditional
# RS 组, US Shorts 用 min_rs_percentile_longs (top 10%).
min_rs_percentile = 90
min_rs_percentile_longs = 90

# 第二层 3M RS 闸门 (本地算法, vs SPY) — 仅作用于 Leaders / 条件 RS 组 / Shorts
# 三个纯走势型组。Longs 5 splits 保持 12M-only (已有强事件闸门)。
# 设为 0 关闭整个 3M 层 (跳过 yfinance batch)。
min_rs_percentile_3m = 90

adr_days = 20
min_adr_percent = 4.0

# US IPO ladder 阈值 (镜像 HK conditional ladder)
# 仅作用于 IPO 候选 (Finviz 屏过但 yfinance 历史不足的 ticker)
min_market_cap = 300_000_000     # USD; 对齐 Finviz cap_smallover
min_price = 10.0                 # USD; 对齐 Finviz sh_price_o10
min_avg_volume = 500_000         # 20-day shares/day; 对齐 Finviz sh_avgvol_o500
# min_dollar_volume / min_adr_percent 复用上方既有 [settings] 值
```

Adjust the comment block to match the style of the surrounding existing comments. **Verify** by re-running the full test suite — no test should reference a key you accidentally removed.

```bash
uv run pytest tests/ -v 2>&1 | tail -10
```

- [ ] **Step 7: Commit**

```bash
git add cleanup.py config.toml tests/test_cleanup.py
git commit -m "chore: add rs_rating_3m_*.csv 4-day retention + IPO ladder config knobs"
```

---

### Task 15: CLAUDE.md 文档更新

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the IBD RS Rating / US section**

Locate the `## IBD Relative Strength Rating` section, `### US: rs_rating.py (CSV-based, vs SPY)` subsection. Replace the heading and add a 3M-layer paragraph. The existing subsection's 12M description stays — append the 3M layer details.

Find:

```markdown
### US: `rs_rating.py` (CSV-based, vs SPY)

`rs_rating.py` pulls the daily IBD-style RS percentile table (0-99) from
... All US EOD long-side groups plus US Shorts gate at **RS ≥ 90** (top 10%):
**Leaders** uses `min_rs_percentile`; **Longs (5 splits) + RS group + US Shorts**
share `min_rs_percentile_longs`. HK Shorts, Morning Gap, and IPO are NOT
RS-gated.
```

Replace with:

```markdown
### US: 12M (Fred6725 CSV, vs SPY) + 3M (local, Leaders/RS/Shorts only, vs SPY)

`rs_rating.py` pulls the daily IBD-style 12-month RS percentile table (0-99)
from `Fred6725/rs-log/output/rs_stocks.csv` ... [keep existing 12M paragraph
content unchanged].

**3M layer (added 2026-05-21).** `us_rs_3m.py` computes a second IBD-style
percentile locally using `WEIGHTS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63` vs SPY
(yfinance). Universe = Fred6725 12M table's tickers (~6100). Cached to
`output/state/rs_rating_3m_<date>.csv` with both `rs_percentile` and
`raw_score` columns (the raw_score is used by the IPO ladder to
percentile-rank out-of-universe candidates).

3M gate applies to **Leaders + conditional RS group + Shorts** only.
**Longs 5 splits stay 12M-only** — they already have strong event filters
(EarningsGap / RVol surge / GapUp / 52-week high / Top Gainer) and stacking
3M would over-tighten the universe. Both layers use threshold ≥ 90 by
default (`min_rs_percentile_3m = 90` in `[settings]`); set to 0 to disable
the 3M layer entirely (yfinance batch is skipped on disable).
```

- [ ] **Step 2: Update the IPO section**

Locate the `**IPO** (no config)` bullet in the `## Architecture` section. Replace its body with the ladder description:

```markdown
- **IPO** (no config; auto-collected sidecar of the long-side pipeline):
  Tickers that pass Finviz long-side screeners (Longs/Leaders/RS) but get
  dropped by yfinance for missing/insufficient daily history. Sent through
  a depth-conditional ladder (mirror of HK `filter_hk_ipo_candidates`,
  implementation in `us_ipo.filter_us_ipo_candidates`):

  - `len(df) < 20` → dropped (`drops['min_history']`). Fresh IPOs in their
    first 19 days are too noisy on volume.
  - **≥ 20 days** (always): cap ≥ $300M (from Finviz cap captured during
    screener pass), price ≥ $10.
  - **≥ 20 days** (with 20-day metrics): avg_vol ≥ 500K, $vol ≥ $100M,
    ADR ≥ 4.0%.
  - **≥ 50 days**: above SMA50.
  - **≥ 200 days**: above SMA200.
  - **≥ 64 days** (3M RS algorithm can score the ticker): 3M RS percentile
    ≥ 90. Percentile derived by computing the IPO's `WEIGHTS_3M` score vs
    SPY and ranking it against the long-side 3M table's `raw_score`
    distribution — i.e., "where would this IPO rank if it joined the
    Fred6725 universe today". When `rs_table_3m is None` (3M layer
    disabled or fetch failed) the RS gate is skipped entirely.

  Has its own append-only Futu group `IPO` and cross-day master
  `output/state/eod_seen_IPO.txt` — independent of `eod_seen_US.txt`, so
  a ticker that ages into having enough yfinance data still lands in its
  proper long-side group on the first qualifying day. ADR%/dollar-volume
  rejections from the long-side flow are NOT IPO drops (they're real
  filter rejections, not data gaps).
```

- [ ] **Step 3: Smoke-check the file**

```bash
wc -l CLAUDE.md
head -5 CLAUDE.md
```

Make sure the file isn't broken (no half-edited blocks, no duplicate sections).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE): document US 3M RS double gate + IPO ladder"
```

---

## Phase 6: 端到端 sanity check

### Task 16: Manual dry-run

**Files:** none (verification only)

- [ ] **Step 1: Run a real us-eod (or dry-run subset)**

Pick a recent trading day — the user can run this; we just need to verify outputs look sensible.

```bash
uv run main.py --mode us-eod 2>&1 | tee /tmp/us-eod-dry.log
```

- [ ] **Step 2: Check the log for expected new lines**

```bash
grep -E "US RS 3M|RS_3M|IPO\] " /tmp/us-eod-dry.log
```

Expected:
- `[US RS 3M] yfinance batch N/13 (...)` lines during build
- `[US RS 3M] Built NNNN ticker table → cache`
- `[Leaders/<name>] NN after RS_3M >= 90 (dropped XX)` per Leaders strategy
- `[RS] NN after RS_3M >= 90 (dropped XX)` (only if SPY+QQQ ≤ -1.2% triggers the RS scan that day)
- `[Shorts] NN after RS_3M >= 90 (dropped XX)`
- `[IPO] N/M kept; drops={'min_history': X, 'cap': Y, ...}`

- [ ] **Step 3: Check output files**

```bash
ls -la output/state/rs_rating_3m_*.csv
head -3 output/state/rs_rating_3m_*.csv
ls -la output/TV/US/ | head -20
```

Expected: New `rs_rating_3m_<date>.csv` with header `ticker,raw_score,rs_percentile`. IPO .txt may be empty or small depending on day.

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest tests/ -v 2>&1 | tail -25
```

Expected: All tests PASS.

- [ ] **Step 5: Done** — no commit (verification only).

---

## Self-Review Summary

**Spec coverage check:**
- ✅ 3M RS algorithm `WEIGHTS_3M = 0.5·R21+0.3·R42+0.2·R63` vs SPY → Task 1-2
- ✅ Universe = Fred6725 CSV ticker list → Task 5 (`fetch_universe_from_rs_csv`)
- ✅ raw_score column in 3M table → Task 2 + Task 3 cache test
- ✅ 3M gate on Leaders / RS / Shorts (not Longs) → Task 7
- ✅ `filter_shorts` signature extension → Task 7
- ✅ Single config knob `min_rs_percentile_3m = 90` → Task 6 + Task 14
- ✅ US IPO 20-day floor → Task 9
- ✅ US IPO cap/price/avg_vol/$vol/ADR=4 → Tasks 9-10
- ✅ US IPO SMA50/200 conditional → Task 10
- ✅ US IPO 3M RS via raw_score lookup → Task 11
- ✅ Finviz cap capture in `run_screener` → Task 12
- ✅ IPO pipeline integration → Task 13
- ✅ Cache cleanup + config + CLAUDE.md → Tasks 14-15
- ✅ Failure modes (passthrough on None table / empty universe / SPY missing) → Tasks 3, 5, 11

**Placeholder scan:** No TBDs, TODOs, or "implement later". Every step includes the actual code.

**Type consistency:** `rs_table_3m` is `pd.DataFrame | None` everywhere; `rs_table_3m_full` in `us_ipo.filter_us_ipo_candidates` is the same type. `finviz_caps` is `dict[str, float]` consistently.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-us-3m-rs-double-gate.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
