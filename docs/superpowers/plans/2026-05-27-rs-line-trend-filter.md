# RS Line Trend Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Annotate each US/HK long-side candidate with whether its RS line (price/index) is persistently below its own EMA21, computed cloud-side and surfaced in the EOD log — no watchlist output change (v1).

**Architecture:** A new pure-compute module `rs_line.py` derives per-ticker `rs_below_ma` / `rs_days_below_ma` / `rs_frac_below_ma` from klines the cloud RS scripts already fetch. The two cloud scripts merge these as extra columns into the published CSVs (`data/us_rs_3m/<date>.csv`, `data/hk_rs/<date>.csv`). The local pipeline reads the columns and logs a per-list RS-line summary; `.txt`/dedup/Futu output is untouched.

**Tech Stack:** Python 3.12, pandas, pytest, stdlib `tomllib`. yfinance klines (cloud only). No new dependencies.

---

## File Structure

| File | Responsibility | Status |
|------|----------------|--------|
| `rs_line.py` | Pure compute: `compute_rs_line_features`, `params_from_config`, log-summary helper `summarize_rs_line` | **create** |
| `tests/test_rs_line.py` | Unit tests for the pure compute | **create** |
| `config.toml` | New `[rs_line]` section | modify |
| `scripts/compute_us_rs_3m_cloud.py` | Merge RS-line columns into US CSV | modify |
| `scripts/compute_hk_rs_cloud.py` | Merge RS-line columns into HK CSV | modify |
| `tests/test_compute_us_rs_3m_cloud.py` | Assert US CSV gains columns | modify |
| `tests/test_compute_hk_rs_cloud.py` | Assert HK CSV gains columns | modify |
| `hk_rs.py` | `build_hk_rs_tables` returns a 3rd frame (RS-line columns) | modify |
| `tests/test_hk_rs_fetcher.py` | Assert 3rd frame surfaced | modify |
| `main.py` | Log US long-side RS-line summary after writes | modify |
| `hk_eod.py` | Extend per-category summary with RS-line counts; consume 3rd frame | modify |
| `CLAUDE.md` | One-line note under RS gating | modify |

---

## Task 1: `rs_line.py` core compute

**Files:**
- Create: `rs_line.py`
- Test: `tests/test_rs_line.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rs_line.py
import numpy as np
import pandas as pd
import pytest

from rs_line import compute_rs_line_features


def _kline(closes, start="2026-01-01"):
    idx = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({"time_key": idx, "close": np.asarray(closes, dtype=float)})


def _flat_bench(n, level=100.0, start="2026-01-01"):
    return _kline([level] * n, start=start)


def test_persistently_above_ma_not_flagged():
    # Ratio rises steadily → line stays above its own EMA → below=0.
    n = 80
    stock = _kline([100 + i for i in range(n)])      # rising
    bench = _flat_bench(n)
    out = compute_rs_line_features({"UP": stock}, bench, ma_length=21,
                                   persistence_window=20, min_history=42)
    assert int(out.loc["UP", "rs_below_ma"]) == 0
    assert int(out.loc["UP", "rs_days_below_ma"]) == 0
    assert out.loc["UP", "rs_frac_below_ma"] < 0.25


def test_persistently_below_ma_flagged():
    # Ratio falls steadily → line below its own EMA → below=1, high frac.
    n = 80
    stock = _kline([200 - i for i in range(n)])      # falling
    bench = _flat_bench(n)
    out = compute_rs_line_features({"DOWN": stock}, bench, ma_length=21,
                                   persistence_window=20, min_history=42)
    assert int(out.loc["DOWN", "rs_below_ma"]) == 1
    assert int(out.loc["DOWN", "rs_days_below_ma"]) >= 10
    assert out.loc["DOWN", "rs_frac_below_ma"] > 0.75


def test_two_day_dip_then_recover_resets_streak():
    # Strong uptrend, with the LAST 2 bars dipping below — streak should be
    # small, fraction-below low. (User's "down 2 days then up" case.)
    n = 80
    closes = [100 + i for i in range(n - 2)] + [100, 95]  # late 2-bar dip
    stock = _kline(closes)
    bench = _flat_bench(n)
    out = compute_rs_line_features({"CHOP": stock}, bench, ma_length=21,
                                   persistence_window=20, min_history=42)
    assert int(out.loc["CHOP", "rs_days_below_ma"]) <= 2
    assert out.loc["CHOP", "rs_frac_below_ma"] < 0.5


def test_scale_invariance_spy_vs_spx():
    # Benchmark scaled by a constant must not change the position signal.
    n = 80
    stock = _kline([200 - i for i in range(n)])
    bench_spy = _flat_bench(n, level=50.0)
    bench_spx = _flat_bench(n, level=500.0)   # ×10
    a = compute_rs_line_features({"X": stock}, bench_spy, min_history=42)
    b = compute_rs_line_features({"X": stock}, bench_spx, min_history=42)
    assert int(a.loc["X", "rs_below_ma"]) == int(b.loc["X", "rs_below_ma"])
    assert int(a.loc["X", "rs_days_below_ma"]) == int(b.loc["X", "rs_days_below_ma"])
    assert a.loc["X", "rs_frac_below_ma"] == b.loc["X", "rs_frac_below_ma"]


def test_short_history_excluded():
    n = 30  # < min_history
    stock = _kline([100 + i for i in range(n)])
    bench = _flat_bench(n)
    out = compute_rs_line_features({"NEW": stock}, bench, min_history=42)
    assert "NEW" not in out.index


def test_sma_path_runs():
    n = 80
    stock = _kline([200 - i for i in range(n)])
    bench = _flat_bench(n)
    out = compute_rs_line_features({"D": stock}, bench, ma_type="sma",
                                   ma_length=21, min_history=42)
    assert int(out.loc["D", "rs_below_ma"]) == 1


def test_date_alignment_handles_missing_bar():
    # Stock missing one benchmark date → inner-join aligns, no crash/NaN.
    n = 80
    bench = _flat_bench(n)
    stock = _kline([200 - i for i in range(n)])
    stock = stock.drop(index=40).reset_index(drop=True)  # drop one bar
    out = compute_rs_line_features({"D": stock}, bench, min_history=42)
    assert "D" in out.index
    assert not pd.isna(out.loc["D", "rs_below_ma"])


def test_empty_benchmark_returns_empty_schema():
    out = compute_rs_line_features({"X": _kline([1, 2, 3])}, None)
    assert list(out.columns) == ["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"]
    assert out.empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rs_line.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rs_line'`

- [ ] **Step 3: Write the implementation**

```python
# rs_line.py
"""RS-line-vs-MA trend features (TraderLion-style RS line position).

The RS line is the price-to-benchmark ratio (close / benchmark_close). This
module reports whether that line sits below its own moving average and how
persistently — the inverse of TraderLion's "RS line overtakes MA" signal.

Pure compute: no network, no I/O. Computed cloud-side (the cloud RS scripts
already fetch the klines) and published as extra CSV columns; the local
pipeline only reads the columns. Only the line's position relative to its OWN
MA is used, so the result is scale-invariant — the benchmark's absolute level
(SPX vs SPY) is irrelevant.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_MA_LENGTH = 21
DEFAULT_MA_TYPE = "ema"
DEFAULT_PERSISTENCE_WINDOW = 20
DEFAULT_MIN_HISTORY = 42

_COLUMNS = ["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"]


def _moving_average(s: pd.Series, length: int, ma_type: str) -> pd.Series:
    if ma_type == "sma":
        return s.rolling(length).mean()
    return s.ewm(span=length, adjust=False).mean()


def _trailing_streak(flags: list[bool]) -> int:
    """Count of consecutive True values at the end of the list."""
    n = 0
    for v in reversed(flags):
        if v:
            n += 1
        else:
            break
    return n


def compute_rs_line_features(
    klines: dict[str, pd.DataFrame],
    benchmark_kline: pd.DataFrame | None,
    ma_length: int = DEFAULT_MA_LENGTH,
    ma_type: str = DEFAULT_MA_TYPE,
    persistence_window: int = DEFAULT_PERSISTENCE_WINDOW,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> pd.DataFrame:
    """Per-id RS-line-vs-MA features, indexed by the ``klines`` dict key.

    Each value DataFrame must have ``time_key`` (datetime) + ``close`` columns
    (the shape returned by fetch_us_klines_yf / fetch_hk_klines_yf). Columns:
      rs_below_ma       int   1 if line < MA on the latest aligned bar else 0
      rs_days_below_ma  int   trailing consecutive bars below MA (0 if above)
      rs_frac_below_ma  float fraction of last ``persistence_window`` bars below
    Ids with < ``min_history`` aligned bars are EXCLUDED (can't seed the MA);
    consumers treat missing-from-frame as "unknown". Never raises.
    """
    if benchmark_kline is None or getattr(benchmark_kline, "empty", True):
        return pd.DataFrame(columns=_COLUMNS)
    bench = (
        benchmark_kline[["time_key", "close"]]
        .rename(columns={"close": "_bench"})
        .dropna()
    )

    rows: dict[str, tuple[int, int, float]] = {}
    for tid, df in klines.items():
        if df is None or df.empty or "close" not in df or "time_key" not in df:
            continue
        m = (
            df[["time_key", "close"]]
            .dropna()
            .merge(bench, on="time_key", how="inner")
            .sort_values("time_key")
        )
        if len(m) < min_history:
            continue
        rs = m["close"].astype(float) / m["_bench"].astype(float)
        ma = _moving_average(rs, ma_length, ma_type)
        below = (rs < ma)[ma.notna()]
        if len(below) < min_history:
            continue
        flags = [bool(v) for v in below.tolist()]
        window = flags[-persistence_window:]
        rows[tid] = (
            int(flags[-1]),
            _trailing_streak(flags),
            round(sum(window) / len(window), 3),
        )

    if not rows:
        return pd.DataFrame(columns=_COLUMNS)
    return pd.DataFrame.from_dict(rows, orient="index", columns=_COLUMNS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rs_line.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add rs_line.py tests/test_rs_line.py
git commit -m "feat(rs_line): RS-line-vs-MA persistence features (pure compute)"
```

---

## Task 2: `[rs_line]` config + `params_from_config`

**Files:**
- Modify: `config.toml` (add section)
- Modify: `rs_line.py` (add `params_from_config`)
- Test: `tests/test_rs_line.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_rs_line.py
from rs_line import params_from_config


def test_params_from_config_defaults_and_overrides():
    assert params_from_config({}) == {
        "ma_length": 21, "ma_type": "ema",
        "persistence_window": 20, "min_history": 42,
    }
    cfg = {"rs_line": {"ma_length": 50, "ma_type": "sma",
                       "persistence_window": 30, "min_history": 60}}
    assert params_from_config(cfg) == {
        "ma_length": 50, "ma_type": "sma",
        "persistence_window": 30, "min_history": 60,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rs_line.py::test_params_from_config_defaults_and_overrides -v`
Expected: FAIL with `ImportError: cannot import name 'params_from_config'`

- [ ] **Step 3: Implement `params_from_config`**

Append to `rs_line.py`:

```python
def params_from_config(config: dict) -> dict:
    """Extract compute kwargs from a parsed config dict's ``[rs_line]`` section.
    Missing keys fall back to module defaults."""
    cfg = config.get("rs_line", {}) or {}
    return {
        "ma_length": int(cfg.get("ma_length", DEFAULT_MA_LENGTH)),
        "ma_type": str(cfg.get("ma_type", DEFAULT_MA_TYPE)),
        "persistence_window": int(cfg.get("persistence_window", DEFAULT_PERSISTENCE_WINDOW)),
        "min_history": int(cfg.get("min_history", DEFAULT_MIN_HISTORY)),
    }


def is_enabled(config: dict) -> bool:
    return bool((config.get("rs_line", {}) or {}).get("enabled", True))
```

- [ ] **Step 4: Add the config section**

Add to `config.toml` after the `[settings]` US RS block (after line 28, the `min_rs_percentile_3m` line):

```toml

# RS line 趋势标注 (TraderLion 式 RS line = 价/指数, vs 它自己的 EMA)。
# 云端 (update_us_rs_3m / update_hk_rs) 从已有 k 线算 rs_below_ma /
# rs_days_below_ma / rs_frac_below_ma 三列, 本地只读列做日志标注 —— v1 不改
# .txt / dedup 输出。判据 = RS line 持续待在它均线下方 (位置+持续性, 不看均线斜率)。
[rs_line]
enabled = true
ma_length = 21          # 对齐 TraderLion RS-line 均线 (用户确认 EMA21)
ma_type = "ema"         # "ema" | "sma"
persistence_window = 20 # 占比测度的回看交易日数
min_history = 42        # 对齐后不足此根数 → unknown (排除, 不标注)
# v1 只标注。漂移阈值故意留空, 待观察真实分布后在 v2 加:
#   drop_days_below = ...   # 连续在均线下方天数
#   drop_frac_below = ...   # persistence_window 内占比
```

- [ ] **Step 5: Run tests + verify config parses**

Run: `uv run pytest tests/test_rs_line.py -v && uv run python -c "import tomllib; print(tomllib.load(open('config.toml','rb'))['rs_line'])"`
Expected: tests PASS; prints the `[rs_line]` dict.

- [ ] **Step 6: Commit**

```bash
git add config.toml rs_line.py tests/test_rs_line.py
git commit -m "feat(rs_line): [rs_line] config section + params helpers"
```

---

## Task 3: Merge RS-line columns into the US cloud CSV

**Files:**
- Modify: `scripts/compute_us_rs_3m_cloud.py:91-109`
- Test: `tests/test_compute_us_rs_3m_cloud.py`

- [ ] **Step 1: Write the failing test**

Read the existing smoke test first to reuse its monkeypatch fixtures, then add:

```python
# append to tests/test_compute_us_rs_3m_cloud.py
def test_csv_has_rs_line_columns(tmp_path, monkeypatch):
    """The published US CSV gains the three rs_line columns."""
    import numpy as np, pandas as pd
    import scripts.compute_us_rs_3m_cloud as mod

    n = 80
    idx = pd.bdate_range("2026-01-01", periods=n)
    def _kl(start_price, slope):
        return pd.DataFrame({"time_key": idx,
                             "close": [start_price + slope * i for i in range(n)]})
    klines = {"AAA": _kl(100, 1.0), "BBB": _kl(200, -1.0)}  # AAA up, BBB down
    spy = pd.DataFrame({"time_key": idx, "close": [100.0] * n})

    monkeypatch.setattr(mod, "fetch_rs_table", lambda *a, **k: {"AAA": 90, "BBB": 80})
    monkeypatch.setattr(mod, "_fetch_spy_kline", lambda **k: spy)
    monkeypatch.setattr(mod, "fetch_us_klines_yf", lambda *a, **k: klines)
    monkeypatch.setattr(mod, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "_today", lambda: __import__("datetime").date(2026, 5, 27))

    assert mod.main() == 0
    out = pd.read_csv(tmp_path / "2026-05-27.csv", index_col="ticker")
    for col in ("rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"):
        assert col in out.columns
    assert int(out.loc["BBB", "rs_below_ma"]) == 1   # falling line below MA
    assert int(out.loc["AAA", "rs_below_ma"]) == 0   # rising line above MA
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compute_us_rs_3m_cloud.py::test_csv_has_rs_line_columns -v`
Expected: FAIL — `KeyError: 'rs_below_ma'` (column not yet added)

- [ ] **Step 3: Implement the merge**

In `scripts/compute_us_rs_3m_cloud.py`, add to the imports near line 27-32:

```python
import tomllib  # noqa: E402
from rs_line import compute_rs_line_features, is_enabled, params_from_config  # noqa: E402
```

Then, between the table compute (line 94, `table = compute_us_rs_3m_table(...)`) and the coverage guard (line 97), insert:

```python
    # 4b. RS-line-vs-MA features (TraderLion-style) merged as extra columns.
    #     Computed off the SAME klines + SPY; scale-invariant so SPY≈SPX is fine.
    with open(_REPO_ROOT / "config.toml", "rb") as f:
        _cfg = tomllib.load(f)
    if is_enabled(_cfg) and spy_kline is not None and not spy_kline.empty:
        feats = compute_rs_line_features(klines, spy_kline, **params_from_config(_cfg))
        table = table.join(feats, how="left")
        logger.info(f"[Cloud RS 3M] RS-line features merged for {len(feats)} tickers")
```

(`_REPO_ROOT` is already defined at line 24.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_compute_us_rs_3m_cloud.py -v`
Expected: PASS (new test + existing tests still green)

- [ ] **Step 5: Commit**

```bash
git add scripts/compute_us_rs_3m_cloud.py tests/test_compute_us_rs_3m_cloud.py
git commit -m "feat(rs_line): merge RS-line columns into US cloud CSV"
```

---

## Task 4: Merge RS-line columns into the HK cloud CSV

**Files:**
- Modify: `scripts/compute_hk_rs_cloud.py:115-131`
- Test: `tests/test_compute_hk_rs_cloud.py`

- [ ] **Step 1: Write the failing test**

Read the existing HK cloud smoke test for its monkeypatch pattern, then add a test mirroring Task 3's: monkeypatch `fetch_hkex_equities`, `fetch_hsi_kline_yf`, `fetch_hk_klines_yf`, `_DATA_DIR`, `_METRICS_DIR`, `_today`; assert the written `data/hk_rs/<date>.csv` has columns `rs_below_ma`, `rs_days_below_ma`, `rs_frac_below_ma`, and that a falling-ratio code has `rs_below_ma == 1`.

```python
# append to tests/test_compute_hk_rs_cloud.py
def test_hk_csv_has_rs_line_columns(tmp_path, monkeypatch):
    import pandas as pd
    import scripts.compute_hk_rs_cloud as mod

    n = 300  # HK needs 12M history for percentile; plenty for EMA21 too
    idx = pd.bdate_range("2025-01-01", periods=n)
    def _kl(start, slope):
        c = [start + slope * i for i in range(n)]
        return pd.DataFrame({"time_key": idx, "open": c, "high": c, "low": c,
                             "close": c, "volume": [1_000_000] * n})
    klines = {"HK.00001": _kl(100, 1.0), "HK.00002": _kl(300, -0.5)}
    hsi = pd.DataFrame({"time_key": idx, "close": [20000.0] * n})

    monkeypatch.setattr(mod, "fetch_hkex_equities", lambda *a, **k: ["HK.00001", "HK.00002"])
    monkeypatch.setattr(mod, "fetch_hsi_kline_yf", lambda *a, **k: hsi)
    monkeypatch.setattr(mod, "fetch_hk_klines_yf", lambda *a, **k: klines)
    monkeypatch.setattr(mod, "_DATA_DIR", tmp_path / "hk_rs")
    monkeypatch.setattr(mod, "_METRICS_DIR", tmp_path / "hk_metrics")
    monkeypatch.setattr(mod, "_today", lambda: __import__("datetime").date(2026, 5, 27))

    assert mod.main() == 0
    out = pd.read_csv(tmp_path / "hk_rs" / "2026-05-27.csv", index_col="code")
    for col in ("rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"):
        assert col in out.columns
    assert int(out.loc["HK.00002", "rs_below_ma"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compute_hk_rs_cloud.py::test_hk_csv_has_rs_line_columns -v`
Expected: FAIL — `KeyError: 'rs_below_ma'`

- [ ] **Step 3: Implement the merge**

In `scripts/compute_hk_rs_cloud.py`, add to imports (near line 40-44):

```python
import tomllib  # noqa: E402
from rs_line import compute_rs_line_features, is_enabled, params_from_config  # noqa: E402
```

Then between building `combined` (line 122-126) and writing it (line 129), insert:

```python
    # 6b. RS-line-vs-MA features (TraderLion-style) on close/HSI, merged in.
    with open(_REPO_ROOT / "config.toml", "rb") as f:
        _cfg = tomllib.load(f)
    if is_enabled(_cfg) and hsi_kline is not None and not hsi_kline.empty:
        feats = compute_rs_line_features(klines, hsi_kline, **params_from_config(_cfg))
        combined = combined.join(feats, how="left")
        logger.info(f"[Cloud HK RS] RS-line features merged for {len(feats)} codes")
```

(`_REPO_ROOT` is defined at line 29; `hsi_kline` at line 93.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_compute_hk_rs_cloud.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/compute_hk_rs_cloud.py tests/test_compute_hk_rs_cloud.py
git commit -m "feat(rs_line): merge RS-line columns into HK cloud CSV"
```

---

## Task 5: Surface RS-line columns from `build_hk_rs_tables`

**Files:**
- Modify: `hk_rs.py` (`_split_combined`, `build_hk_rs_tables`)
- Test: `tests/test_hk_rs_fetcher.py`

- [ ] **Step 1: Write the failing test**

Read `tests/test_hk_rs_fetcher.py` for its cloud-CSV monkeypatch pattern, then add a test that a combined CSV carrying `rs_below_ma` etc. makes `build_hk_rs_tables` return a third frame indexed by `code` with those columns:

```python
# append to tests/test_hk_rs_fetcher.py
def test_build_returns_rs_line_frame(tmp_path, monkeypatch):
    import pandas as pd
    import hk_rs

    combined = pd.DataFrame({
        "code": ["HK.00001", "HK.00002"],
        "rs_percentile_12m": [95, 80],
        "rs_percentile_3m": [92, 70],
        "rs_below_ma": [0, 1],
        "rs_days_below_ma": [0, 14],
        "rs_frac_below_ma": [0.1, 0.9],
    }).set_index("code")
    monkeypatch.setattr(hk_rs, "_fetch_cloud_csv", lambda url, **k: combined)

    t12, t3, tline = hk_rs.build_hk_rs_tables(tmp_path, __import__("datetime").date(2026, 5, 27))
    assert tline is not None
    assert int(tline.loc["HK.00002", "rs_below_ma"]) == 1
    assert "rs_frac_below_ma" in tline.columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hk_rs_fetcher.py::test_build_returns_rs_line_frame -v`
Expected: FAIL — `ValueError: not enough values to unpack (expected 3, got 2)`

- [ ] **Step 3: Implement the third return**

In `hk_rs.py`, add a helper after `_split_combined` (line 214):

```python
_RS_LINE_COLS = ["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"]


def _extract_rs_line(combined: pd.DataFrame) -> pd.DataFrame:
    """Pull the RS-line columns out of the combined cloud CSV (if present).
    Rows where rs_below_ma is NaN (short history / pre-feature CSVs) are
    dropped, mirroring the per-column NaN handling in _split_combined."""
    present = [c for c in _RS_LINE_COLS if c in combined.columns]
    if "rs_below_ma" not in present:
        return pd.DataFrame(columns=_RS_LINE_COLS)
    out = combined[present].dropna(subset=["rs_below_ma"]).copy()
    out["rs_below_ma"] = out["rs_below_ma"].astype(int)
    if "rs_days_below_ma" in out:
        out["rs_days_below_ma"] = out["rs_days_below_ma"].astype(int)
    return out
```

Change `build_hk_rs_tables`'s signature/return type to a 3-tuple. The current return type is `tuple[pd.DataFrame | None, pd.DataFrame | None]`; make it a 3-tuple `(table_12m, table_3m, rs_line_tbl)`. At each `return` site:
- The cache-hit return (line 246) and the success return (line 271): add `_extract_rs_line(combined)` — but the cache-hit path has no `combined`. Simplest: in the cache-hit path, build the third frame from the cached 12M/3M? It doesn't carry rs_line. Instead, **read rs_line from cache too**. To avoid a new cache file, change the cache-hit branch to fall through to the cloud fetch when the rs_line columns are needed. **Decision (keep it simple):** the local cache (`hk_rs_rating_*.csv`) does NOT carry rs_line columns, so on a cache hit return `rs_line_tbl=None` (annotation degrades to "unknown" on same-day reruns — acceptable, annotation-only).
  - Cache-hit return becomes: `return cached_12m, cached_3m, None`
- The not-found return (line 278): `return None, None, None`
- The success return (line 271): `return table_12m, table_3m, _extract_rs_line(combined)`

- [ ] **Step 4: Update the existing call site**

In `hk_eod.py:971`, change:

```python
    rs_table_12m, rs_table_3m = build_hk_rs_tables(output_dir, today_d)
```
to:
```python
    rs_table_12m, rs_table_3m, rs_line_tbl = build_hk_rs_tables(output_dir, today_d)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_hk_rs_fetcher.py tests/test_hk_rs.py -v`
Expected: PASS (update any other test that unpacks `build_hk_rs_tables` into 2 vars — search `build_hk_rs_tables` in tests and widen to 3).

- [ ] **Step 6: Commit**

```bash
git add hk_rs.py hk_eod.py tests/test_hk_rs_fetcher.py
git commit -m "feat(rs_line): surface RS-line frame from build_hk_rs_tables"
```

---

## Task 6: Log-summary helper + US annotation

**Files:**
- Modify: `rs_line.py` (add `summarize_rs_line`)
- Modify: `main.py` (after long-side writes, ~line 1789)
- Test: `tests/test_rs_line.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_rs_line.py
from rs_line import summarize_rs_line


def test_summarize_counts_and_lists_below():
    feats = pd.DataFrame.from_dict(
        {"AAA": (0, 0, 0.05), "BBB": (1, 14, 0.90), "CCC": (1, 3, 0.40)},
        orient="index", columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"],
    )
    s = summarize_rs_line(["AAA", "BBB", "CCC"], feats)
    assert "1 above MA" in s
    assert "2 below" in s
    assert "BBB" in s and "14d" in s   # most-persistent listed first


def test_summarize_handles_missing_and_none():
    assert summarize_rs_line(["X"], None) is None
    feats = pd.DataFrame(columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"])
    assert summarize_rs_line(["X"], feats) is None
    # ticker absent from table → counted as unknown (omitted), not crash
    feats2 = pd.DataFrame.from_dict({"AAA": (0, 0, 0.0)}, orient="index",
                                    columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"])
    assert "1 above MA, 0 below" in summarize_rs_line(["AAA", "ZZZ"], feats2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rs_line.py::test_summarize_counts_and_lists_below -v`
Expected: FAIL — `ImportError: cannot import name 'summarize_rs_line'`

- [ ] **Step 3: Implement `summarize_rs_line`**

Append to `rs_line.py`:

```python
def summarize_rs_line(ids, features: pd.DataFrame | None) -> str | None:
    """One-line RS-line summary for a list of ids against a features frame.
    Returns None when no usable feature data exists (annotation skipped).
    Ids missing from the frame are treated as 'unknown' and omitted from counts.
    """
    if features is None or features.empty or "rs_below_ma" not in features.columns:
        return None
    above = 0
    below: list[tuple[str, int, float]] = []
    for i in ids:
        if i not in features.index:
            continue
        val = features.loc[i, "rs_below_ma"]
        if pd.isna(val):
            continue
        if int(val) == 1:
            d = features.loc[i, "rs_days_below_ma"]
            f = features.loc[i, "rs_frac_below_ma"]
            below.append((i, int(d) if pd.notna(d) else 0, float(f) if pd.notna(f) else 0.0))
        else:
            above += 1
    if above == 0 and not below:
        return None
    detail = ", ".join(f"{i}({d}d,{f:.0%})" for i, d, f in sorted(below, key=lambda x: -x[1]))
    tail = f" — below: {detail}" if below else ""
    return f"RS-line: {above} above MA, {len(below)} below{tail}"
```

- [ ] **Step 4: Wire into `main.py`**

`build_3m_table` already returns the full CSV frame (with the new columns) as `rs_table_3m`. After the RS write block (after line 1788, before the IPO block at 1790), insert a summary that reuses the already-written lists. First, retain the written lists by collecting them. Add right before the Longs write loop (line 1757) a collector, and populate it in the loop:

Change the Longs write loop (lines 1757-1766) to also record what was written:
```python
        written_longs: dict[str, list[str]] = {}
        for key, name, tickers in longs_dedup:
            futu_key = f"longs_{key}"
            file_stem = futu_groups_cfg.get(futu_key) or key
            sorted_t = sorted(tickers)
            sorted_t = _dedup_seen(f"[Longs/{key}]", sorted_t, us_seen, us_seen_path)
            dated = us_output_dir / f"{today}_{file_stem}.txt"
            write_watchlist(sorted_t, dated, fmt)
            logger.info(f"[Longs/{key}] {len(sorted_t)} tickers -> {dated}")
            _write_webull(sorted_t, dated, output_dir)
            _futu_sync(config, futu_key, sorted_t, "US")
            written_longs[key] = sorted_t
```

Then after the RS write block (after line 1788), add:
```python
        # --- RS-line trend annotation (v1: log only, no output change) ---
        import rs_line
        for key, tickers in written_longs.items():
            s = rs_line.summarize_rs_line(tickers, rs_table_3m)
            if s:
                logger.info(f"[Longs/{key}] {s}")
        if config.get("leaders"):
            s = rs_line.summarize_rs_line(sorted_leaders, rs_table_3m)
            if s:
                logger.info(f"[Leaders] {s}")
        if rs_ran:
            s = rs_line.summarize_rs_line(sorted_rs, rs_table_3m)
            if s:
                logger.info(f"[RS] {s}")
```

(`rs_table_3m` is the DataFrame from `build_3m_table` at line 1539; when `min_rs_percentile_3m == 0` it is None → `summarize_rs_line` returns None → no annotation. Acceptable.)

- [ ] **Step 5: Run tests + targeted import check**

Run: `uv run pytest tests/test_rs_line.py -v && uv run python -c "import ast; ast.parse(open('main.py').read()); print('main.py parses')"`
Expected: tests PASS; `main.py parses`.

- [ ] **Step 6: Commit**

```bash
git add rs_line.py main.py tests/test_rs_line.py
git commit -m "feat(rs_line): log US long-side RS-line trend summary (annotate-only)"
```

---

## Task 7: HK annotation in the per-category summary

**Files:**
- Modify: `hk_eod.py` (per-category summary block, lines 1094-1117)
- Test: `tests/test_hk_eod.py`

- [ ] **Step 1: Write the failing test**

Read `tests/test_hk_eod.py` for how `run_hk_eod` is driven (or whether a smaller helper is testable). If `run_hk_eod` is too heavy to drive directly, factor the annotation into a tiny pure helper and test that instead:

Add a pure helper to `hk_eod.py` and test it:
```python
# append to tests/test_hk_eod.py
def test_hk_rs_line_counts():
    import pandas as pd
    from hk_eod import _rs_line_group_note
    tline = pd.DataFrame.from_dict(
        {"HK.00001": (0, 0, 0.0), "HK.00002": (1, 12, 0.85)},
        orient="index", columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"],
    )
    # group of two codes, one below MA
    assert _rs_line_group_note(["HK.00001", "HK.00002"], tline) == " | RS↓1"
    assert _rs_line_group_note(["HK.00001"], tline) == ""        # none below → no note
    assert _rs_line_group_note(["HK.00001"], None) == ""         # no table → no note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_hk_eod.py::test_hk_rs_line_counts -v`
Expected: FAIL — `ImportError: cannot import name '_rs_line_group_note'`

- [ ] **Step 3: Implement the helper + wire it in**

Add to `hk_eod.py` (near the top-level helpers):
```python
def _rs_line_group_note(codes, rs_line_tbl) -> str:
    """ ' | RS↓N' where N = how many of `codes` have their RS line below its MA.
    Empty string when the table is absent or no code is below (keeps the
    summary line clean). `codes` are Futu codes (rs_line_tbl index format)."""
    if rs_line_tbl is None or getattr(rs_line_tbl, "empty", True):
        return ""
    if "rs_below_ma" not in rs_line_tbl.columns:
        return ""
    n = 0
    for c in codes:
        if c in rs_line_tbl.index:
            v = rs_line_tbl.loc[c, "rs_below_ma"]
            if not _pd_isna(v) and int(v) == 1:
                n += 1
    return f" | RS↓{n}" if n else ""
```
Add `import pandas as pd` at module top if not present, and define `_pd_isna = pd.isna` (or inline `pd.isna`).

In the per-category summary loop (lines 1099-1111), the codes for group `n` are `dedup.get(n, [])` (Futu-code format — matches `rs_line_tbl` index). Append the note to the written line:
```python
        note = f"  [{masked} already-seen]" if masked > 0 else ""
        note += _rs_line_group_note(dedup.get(n, []), rs_line_tbl)
        logger.info(
            f"[HK Longs]   {n:<11} "
            f"{pre_counts.get(n, 0):>3} → {post_rs_counts.get(n, 0):>3} → "
            f"{len(dedup.get(n, [])):>3} → {written:>3}{note}"
        )
```

(`rs_line_tbl` is in scope from the Task-5 change to line 971.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hk_eod.py tests/test_rs_line.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hk_eod.py tests/test_hk_eod.py
git commit -m "feat(rs_line): annotate HK per-category summary with RS-line counts"
```

---

## Task 8: Docs + full suite

**Files:**
- Modify: `CLAUDE.md` (RS gating section)
- Modify: `data/us_rs_3m/README.md`, `data/hk_rs/README.md` (schema note, if present)

- [ ] **Step 1: Update CLAUDE.md**

Under the "RS gating" section, add a bullet:
```markdown
- **RS-line trend (annotate-only, v1):** cloud scripts publish `rs_below_ma` /
  `rs_days_below_ma` / `rs_frac_below_ma` (RS line = price/index vs its own
  EMA21) as extra CSV columns; the EOD log annotates long-side survivors whose
  RS line is persistently below its MA. No `.txt`/dedup effect. See
  `docs/superpowers/specs/2026-05-27-rs-line-trend-filter-design.md`.
```

- [ ] **Step 2: Update data READMEs (if they document columns)**

If `data/us_rs_3m/README.md` / `data/hk_rs/README.md` list the CSV columns, append the three `rs_*` columns with a one-line description each.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest tests/ -q`
Expected: all green (no regressions).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md data/us_rs_3m/README.md data/hk_rs/README.md
git commit -m "docs(rs_line): note RS-line trend annotation in CLAUDE.md + data READMEs"
```

---

## Post-implementation: seed cloud CSVs

After Tasks 3-4 merge, the new columns only appear once the cloud workflows
run. Manually trigger both via `workflow_dispatch` (or wait for the weekday
cron) so `data/{us_rs_3m,hk_rs}/<date>.csv` carry the columns before the next
local EOD run. Until then, `build_3m_table` / `build_hk_rs_tables` return frames
without the columns → `summarize_rs_line` / `_rs_line_group_note` return
None/"" → no annotation (graceful, no crash).

## Self-Review

- **Spec coverage:** rs_line module (T1), config (T2), cloud merge US/HK (T3/T4),
  local surfacing HK (T5) + US auto via build_3m_table, annotation US (T6) + HK
  (T7), docs (T8), scale-invariance + short-history + persistence tests (T1).
  v2 drop threshold is explicitly out of scope per the spec. ✓
- **Placeholders:** none — every code step has complete code. The only deferred
  items (`drop_*` thresholds) are commented-out in config by design (v2). ✓
- **Type consistency:** `compute_rs_line_features` returns columns
  `rs_below_ma`(int), `rs_days_below_ma`(int), `rs_frac_below_ma`(float)
  everywhere; `summarize_rs_line(ids, features)` and
  `_rs_line_group_note(codes, tbl)` signatures used consistently;
  `build_hk_rs_tables` 3-tuple updated at its only call site (hk_eod.py:971) and
  in tests. ✓

## Notes for the implementer

- **Annotate-only invariant:** after every task, the `.txt`/Webull/Futu/dedup
  output must be byte-identical to before. If a test or manual run shows a
  watchlist diff, you broke the invariant — stop and fix.
- **Bool→CSV:** `rs_below_ma` is stored as int (1/0), not Python bool, so a
  `table.join(..., how="left")` NaN-fill for missing tickers round-trips through
  CSV cleanly (object/float, never the string "True").
- **No local kline refetch:** the local pipeline must never compute RS-line
  features itself — it only reads published columns. Computation stays cloud-side.
