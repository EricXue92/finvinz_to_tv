# RS New High 强势子清单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从当日已选长线侧股票里再筛出"RS line 在/接近 6 个月最高点"的最强势子集,输出独立 `RSNewHigh` 清单(US + HK)。

**Architecture:** 沿用现有"云端算列 / 本地读列"管线。云端(GitHub Actions)从已抓 k 线算一个连续列 `rs_pct_off_high`(= RS line 距其全历史最高值的百分比,0=当日新高),并入已发布的 `data/{us_rs_3m,hk_rs}/<date>.csv`。本地 EOD 读该列,对当日长线侧 survivors 做 `rs_pct_off_high ≤ nh_tolerance` 的正向筛选,写独立 `.txt` + Webull 镜像 + Futu 同步。阈值留本地可调,改带宽永不重抓 k 线。

**Tech Stack:** Python 3 / pandas;`uv` 跑测试(`uv run python -m pytest`);现有模块 `rs_line.py`、`us_rs_3m.py`、`hk_rs.py`、`main.py`、`hk_eod.py`、两个 `scripts/compute_*_cloud.py`。

## Global Constraints

- **纯计算函数不抛异常、不做网络/IO**:`rs_line.py` 内所有 compute/select 函数失败时返回空/降级,绝不 raise(对齐现有 `compute_rs_line_features`)。
- **unknown 处理方向**:本特征是**正向精选** —— 缺列/缺票/历史不足/NaN 的 ticker **不纳入** RS-NH(无法确认新高就不收)。注意这与现有 RS 闸门"missing → KEPT"方向相反,是有意为之。
- **不引入独立跨日 master**:RS-NH 是已 dedup 长线侧的纯子集,dedup 由父清单继承,可每日重新检测(同 RS/Shorts)。
- **空清单也写 0 字节 `.txt`**,Futu no-op(空 ticker 不擦组)—— 对齐现有 `write_watchlist` / `_futu_sync` 不变量。
- **Futu 组非 append-only**:`RSNewHigh` / `HKRSNewHigh` 是 diff 式(DEL+ADD),**不**加入 `[futu] append_only_groups`。
- **min_history = 42**(复用现有 `[rs_line]` 默认),起始 `nh_tolerance = 0.02`。
- 测试命令一律 `uv run python -m pytest`(`uv run pytest` 无法 spawn)。
- 提交信息末尾附 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

### Task 1: `compute_rs_new_high`(云端计算列)

**Files:**
- Modify: `rs_line.py`(在 `compute_rs_line_features` 之后新增函数 + 模块常量)
- Test: `tests/test_rs_line.py`(沿用现有 `_kline` / `_flat_bench` 辅助)

**Interfaces:**
- Produces: `compute_rs_new_high(klines: dict[str, pd.DataFrame], benchmark_kline: pd.DataFrame | None, *, min_history: int = 42) -> pd.DataFrame` —— 单列 `rs_pct_off_high`(float),按 `klines` dict key 索引。空 benchmark/空 klines → `DataFrame(columns=["rs_pct_off_high"])`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_rs_line.py` 顶部 import 处加入 `compute_rs_new_high`:

```python
from rs_line import (
    compute_rs_direction,
    compute_rs_line_features,
    compute_rs_new_high,
    compute_rs_reversal,
    direction_params_from_config,
    find_anomaly_ids,
    params_from_config,
    summarize_rs_line,
    tolerance_from_config,
    adr_mult_from_config,
    DEFAULT_ADR_MULT,
)
```

在文件末尾追加:

```python
def test_rs_new_high_zero_when_at_high():
    n = 80
    stock = _kline([100 + i for i in range(n)])   # RS line 单调上行 → 当日即最高
    bench = _flat_bench(n)
    out = compute_rs_new_high({"UP": stock}, bench, min_history=42)
    assert abs(float(out.loc["UP", "rs_pct_off_high"])) < 1e-9


def test_rs_new_high_positive_after_pullback():
    n = 80
    closes = [100 + i for i in range(n - 5)] + [104, 103, 102, 101, 100]  # 末段回踩
    stock = _kline(closes)
    bench = _flat_bench(n)
    out = compute_rs_new_high({"DOWN": stock}, bench, min_history=42)
    assert float(out.loc["DOWN", "rs_pct_off_high"]) > 0.0


def test_rs_new_high_scale_invariant():
    n = 80
    closes = [100 + i for i in range(n - 5)] + [104, 103, 102, 101, 100]
    stock = _kline(closes)
    a = compute_rs_new_high({"X": stock}, _flat_bench(n, level=50.0), min_history=42)
    b = compute_rs_new_high({"X": stock}, _flat_bench(n, level=500.0), min_history=42)
    assert abs(float(a.loc["X", "rs_pct_off_high"]) - float(b.loc["X", "rs_pct_off_high"])) < 1e-9


def test_rs_new_high_split_excluded():
    n = 80
    closes = [100 + i for i in range(n - 1)] + [10]   # 末根 ~ -90% 跳变(拆股)
    stock = _kline(closes)
    bench = _flat_bench(n)
    out = compute_rs_new_high({"SPL": stock}, bench, min_history=42)
    assert "SPL" not in out.index


def test_rs_new_high_short_history_excluded():
    n = 30
    stock = _kline([100 + i for i in range(n)])
    out = compute_rs_new_high({"NEW": stock}, _flat_bench(n), min_history=42)
    assert "NEW" not in out.index


def test_rs_new_high_empty_benchmark_returns_schema():
    out = compute_rs_new_high({"X": _kline([1, 2, 3])}, None)
    assert list(out.columns) == ["rs_pct_off_high"]
    assert out.empty
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_rs_line.py -k rs_new_high -v`
Expected: FAIL —— `ImportError: cannot import name 'compute_rs_new_high'`

- [ ] **Step 3: 写最小实现**

在 `rs_line.py` 中,`compute_rs_line_features` 函数结束后(约 `rs_line.py:156`,`compute_rs_direction` 之前)插入。注意复用现有模块级 `_has_bar_anomaly` 与 `_moving_average` 同区:

```python
_NEW_HIGH_COLUMNS = ["rs_pct_off_high"]


def compute_rs_new_high(
    klines: dict[str, pd.DataFrame],
    benchmark_kline: pd.DataFrame | None,
    *,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> pd.DataFrame:
    """Per-id distance of the RS line from its own all-history maximum.

    RS line = close / benchmark_close (date-aligned inner join, same shape as
    compute_rs_line_features). The single output column ``rs_pct_off_high`` is
    ``(window_max - rs[-1]) / window_max`` over the FULL aligned history, clamped
    at 0 on the low side (numerical noise). 0.0 == latest bar is the high; larger
    == further below the high. Scale-invariant (benchmark constant cancels).

    Ids with < ``min_history`` aligned bars, or with a single-bar |return| >=
    ANOMALY_BAR_THRESHOLD anywhere in the aligned series (split / bad quote that
    would poison the max), are EXCLUDED — consumers treat missing-from-frame as
    "unknown". Never raises.
    """
    if benchmark_kline is None or getattr(benchmark_kline, "empty", True):
        return pd.DataFrame(columns=_NEW_HIGH_COLUMNS)
    bench = (
        benchmark_kline[["time_key", "close"]]
        .rename(columns={"close": "_bench"})
        .dropna()
    )

    rows: dict[str, float] = {}
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
        # Split / bad quote anywhere in the full series would warp the max →
        # exclude. Window = whole series, so check the whole series.
        if _has_bar_anomaly(rs, len(rs) - 1):
            continue
        window_max = float(rs.max())
        if window_max <= 0:
            continue
        off = (window_max - float(rs.iloc[-1])) / window_max
        rows[tid] = round(max(off, 0.0), 4)

    if not rows:
        return pd.DataFrame(columns=_NEW_HIGH_COLUMNS)
    return pd.DataFrame.from_dict(rows, orient="index", columns=_NEW_HIGH_COLUMNS)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_rs_line.py -k rs_new_high -v`
Expected: PASS(6 个用例)

- [ ] **Step 5: 提交**

```bash
git add rs_line.py tests/test_rs_line.py
git commit -m "feat(rs-line): compute_rs_new_high — RS-line distance from all-history max

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 配置 helpers + 本地选择函数

**Files:**
- Modify: `rs_line.py`(在 `params_from_config` / `is_enabled` 附近新增 4 个 helper)
- Test: `tests/test_rs_line.py`

**Interfaces:**
- Consumes: `compute_rs_new_high`(签名见 Task 1,用于 `new_high_params_from_config` 的 kwargs 对齐)。
- Produces:
  - `new_high_params_from_config(config: dict) -> dict` —— `{"min_history": int}`(splat 进 `compute_rs_new_high`)。
  - `nh_is_enabled(config: dict) -> bool` —— `[rs_line].nh_enabled`,默认 True。
  - `nh_tolerance_from_config(config: dict) -> float` —— `[rs_line].nh_tolerance`,默认 0.02。
  - `select_rs_new_high(candidates: list[str], features: pd.DataFrame | None, tolerance: float) -> tuple[list[str], dict]` —— 返回 `(sorted_selected, stats)`;`stats` 键:`total, selected, le_1pct, le_2pct, le_5pct, unknown`(int)。id 在 `features` 缺失/NaN/列不存在 → unknown(不纳入)。
  - `format_rs_new_high_summary(stats: dict) -> str` —— 一行日志文案。

- [ ] **Step 1: 写失败测试**

在 import 块加入这 5 个名字(追加到 Task 1 已改的 import 列表):

```python
from rs_line import (
    compute_rs_direction,
    compute_rs_line_features,
    compute_rs_new_high,
    compute_rs_reversal,
    direction_params_from_config,
    find_anomaly_ids,
    format_rs_new_high_summary,
    new_high_params_from_config,
    nh_is_enabled,
    nh_tolerance_from_config,
    params_from_config,
    select_rs_new_high,
    summarize_rs_line,
    tolerance_from_config,
    adr_mult_from_config,
    DEFAULT_ADR_MULT,
)
```

文件末尾追加:

```python
def _nh_frame(mapping):
    # mapping: {id: rs_pct_off_high or None}
    import pandas as pd
    return pd.DataFrame.from_dict(
        {k: [v] for k, v in mapping.items()},
        orient="index", columns=["rs_pct_off_high"],
    )


def test_nh_config_helpers_defaults_and_overrides():
    assert new_high_params_from_config({}) == {"min_history": 42}
    assert nh_is_enabled({}) is True
    assert nh_tolerance_from_config({}) == 0.02
    cfg = {"rs_line": {"nh_enabled": False, "nh_tolerance": 0.05, "nh_min_history": 30}}
    assert nh_is_enabled(cfg) is False
    assert nh_tolerance_from_config(cfg) == 0.05
    assert new_high_params_from_config(cfg) == {"min_history": 30}


def test_select_rs_new_high_filters_and_counts():
    feats = _nh_frame({"A": 0.005, "B": 0.018, "C": 0.04, "D": 0.09})
    selected, stats = select_rs_new_high(["A", "B", "C", "D", "E"], feats, 0.02)
    assert selected == ["A", "B"]                       # E 缺失 → unknown
    assert stats["total"] == 5
    assert stats["selected"] == 2
    assert stats["le_1pct"] == 1                         # A
    assert stats["le_2pct"] == 2                         # A, B
    assert stats["le_5pct"] == 3                         # A, B, C
    assert stats["unknown"] == 1                         # E


def test_select_rs_new_high_missing_column_all_unknown():
    import pandas as pd
    feats = pd.DataFrame.from_dict({"A": [1]}, orient="index", columns=["rs_below_ma"])
    selected, stats = select_rs_new_high(["A", "B"], feats, 0.02)
    assert selected == []
    assert stats["unknown"] == 2
    assert stats["selected"] == 0


def test_select_rs_new_high_none_frame():
    selected, stats = select_rs_new_high(["A", "B"], None, 0.02)
    assert selected == []
    assert stats["total"] == 2 and stats["unknown"] == 2


def test_select_rs_new_high_nan_is_unknown():
    feats = _nh_frame({"A": 0.01, "B": None})
    selected, stats = select_rs_new_high(["A", "B"], feats, 0.02)
    assert selected == ["A"]
    assert stats["unknown"] == 1


def test_format_rs_new_high_summary():
    s = format_rs_new_high_summary(
        {"total": 5, "selected": 2, "le_1pct": 1, "le_2pct": 2, "le_5pct": 3, "unknown": 1}
    )
    assert "2/5" in s
    assert "unknown" in s
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_rs_line.py -k "nh_config or select_rs_new_high or format_rs_new_high" -v`
Expected: FAIL —— ImportError(这些名字尚不存在)

- [ ] **Step 3: 写最小实现**

在 `rs_line.py` 的 `is_enabled` 函数之后(约 `rs_line.py:291`)插入:

```python
def new_high_params_from_config(config: dict) -> dict:
    """``compute_rs_new_high`` kwargs from ``[rs_line]``. Reuses the shared
    ``min_history`` floor (nh_min_history override) — splatted directly."""
    cfg = config.get("rs_line", {}) or {}
    return {"min_history": int(cfg.get("nh_min_history", cfg.get("min_history", DEFAULT_MIN_HISTORY)))}


def nh_is_enabled(config: dict) -> bool:
    """Whether the RS-New-High sub-list is produced (``[rs_line].nh_enabled``)."""
    return bool((config.get("rs_line", {}) or {}).get("nh_enabled", True))


def nh_tolerance_from_config(config: dict) -> float:
    """Max ``rs_pct_off_high`` to qualify for RS-New-High (fraction; 0.02 = 2%)."""
    return float((config.get("rs_line", {}) or {}).get("nh_tolerance", 0.02))


def select_rs_new_high(
    candidates: list[str],
    features: pd.DataFrame | None,
    tolerance: float,
) -> tuple[list[str], dict]:
    """From ``candidates`` (ids in ``features`` index format), return those whose
    ``rs_pct_off_high`` <= ``tolerance``, plus a stats dict. Ids missing from the
    frame, with NaN, or when the column is absent are 'unknown' → EXCLUDED (this
    is a positive highlight filter: can't confirm a new high → don't include).
    Never raises. stats keys: total, selected, le_1pct, le_2pct, le_5pct, unknown.
    """
    stats = {"total": len(candidates), "selected": 0,
             "le_1pct": 0, "le_2pct": 0, "le_5pct": 0, "unknown": 0}
    have_col = (
        features is not None
        and not getattr(features, "empty", True)
        and "rs_pct_off_high" in features.columns
    )
    if not have_col:
        stats["unknown"] = len(candidates)
        return [], stats
    selected: list[str] = []
    for cid in candidates:
        if cid not in features.index:
            stats["unknown"] += 1
            continue
        val = features.loc[cid, "rs_pct_off_high"]
        if pd.isna(val):
            stats["unknown"] += 1
            continue
        off = float(val)
        if off <= 0.01:
            stats["le_1pct"] += 1
        if off <= 0.02:
            stats["le_2pct"] += 1
        if off <= 0.05:
            stats["le_5pct"] += 1
        if off <= tolerance:
            selected.append(cid)
    stats["selected"] = len(selected)
    return sorted(selected), stats


def format_rs_new_high_summary(stats: dict) -> str:
    """One-line RS-New-High distribution log."""
    return (
        f"RS-NH: {stats['selected']}/{stats['total']} selected "
        f"(<=1%: {stats['le_1pct']}, <=2%: {stats['le_2pct']}, "
        f"<=5%: {stats['le_5pct']}; unknown: {stats['unknown']})"
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_rs_line.py -v`
Expected: PASS(全部,含 Task 1 用例)

- [ ] **Step 5: 提交**

```bash
git add rs_line.py tests/test_rs_line.py
git commit -m "feat(rs-line): select_rs_new_high + nh config helpers + summary formatter

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: HK 框架透传 `rs_pct_off_high` + config.toml

**Files:**
- Modify: `hk_rs.py:217`(`_RS_LINE_COLS`)
- Modify: `config.toml`(`[rs_line]` 新键 + `[futu.groups]` 两条映射)
- Test: `tests/test_hk_rs.py`

**Interfaces:**
- Consumes: `_extract_rs_line(combined)` 已有逻辑(把 RS-line 列从 cloud CSV 抽出,按 rs_below_ma 非 NaN dropna)。
- Produces: `rs_line_tbl`(`build_hk_rs_tables` 返回的第 3 个元素)在 cloud CSV 含 `rs_pct_off_high` 时一并携带该列。US 侧无需改动 —— `build_3m_table` 直接 `read_csv` 整表透传所有列。

- [ ] **Step 1: 写失败测试**

在 `tests/test_hk_rs.py` 末尾追加(若文件已 import pandas 则复用):

```python
def test_extract_rs_line_includes_pct_off_high():
    import pandas as pd
    from hk_rs import _extract_rs_line
    combined = pd.DataFrame(
        {
            "rs_percentile_12m": [85.0, 40.0],
            "rs_below_ma": [0, 1],
            "rs_days_below_ma": [0, 5],
            "rs_frac_below_ma": [0.0, 0.8],
            "rs_pct_off_high": [0.004, 0.12],
        },
        index=pd.Index(["HK.00001", "HK.00002"], name="code"),
    )
    out = _extract_rs_line(combined)
    assert "rs_pct_off_high" in out.columns
    assert float(out.loc["HK.00001", "rs_pct_off_high"]) == 0.004
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_hk_rs.py -k pct_off_high -v`
Expected: FAIL —— `KeyError` / `assert "rs_pct_off_high" in out.columns`(列被 `_RS_LINE_COLS` 过滤掉)

- [ ] **Step 3: 写最小实现**

`hk_rs.py:217` 把:

```python
_RS_LINE_COLS = ["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"]
```

改为:

```python
_RS_LINE_COLS = ["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma", "rs_pct_off_high"]
```

(`_extract_rs_line` 用 `present = [c for c in _RS_LINE_COLS if c in combined.columns]`,新列存在即透传;它不在 `.astype(int)` 列表里,保持 float。dropna 仍只看 rs_below_ma,符合两列同源共存。)

`config.toml` `[rs_line]` 段(约 `config.toml:64`,在 `min_history` / TUNABLE 区之后、注释块之前)加入:

```toml
# RS New High 强势子清单: 从当日长线侧 survivors 里再筛 RS line 贴近 6 个月高点的票。
# 云端发布连续列 rs_pct_off_high (= RS line 距全历史最高值的百分比, 0=当日新高);
# 本地按下面阈值正向精选, 输出 RSNewHigh.txt (独立组, 非 append-only, 不进 dedup master)。
nh_enabled     = true
nh_tolerance   = 0.02   # TUNE ⭐ rs_pct_off_high ≤ 此值 → 进 RSNewHigh。起步 2%, 按 EOD 分布日志校准
nh_min_history = 42     # 不足 → unknown, 不纳入
```

`config.toml` `[futu.groups]` 段,美股区在 `leaders = "Leaders"`(约 `config.toml:308`)后加:

```toml
rs_new_high = "RSNewHigh"
```

港股区在 `hk_morning_gap = "HKMorningGap"`(约 `config.toml:321`)后加:

```toml
hk_rs_new_high = "HKRSNewHigh"
```

（**不**改 `append_only_groups`。）

- [ ] **Step 4: 跑测试确认通过 + config 可解析**

Run: `uv run python -m pytest tests/test_hk_rs.py -k pct_off_high -v`
Expected: PASS

Run: `uv run python -c "import tomllib; c=tomllib.load(open('config.toml','rb')); print(c['rs_line']['nh_tolerance'], c['futu']['groups']['rs_new_high'], c['futu']['groups']['hk_rs_new_high'])"`
Expected: `0.02 RSNewHigh HKRSNewHigh`

- [ ] **Step 5: 提交**

```bash
git add hk_rs.py config.toml tests/test_hk_rs.py
git commit -m "feat(rs-line): pass rs_pct_off_high through HK frame; add nh config + futu groups

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 云端脚本 join `rs_pct_off_high`

**Files:**
- Modify: `scripts/compute_us_rs_3m_cloud.py:30,107-110`
- Modify: `scripts/compute_hk_rs_cloud.py:35,139-142`

**Interfaces:**
- Consumes: `compute_rs_new_high`, `new_high_params_from_config`, `is_enabled`(Task 1/2)。
- Produces: 发布的 `data/{us_rs_3m,hk_rs}/<date>.csv` 多出 `rs_pct_off_high` 列。

- [ ] **Step 1: US 云端脚本接线**

`scripts/compute_us_rs_3m_cloud.py:30` 把:

```python
from rs_line import compute_rs_line_features, is_enabled, params_from_config  # noqa: E402
```

改为:

```python
from rs_line import (  # noqa: E402
    compute_rs_line_features,
    compute_rs_new_high,
    is_enabled,
    new_high_params_from_config,
    params_from_config,
)
```

在现有 RS-line features 的 `if` 块内(`compute_us_rs_3m_cloud.py:107-110`),`table = table.join(feats, how="left")` 之后追加:

```python
        nh = compute_rs_new_high(klines, spy_kline, **new_high_params_from_config(_cfg))
        table = table.join(nh, how="left")
        logger.info(f"[Cloud RS 3M] RS-new-high column merged for {len(nh)} tickers")
```

- [ ] **Step 2: HK 云端脚本接线**

`scripts/compute_hk_rs_cloud.py:35` 把:

```python
from rs_line import compute_rs_line_features, is_enabled, params_from_config  # noqa: E402
```

改为:

```python
from rs_line import (  # noqa: E402
    compute_rs_line_features,
    compute_rs_new_high,
    is_enabled,
    new_high_params_from_config,
    params_from_config,
)
```

在现有 RS-line features 的 `if` 块内(`compute_hk_rs_cloud.py:139-142`),`combined = combined.join(feats, how="left")` 之后追加:

```python
        nh = compute_rs_new_high(klines, hsi_kline, **new_high_params_from_config(_cfg))
        combined = combined.join(nh, how="left")
        logger.info(f"[Cloud HK RS] RS-new-high column merged for {len(nh)} codes")
```

- [ ] **Step 3: 跑相关云端测试 + import 检查**

Run: `uv run python -m pytest tests/test_compute_us_rs_3m_cloud.py tests/test_compute_hk_rs_cloud.py -v`
Expected: PASS(若这些测试 mock 了 fetch;若某用例断言列集合,按新增列更新断言)

Run: `uv run python -c "import ast; [ast.parse(open(p).read()) for p in ('scripts/compute_us_rs_3m_cloud.py','scripts/compute_hk_rs_cloud.py')]; print('ok')"`
Expected: `ok`

- [ ] **Step 4: 提交**

```bash
git add scripts/compute_us_rs_3m_cloud.py scripts/compute_hk_rs_cloud.py
git commit -m "feat(rs-line): publish rs_pct_off_high column from US/HK cloud RS scripts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: US 本地集成(`main.py`)

**Files:**
- Modify: `main.py:2004`(在 RS-line 标注块之后、`# --- Write IPO list ---` 之前插入 RS-NH 区块)

**Interfaces:**
- Consumes: `rs_line.select_rs_new_high` / `nh_is_enabled` / `nh_tolerance_from_config` / `format_rs_new_high_summary`;现有 `written_longs`(dict[str,list[str]])、`sorted_leaders`(list[str],仅当 `config.get("leaders")`)、`rs_table_3m`(DataFrame|None,index=ticker)、`write_watchlist`、`_write_webull`、`_futu_sync`、`_tv_sync`、`us_output_dir`、`today`、`output_dir`、`fmt`、`config`。
- Produces: `output/TV/US/<today>_RSNewHigh.txt`(+ Webull 镜像 + Futu/TV 同步,组键 `rs_new_high`)。

- [ ] **Step 1: 插入 RS-NH 区块**

在 `main.py:2004`(`logger.info(f"[RS] {_s}")` 所在 `if rs_ran:` 块结束)之后、`# --- Write IPO list ---`(`main.py:2006`)之前插入:

```python
        # --- RS New High strong sub-list (US) ---
        # Positive highlight filter: of today's long-side survivors, keep those
        # whose RS line sits within nh_tolerance of its 6-month high (cloud
        # column rs_pct_off_high). Pure subset of already-deduped output → no
        # own cross-day master; re-detected daily like RS/Shorts. Unknown
        # (missing column/ticker, short history) is EXCLUDED, not kept.
        if rs_line.nh_is_enabled(config):
            nh_candidates = set()
            for _t in written_longs.values():
                nh_candidates.update(_t)
            if config.get("leaders"):
                nh_candidates.update(sorted_leaders)
            nh_tol = rs_line.nh_tolerance_from_config(config)
            nh_selected, nh_stats = rs_line.select_rs_new_high(
                sorted(nh_candidates), rs_table_3m, nh_tol
            )
            dated = us_output_dir / f"{today}_RSNewHigh.txt"
            write_watchlist(nh_selected, dated, fmt)
            logger.info(
                f"[RS-NH] {rs_line.format_rs_new_high_summary(nh_stats)} "
                f"(tol={nh_tol:.0%}) -> {dated}"
            )
            _write_webull(nh_selected, dated, output_dir)
            _futu_sync(config, "rs_new_high", nh_selected, "US")
            _tv_sync(config, "rs_new_high", nh_selected, "US")
```

(`import rs_line` 已在上方 `main.py:1992` 执行,作用域内可直接用。)

- [ ] **Step 2: smoke 验证(无网络副作用)**

Run: `uv run python -c "import ast; ast.parse(open('main.py').read()); print('parse ok')"`
Expected: `parse ok`

Run: `uv run python -m pytest tests/ -k "rs_new_high or rs_line" -v`
Expected: PASS(回归无破坏)

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat(us-eod): emit RSNewHigh sub-list from long-side survivors

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: HK 本地集成(`hk_eod.py`)

**Files:**
- Modify: `hk_eod.py:830`(新增 `_tv_to_code` 逆映射 helper,紧跟 `_to_tv`)
- Modify: `hk_eod.py:1077`(写长线侧循环之后、`# --- HK IPO sidecar ---` 之前插入 RS-NH 区块)
- Test: `tests/test_hk_eod.py`(或新建 `tests/test_hk_rs_new_high.py`)—— `_tv_to_code` 往返

**Interfaces:**
- Consumes: `rs_line.select_rs_new_high` 等(同 Task 5);现有 `final`(dict[str,list[str]],值为 TV 格式)、`rs_line_tbl`(DataFrame|None,index=Futu code `HK.00xxx`,Task 3 后含 `rs_pct_off_high`)、`write_watchlist`、`write_webull`、`futu_sync`、`tv_sync`、`hk_output_dir`、`today_iso`、`output_dir`、`fmt`、`config`、`_to_tv`。
- Produces: `_tv_to_code(tv: str) -> str`(`HKEX:700` → `HK.00700`);`output/TV/HK/<today_iso>_HKRSNewHigh.txt`(组键 `hk_rs_new_high`)。

- [ ] **Step 1: 写 `_tv_to_code` 失败测试**

新建 `tests/test_hk_rs_new_high.py`:

```python
from hk_eod import _to_tv, _tv_to_code


def test_tv_to_code_roundtrip():
    for code in ["HK.00700", "HK.00001", "HK.00148"]:
        assert _tv_to_code(_to_tv(code)) == code


def test_tv_to_code_explicit():
    assert _tv_to_code("HKEX:700") == "HK.00700"
    assert _tv_to_code("HKEX:1") == "HK.00001"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_hk_rs_new_high.py -v`
Expected: FAIL —— `ImportError: cannot import name '_tv_to_code'`

- [ ] **Step 3: 写 `_tv_to_code`**

在 `hk_eod.py` 的 `_to_tv` 函数之后(约 `hk_eod.py:837`)插入:

```python
def _tv_to_code(tv: str) -> str:
    """``HKEX:700`` → ``HK.00700`` — inverse of ``_to_tv``, re-padding to the
    5-digit Futu code used as the RS-table index. ``HKEX:1`` → ``HK.00001``."""
    num = tv.replace("HKEX:", "", 1)
    return "HK." + num.zfill(5)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_hk_rs_new_high.py -v`
Expected: PASS

- [ ] **Step 5: 插入 HK RS-NH 区块**

在 `hk_eod.py:1077`(写长线侧 + Futu 同步的 `for name, tv in final.items():` 循环结束)之后、`# --- HK IPO sidecar ---`(`hk_eod.py:1079`)之前插入。需在 `hk_eod.py` 顶部已有 `import rs_line`;若无则在区块内 `import rs_line`(局部 import 对齐 main.py 风格):

```python
    # --- RS New High strong sub-list (HK) ---
    # Mirror of the US RS-NH block: of today's long-side survivors (the 4
    # first-sighting groups + Leaders; conditional RS excluded, same as US),
    # keep those whose RS line is within nh_tolerance of its 6-month high.
    # rs_line_tbl is indexed by Futu code, so map the TV survivors back to
    # codes for the lookup, then convert the selected codes to TV for output.
    import rs_line
    if rs_line.nh_is_enabled(config):
        nh_long_groups = ["EarningsGap", "HighVolume", "GapUp", "Leaders"]
        nh_candidate_codes = sorted({
            _tv_to_code(tv)
            for name in nh_long_groups
            for tv in final.get(name, [])
        })
        nh_tol = rs_line.nh_tolerance_from_config(config)
        nh_codes, nh_stats = rs_line.select_rs_new_high(
            nh_candidate_codes, rs_line_tbl, nh_tol
        )
        nh_tv = sorted(_to_tv(c) for c in nh_codes)
        dated_nh = hk_output_dir / f"{today_iso}_HKRSNewHigh.txt"
        write_watchlist(nh_tv, dated_nh, fmt)
        logger.info(
            f"[HK RS-NH] {rs_line.format_rs_new_high_summary(nh_stats)} "
            f"(tol={nh_tol:.0%}) -> {dated_nh}"
        )
        write_webull(nh_tv, dated_nh, output_dir)
        futu_sync(config, "hk_rs_new_high", nh_tv, "HK")
        tv_sync(config, "hk_rs_new_high", nh_tv, "HK")
```

- [ ] **Step 6: smoke 验证**

Run: `uv run python -c "import ast; ast.parse(open('hk_eod.py').read()); print('parse ok')"`
Expected: `parse ok`

Run: `uv run python -m pytest tests/ -v`
Expected: PASS(全套回归)

- [ ] **Step 7: 提交**

```bash
git add hk_eod.py tests/test_hk_rs_new_high.py
git commit -m "feat(hk-eod): emit HKRSNewHigh sub-list from HK long-side survivors

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 完成后

- ⚠️ **手动一次性**:在 Futu 客户端手建 `RSNewHigh`、`HKRSNewHigh` 两个自定义组(API 建不了组),否则那两条 Futu 同步是 no-op(软失败,只 warning,不影响 `.txt`)。
- 云端 CSV 需重新发布一次才会带 `rs_pct_off_high` 列;在那之前本地 RS-NH 当日为空(全 unknown),日志会显示 `unknown: N`。
- 观察几个 EOD 的 `[RS-NH] ... (<=1%/<=2%/<=5%)` 分布日志,据此把 `config.toml` 的 `nh_tolerance` 校准到舒服值。
- 更新 `CLAUDE.md`(RS gating 段)记录 RS-NH 子清单 + 两个新 Futu 组 —— 可作为收尾 PR 的一部分,非本计划任务。
```
