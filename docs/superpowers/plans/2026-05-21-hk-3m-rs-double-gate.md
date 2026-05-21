# HK 3M RS 双闸门 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 HK 长线流水线现有 12 月 RS 闸门之上叠加 3 月 RS 闸门（AND 串联），过滤掉「12 月数据未掉但近期已掉头」的旧 leader。

**Architecture:** 在 `hk_rs.py` 里抽出 weights 常量并重构 `_score_from_kline` / `compute_rs_table` / `cache_path / save_cache / load_cache` 接受 weights/suffix 参数；`hk_eod.py` 在同一次 HSI fetch 下计算 12M + 3M 两张 RS 表，分别落盘到 `output/state/hk_rs_rating_<date>.csv` 与 `hk_rs_rating_3m_<date>.csv`，并把 `filter_by_rs` 串联两次。配置侧新增 `[hk_settings] min_rs_percentile_longs_3m = 90`（设 0 关闸）。下游 `cleanup.py` 加新 CSV 的 2 日保留规则；`report/__main__.py` 的精确文件名候选列表天然不会误读 3M 表，加测试验证。

**Tech Stack:** Python 3.13, pandas, yfinance（HSI k 线复用），pytest，TOML 配置。

**Spec:** `docs/superpowers/specs/2026-05-21-hk-3m-rs-double-gate-design.md`

---

## File Map

| 文件 | 责任 | 改动类型 |
|------|------|---------|
| `hk_rs.py` | RS 计算 + 缓存读写；新增 weights/label/suffix 入参 | Modify |
| `hk_eod.py` | 调用 RS 模块算两张表，串联两道闸门，三段计数 log | Modify (~870-885 段) |
| `config.toml` | 新增 `min_rs_percentile_longs_3m` 键 | Modify |
| `cleanup.py` | 加 3M CSV 保留规则 | Modify |
| `tests/test_hk_rs.py` | 覆盖 weights/suffix 新路径 | Modify |
| `tests/test_cleanup.py` | 3M CSV 2 日保留断言 | Modify |
| `tests/test_report_main.py` | 日报不会误读 3M CSV 的断言 | Modify |
| `CLAUDE.md` | HK RS 章节加双闸描述 | Modify |

---

## Task 1: 在 `hk_rs.py` 中抽出 WEIGHTS_12M / WEIGHTS_3M 常量并让 `_score_from_kline` 接受 weights

**Files:**
- Modify: `/Users/xue/finviz_to_tv/hk_rs.py:19-45`
- Test: `/Users/xue/finviz_to_tv/tests/test_hk_rs.py`

- [ ] **Step 1: 写两条失败测试 — (a) 默认 weights 行为不变；(b) 用 3M weights 时仅需 ~64 行历史**

把以下追加到 `tests/test_hk_rs.py` 末尾：

```python
from hk_rs import WEIGHTS_12M, WEIGHTS_3M, _score_from_kline


def test_score_from_kline_default_weights_unchanged():
    # 260 行平盘 + 末尾跳 +20% → 12M 分数应等于 0.4·0.2 + 0.2·0.2 + 0.2·0.2 + 0.2·0.2 = 0.20
    df = _flat_then_jump(100.0, jump_pct=20, n=260)
    score, reason = _score_from_kline(df)  # 默认 WEIGHTS_12M
    assert reason == "ok"
    assert abs(score - 0.20) < 1e-9


def test_score_from_kline_3m_weights_short_history_ok():
    # 70 行平盘 + 末尾跳 +10% → 3M 算法仅需 max(3)*21 + 1 = 64 行
    # 分数 = 0.5·0.1 + 0.3·0.1 + 0.2·0.1 = 0.10
    df = _flat_then_jump(100.0, jump_pct=10, n=70)
    score, reason = _score_from_kline(df, weights=WEIGHTS_3M)
    assert reason == "ok"
    assert abs(score - 0.10) < 1e-9


def test_score_from_kline_3m_weights_reject_when_below_min_rows():
    # 60 行 < 64 → short_history
    df = _flat_then_jump(100.0, jump_pct=10, n=60)
    score, reason = _score_from_kline(df, weights=WEIGHTS_3M)
    assert score is None
    assert reason == "short_history"
```

- [ ] **Step 2: 跑测试，确认 3 条都失败**

Run: `uv run pytest tests/test_hk_rs.py -v`
Expected: 3 条新测试 FAIL（ImportError on `WEIGHTS_12M / WEIGHTS_3M`，或 `_score_from_kline` 不接受 `weights` 参数）

- [ ] **Step 3: 重构 `hk_rs.py:19-45` — 提取常量，让 `_score_from_kline` 接受 weights，最小行数从权重推导**

把 `hk_rs.py` 第 19-45 行（`_score_from_kline` 函数）替换为：

```python
# Weight tuples: (months, weight). months 决定回看偏移 (months * 21 个交易日)；
# weight 按 IBD 风格压在最短窗口上，重叠窗口产生隐式时间衰减。
WEIGHTS_12M: list[tuple[int, float]] = [(3, 0.4), (6, 0.2), (9, 0.2), (12, 0.2)]
WEIGHTS_3M:  list[tuple[int, float]] = [(1, 0.5), (2, 0.3), (3, 0.2)]


def _score_from_kline(
    df: pd.DataFrame,
    weights: list[tuple[int, float]] = WEIGHTS_12M,
) -> tuple[float | None, str]:
    """Compute Σ wᵢ·Rᵢ from a k-line DataFrame sorted ascending by ``time_key``.
    Returns ``(score, reason)`` — score is None on rejection. Reason is one of:
      ``ok``, ``no_data``, ``short_history`` (insufficient rows for max lookback),
      ``zero_last``, ``zero_past``.

    Minimum rows = ``max(months for months, _ in weights) * 21 + 1``.
    """
    if df is None or df.empty:
        return None, "no_data"
    max_months = max(m for m, _ in weights)
    min_rows = max_months * 21 + 1
    if len(df) < min_rows:
        return None, "short_history"
    closes = df["close"].astype(float).values
    last = closes[-1]
    if last <= 0:
        return None, "zero_last"

    score = 0.0
    for months, w in weights:
        idx = -1 - months * 21
        if abs(idx) > len(closes):
            return None, "short_history"
        past = closes[idx]
        if past <= 0:
            return None, "zero_past"
        score += w * ((last / past) - 1.0)
    return score, "ok"
```

- [ ] **Step 4: 跑测试确认 3 条新测试 + 原有 3 条都过**

Run: `uv run pytest tests/test_hk_rs.py -v`
Expected: 6 PASSED（3 原有 + 3 新增）

- [ ] **Step 5: Commit**

```bash
git add hk_rs.py tests/test_hk_rs.py
git commit -m "$(cat <<'EOF'
refactor(hk_rs): extract WEIGHTS_12M/3M constants, parameterize _score_from_kline

Step 1/4 of HK 3M RS double-gate rollout. Pure refactor — default
behavior unchanged; opens the door for the 3M-weight code path landing
in subsequent commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 让 `compute_rs_table` 接受 weights 与 label

**Files:**
- Modify: `/Users/xue/finviz_to_tv/hk_rs.py:48-89`
- Test: `/Users/xue/finviz_to_tv/tests/test_hk_rs.py`

- [ ] **Step 1: 写失败测试 — 用 3M weights 调 compute_rs_table 在 70 行历史下也能算分**

追加到 `tests/test_hk_rs.py` 末尾：

```python
def test_compute_rs_table_with_3m_weights_uses_shorter_history():
    # 5 个 ticker 各有 70 行历史 (< 253，12M 会全部 short_history rejected)，
    # 但 3M 仅需 64 行 → 全部可打分。
    klines = {
        f"HK.000{i:02d}": _flat_then_jump(100.0, jump_pct=5 + i * 2, n=70)
        for i in range(1, 6)
    }
    hsi = _flat_then_jump(20000.0, jump_pct=0, n=70)
    table = compute_rs_table(klines, hsi, weights=WEIGHTS_3M, label="3M")

    assert set(table.index) == set(klines.keys())
    assert table["rs_percentile"].between(0, 99).all()
    # 跳幅最大的应在最高百分位
    assert table["rs_percentile"].idxmax() == "HK.00005"


def test_compute_rs_table_with_3m_weights_logs_label(caplog):
    klines = {"HK.00001": _flat_then_jump(100.0, jump_pct=5, n=70)}
    hsi = _flat_then_jump(20000.0, jump_pct=0, n=70)
    with caplog.at_level("INFO"):
        compute_rs_table(klines, hsi, weights=WEIGHTS_3M, label="3M")
    assert any("[HK RS 3M]" in r.message for r in caplog.records)
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `uv run pytest tests/test_hk_rs.py::test_compute_rs_table_with_3m_weights_uses_shorter_history tests/test_hk_rs.py::test_compute_rs_table_with_3m_weights_logs_label -v`
Expected: FAIL（`compute_rs_table` 不接受 `weights` / `label` kwarg）

- [ ] **Step 3: 改 `hk_rs.py:48-89` — `compute_rs_table` 接受 weights + label，log 行带 label**

把 `compute_rs_table` 函数体替换为：

```python
def compute_rs_table(
    klines: dict[str, pd.DataFrame],
    hsi_kline: pd.DataFrame,
    weights: list[tuple[int, float]] = WEIGHTS_12M,
    label: str = "12M",
) -> pd.DataFrame:
    """Return DataFrame indexed by Futu code with column ``rs_percentile``
    (0-99). Tickers without enough history are excluded.

    ``weights`` selects the weight tuple (WEIGHTS_12M or WEIGHTS_3M). ``label``
    is purely cosmetic — it's spliced into the rejection-breakdown log line so
    a single run computing both 12M and 3M tables produces distinguishable
    output.

    Logs a per-reason rejection breakdown so the operator can tell whether
    a small RS table is due to (a) Futu/yfinance not returning enough k-line
    history for less liquid HK names (``short_history``) versus (b) data
    hygiene issues (``zero_last`` / ``zero_past``).
    """
    hsi_score, hsi_reason = _score_from_kline(hsi_kline, weights=weights)
    if hsi_score is None:
        logger.warning(
            f"[HK RS {label}] HSI score rejected ({hsi_reason}) — falling back to "
            f"absolute scores (effectively un-relativised)."
        )
        hsi_score = 0.0

    scores: dict[str, float] = {}
    reasons: dict[str, int] = {}
    for code, df in klines.items():
        s, reason = _score_from_kline(df, weights=weights)
        reasons[reason] = reasons.get(reason, 0) + 1
        if s is None:
            continue
        scores[code] = s - hsi_score

    logger.info(
        f"[HK RS {label}] computed: {len(scores)}/{len(klines)} klines scored. "
        f"Reason breakdown: {dict(sorted(reasons.items(), key=lambda x: -x[1]))}"
    )

    if not scores:
        return pd.DataFrame(columns=["rs_percentile"])

    series = pd.Series(scores, name="relative_score")
    pct = series.rank(method="average", pct=True) * 99
    return pd.DataFrame({"rs_percentile": pct.round().astype(int)})
```

- [ ] **Step 4: 跑全部测试**

Run: `uv run pytest tests/test_hk_rs.py -v`
Expected: 8 PASSED（5 现有 + 之前任务 3 个 + 本任务 2 个 = 8；其中 `test_compute_rs_table_relative_to_hsi` 必须仍然过——它走默认 weights 路径）

- [ ] **Step 5: Commit**

```bash
git add hk_rs.py tests/test_hk_rs.py
git commit -m "$(cat <<'EOF'
feat(hk_rs): parameterize compute_rs_table with weights + label

Allows the same code path to compute both 12M and 3M RS tables in a
single HK EOD run. Label is spliced into the log lines for diagnostic
distinguishability.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: cache_path / save_cache / load_cache 加 suffix 参数

**Files:**
- Modify: `/Users/xue/finviz_to_tv/hk_rs.py:113-131`
- Test: `/Users/xue/finviz_to_tv/tests/test_hk_rs.py`

- [ ] **Step 1: 写失败测试 — suffix='3m' 产生 hk_rs_rating_3m_<date>.csv**

追加到 `tests/test_hk_rs.py` 末尾：

```python
from datetime import date as _date

from hk_rs import cache_path, save_cache, load_cache


def test_cache_path_default_suffix_unchanged(tmp_path):
    p = cache_path(_date(2026, 5, 21), tmp_path)
    assert p == tmp_path / "state" / "hk_rs_rating_2026-05-21.csv"


def test_cache_path_with_3m_suffix(tmp_path):
    p = cache_path(_date(2026, 5, 21), tmp_path, suffix="3m")
    assert p == tmp_path / "state" / "hk_rs_rating_3m_2026-05-21.csv"


def test_save_and_load_3m_cache_roundtrip(tmp_path):
    df = pd.DataFrame({"rs_percentile": [95, 50]}, index=["HK.00001", "HK.00002"])
    df.index.name = "code"
    save_cache(df, _date(2026, 5, 21), tmp_path, suffix="3m")

    loaded = load_cache(_date(2026, 5, 21), tmp_path, suffix="3m")
    assert loaded is not None
    assert list(loaded.index) == ["HK.00001", "HK.00002"]
    assert loaded.loc["HK.00001", "rs_percentile"] == 95

    # 默认 suffix 路径不应找到这个文件
    assert load_cache(_date(2026, 5, 21), tmp_path) is None
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `uv run pytest tests/test_hk_rs.py::test_cache_path_default_suffix_unchanged tests/test_hk_rs.py::test_cache_path_with_3m_suffix tests/test_hk_rs.py::test_save_and_load_3m_cache_roundtrip -v`
Expected: FAIL（三个函数都不接受 `suffix` kwarg）

- [ ] **Step 3: 改 `hk_rs.py:113-131` — 三个函数加 `suffix=""` 参数**

把 `cache_path / save_cache / load_cache` 替换为：

```python
def cache_path(today: date, output_dir: Path, suffix: str = "") -> Path:
    """Default suffix '' → hk_rs_rating_<date>.csv (12M, legacy path).
    suffix='3m' → hk_rs_rating_3m_<date>.csv."""
    stem = f"hk_rs_rating_{('' + suffix + '_') if suffix else ''}{today.isoformat()}"
    return output_dir / "state" / f"{stem}.csv"


def save_cache(
    table: pd.DataFrame, today: date, output_dir: Path, suffix: str = ""
) -> None:
    p = cache_path(today, output_dir, suffix=suffix)
    p.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(p, index_label="code")


def load_cache(
    today: date, output_dir: Path, suffix: str = ""
) -> pd.DataFrame | None:
    p = cache_path(today, output_dir, suffix=suffix)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, index_col="code")
    except Exception:
        return None
```

- [ ] **Step 4: 跑全部测试**

Run: `uv run pytest tests/test_hk_rs.py -v`
Expected: 11 PASSED

- [ ] **Step 5: Commit**

```bash
git add hk_rs.py tests/test_hk_rs.py
git commit -m "$(cat <<'EOF'
feat(hk_rs): add suffix kwarg to cache_path/save_cache/load_cache

Default suffix '' keeps the legacy hk_rs_rating_<date>.csv path; suffix='3m'
yields hk_rs_rating_3m_<date>.csv. Lets the EOD pipeline persist 12M and
3M RS tables side-by-side without filename collision.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `hk_eod.py` 接入双闸门

**Files:**
- Modify: `/Users/xue/finviz_to_tv/hk_eod.py:830-884`

- [ ] **Step 1: 替换 RS section（831-884 行）— 同一次 HSI fetch 算两张表，串联 filter，三段计数 log**

把 `hk_eod.py` 第 831-884 行（从 `# --- RS table ---` 到 `# --- Within-day cross-strategy priority dedup ---` 之前的所有内容）替换为：

```python
    # --- RS tables (12M + 3M) ---
    # 双闸门: 先过 12M RS >= threshold, 再过 3M RS >= threshold_3m.
    # 两张表用同一次 HSI fetch 计算，分别落盘到 hk_rs_rating_<date>.csv
    # 与 hk_rs_rating_3m_<date>.csv。"missing -> passthrough" 策略两层都遵守。
    # 任一阈值设为 0 即关闭该层 (filter_by_rs 内部短路)。
    from hk_rs import (
        compute_rs_table, filter_by_rs, save_cache, load_cache,
        WEIGHTS_12M, WEIGHTS_3M,
    )
    today_d = date.today()
    rs_table_12m = load_cache(today_d, output_dir)
    rs_table_3m  = load_cache(today_d, output_dir, suffix="3m")
    need_compute = (rs_table_12m is None or rs_table_3m is None) and bool(klines)
    if need_compute:
        hsi_kline = fetch_hsi_kline_yf(period="2y")
        if hsi_kline is not None and not hsi_kline.empty:
            if use_yesterday:
                hsi_kline = hsi_kline[hsi_kline["time_key"].dt.date < today_d].reset_index(drop=True)
            if rs_table_12m is None:
                rs_table_12m = compute_rs_table(klines, hsi_kline, weights=WEIGHTS_12M, label="12M")
                save_cache(rs_table_12m, today_d, output_dir)
            if rs_table_3m is None:
                rs_table_3m = compute_rs_table(klines, hsi_kline, weights=WEIGHTS_3M, label="3M")
                save_cache(rs_table_3m, today_d, output_dir, suffix="3m")
        else:
            logger.warning("[HK Longs] HSI k-line fetch failed — RS gate disabled")

    # --- Apply per-strategy filters ---
    # The conditional RS group keys off HSI's "today" day-change. When
    # use_yesterday is True the live HSI snapshot reflects a state that
    # doesn't match the trimmed k-line data, so the trigger is meaningless
    # — skip the RS group in that case.
    rs_trigger = hk_settings.get("hsi_rs_trigger", -1.2)
    if use_yesterday:
        hsi_change = None
        rs_enabled = False
        logger.info(
            "[HK Longs] Pre-20:00 run uses yesterday's close — HSI conditional "
            "RS group skipped (live HSI day-change does not match trimmed bars)."
        )
    else:
        hsi_change = hsi_day_change_pct(host=host, port=port)
        rs_enabled = hsi_change is not None and hsi_change <= rs_trigger
        logger.info(
            f"[HK Longs] HSI day-change={hsi_change} (trigger {rs_trigger}); "
            f"RS group {'ENABLED' if rs_enabled else 'skipped'}"
        )

    raw = apply_strategy_filters(metrics, hk_settings, hk_longs, hk_leaders, rs_enabled)

    # --- RS double gate (12M ∩ 3M) ---
    threshold_12m = int(hk_settings.get("min_rs_percentile_longs", 90))
    threshold_3m  = int(hk_settings.get("min_rs_percentile_longs_3m", 90))
    pre_counts   = {n: len(c) for n, c in raw.items()}
    after_12m    = {n: filter_by_rs(c, rs_table_12m, threshold_12m) for n, c in raw.items()}
    after_3m     = {n: filter_by_rs(c, rs_table_3m,  threshold_3m)  for n, c in after_12m.items()}
    raw = after_3m
    logger.info(
        f"[HK Longs] RS 12m>={threshold_12m} ∩ 3m>={threshold_3m}: "
        + ", ".join(
            f"{n} {pre_counts[n]}→{len(after_12m[n])}→{len(after_3m[n])}"
            for n in HK_STRATEGY_PRIORITY
        )
    )
    post_rs_counts = {n: len(c) for n, c in raw.items()}
```

注意：替换后下面紧接着的 `# --- Within-day cross-strategy priority dedup ---` 段会用到 `post_rs_counts`，所以末尾保留赋值以维持现有 log 行兼容。

- [ ] **Step 2: 跑全部已有测试确认无回归**

Run: `uv run pytest tests/ -v`
Expected: 全部 PASS（hk_eod.py 没有直接被单元测试覆盖 RS 段，但 import 路径完整、`from hk_rs import ...` 应能正确解析）

- [ ] **Step 3: 静态 sanity check — 让 Python 编译这个文件**

Run: `uv run python -c "import hk_eod; print('ok')"`
Expected: 输出 `ok`，无 ImportError / SyntaxError

- [ ] **Step 4: Commit**

```bash
git add hk_eod.py
git commit -m "$(cat <<'EOF'
feat(hk_eod): apply 12M ∩ 3M RS double gate to long-side groups

Computes 12M and 3M RS tables from a single HSI fetch, persists both to
output/state/, and runs filter_by_rs twice in series. New 3M gate keyed
by [hk_settings] min_rs_percentile_longs_3m (defaults to 90). Three-segment
log line (pre → 12m → 3m) lets the operator quantify each gate's cut.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `config.toml` 新增 `min_rs_percentile_longs_3m`

**Files:**
- Modify: `/Users/xue/finviz_to_tv/config.toml:194`

- [ ] **Step 1: 在 `[hk_settings]` 表里第 194 行下方添加新键**

把 `config.toml` 第 194 行：
```toml
min_rs_percentile_longs = 90              # 与 HSI 比较，本地 IBD 算法 (top 10%)
```

替换为：
```toml
min_rs_percentile_longs    = 90           # 12月 RS 闸门 — 与 HSI 比较，本地 IBD 算法 (top 10%)
min_rs_percentile_longs_3m = 90           # 3月 RS 闸门 — 0.5·R21+0.3·R42+0.2·R63 vs HSI (top 10%)
```

- [ ] **Step 2: 跑测试确保 TOML 解析仍正常**

Run: `uv run python -c "import tomllib; tomllib.load(open('config.toml','rb')); print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: Commit**

```bash
git add config.toml
git commit -m "$(cat <<'EOF'
config: add hk_settings.min_rs_percentile_longs_3m = 90

Threshold for the second RS gate (3-month) in the HK long-side
pipeline. Setting to 0 disables this layer (behaviour falls back to
the single 12M gate); setting both 12M and 3M to 0 disables RS gating
entirely.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `cleanup.py` 加 3M CSV 保留规则 + 测试

**Files:**
- Modify: `/Users/xue/finviz_to_tv/cleanup.py:31-49`
- Modify: `/Users/xue/finviz_to_tv/tests/test_cleanup.py:56-80`

- [ ] **Step 1: 在 `test_rs_rating_uses_four_day_window` 里加 3M CSV 断言（失败测试先行）**

把 `tests/test_cleanup.py` 第 56-80 行（整个 `test_rs_rating_uses_four_day_window` 函数）替换为：

```python
def test_rs_rating_uses_four_day_window(output_tree: Path) -> None:
    # rs_rating_*.csv survives for 4 days; today is 2026-05-15, cutoff =
    # 2026-05-12, so 05_12..05_15 survive, 05_11 and earlier go.
    for d in ("2026_05_15", "2026_05_14", "2026_05_13",
              "2026_05_12", "2026_05_11", "2026_05_09"):
        _touch(output_tree / f"state/rs_rating_{d}.csv")

    # hk_rs_rating_*.csv (12M) and hk_rs_rating_3m_*.csv (3M) are both on
    # the standard 2-day rule. Today = 15, cutoff = 14.
    for d in ("2026-05-15", "2026-05-14", "2026-05-13", "2026-05-12"):
        _touch(output_tree / f"state/hk_rs_rating_{d}.csv")
        _touch(output_tree / f"state/hk_rs_rating_3m_{d}.csv")

    cleanup_old_outputs(output_tree, date(2026, 5, 15))

    assert (output_tree / "state/rs_rating_2026_05_15.csv").exists()
    assert (output_tree / "state/rs_rating_2026_05_14.csv").exists()
    assert (output_tree / "state/rs_rating_2026_05_13.csv").exists()
    assert (output_tree / "state/rs_rating_2026_05_12.csv").exists()
    assert not (output_tree / "state/rs_rating_2026_05_11.csv").exists()
    assert not (output_tree / "state/rs_rating_2026_05_09.csv").exists()

    assert (output_tree / "state/hk_rs_rating_2026-05-15.csv").exists()
    assert (output_tree / "state/hk_rs_rating_2026-05-14.csv").exists()
    assert not (output_tree / "state/hk_rs_rating_2026-05-13.csv").exists()
    assert not (output_tree / "state/hk_rs_rating_2026-05-12.csv").exists()

    assert (output_tree / "state/hk_rs_rating_3m_2026-05-15.csv").exists()
    assert (output_tree / "state/hk_rs_rating_3m_2026-05-14.csv").exists()
    assert not (output_tree / "state/hk_rs_rating_3m_2026-05-13.csv").exists()
    assert not (output_tree / "state/hk_rs_rating_3m_2026-05-12.csv").exists()
```

- [ ] **Step 2: 跑测试确认 3M CSV 的 4 条断言 FAIL（既有规则会把它们当未识别文件保留 → exists 断言对，not exists 断言错）**

Run: `uv run pytest tests/test_cleanup.py::test_rs_rating_uses_four_day_window -v`
Expected: AssertionError（`hk_rs_rating_3m_2026-05-13.csv` 不应该存在但仍存在；cleanup 未识别此模式）

- [ ] **Step 3: 在 `cleanup.py:43-44` 现有 `hk_rs_rating_` 规则**__**之上**__**插入 3M 规则**

把 `cleanup.py` 第 43-44 行：

```python
    _Rule("state", re.compile(rf"^hk_rs_rating_{_DATE_D}\.csv$"),
          "%Y-%m-%d", 2),
```

替换为：

```python
    # 3M variant first — its filename starts with hk_rs_rating_3m_, which
    # would otherwise be a no-match against the 12M regex below (the 12M
    # regex anchors on hk_rs_rating_<date>.csv with no '3m_' segment).
    _Rule("state", re.compile(rf"^hk_rs_rating_3m_{_DATE_D}\.csv$"),
          "%Y-%m-%d", 2),
    _Rule("state", re.compile(rf"^hk_rs_rating_{_DATE_D}\.csv$"),
          "%Y-%m-%d", 2),
```

- [ ] **Step 4: 跑测试确认全过**

Run: `uv run pytest tests/test_cleanup.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add cleanup.py tests/test_cleanup.py
git commit -m "$(cat <<'EOF'
chore(cleanup): add 2-day retention rule for hk_rs_rating_3m_*.csv

Matches the existing 12M cache retention. The 3M regex is ordered
before the 12M regex defensively; the two patterns are non-overlapping
either way, but explicit ordering is cheaper to verify than implicit
disjointness.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `tests/test_report_main.py` 加 3M CSV 不会被日报误读的断言

**Files:**
- Modify: `/Users/xue/finviz_to_tv/tests/test_report_main.py`

**Background:** `report/__main__.py:65-71` 的候选路径列表是精确文件名 (`hk_rs_rating_<date>.csv`)，不会匹配 `hk_rs_rating_3m_<date>.csv`。这是天然保证，但需要测试钉死，防止有人重构候选列表为 glob 时悄悄破坏。

- [ ] **Step 1: 追加测试到 `tests/test_report_main.py` 末尾**

```python
def test_load_rs_lookup_hk_ignores_3m_cache_when_only_3m_present(
    tmp_path: Path, monkeypatch
):
    """If only the 3M cache file is present (no 12M file), the HK lookup
    must NOT silently use the 3M values — it should return an all-None
    lookup. Defends against future regression where a refactor turns the
    candidate list into a glob."""
    state_dir = tmp_path / "output" / "state"
    state_dir.mkdir(parents=True)
    csv_3m = state_dir / "hk_rs_rating_3m_2026-05-07.csv"
    with csv_3m.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "percentile"])
        writer.writerow(["HK.00700", 92])
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    lookup = orch._load_rs_lookup("hk", "2026_05_07")
    # 12M 表不存在 → 即使 3M 表里有 0700，日报也不应读到它
    assert lookup("0700.HK") is None


def test_load_rs_lookup_hk_uses_12m_cache_when_both_present(
    tmp_path: Path, monkeypatch
):
    """12M and 3M caches coexist; lookup must read the 12M file."""
    state_dir = tmp_path / "output" / "state"
    state_dir.mkdir(parents=True)
    csv_12m = state_dir / "hk_rs_rating_2026-05-07.csv"
    with csv_12m.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "percentile"])
        writer.writerow(["HK.00700", 92])
    csv_3m = state_dir / "hk_rs_rating_3m_2026-05-07.csv"
    with csv_3m.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "percentile"])
        writer.writerow(["HK.00700", 77])  # 不同分数，确认读的是 12M
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    lookup = orch._load_rs_lookup("hk", "2026_05_07")
    assert lookup("0700.HK") == 92  # 12M 表的值，不是 3M 表的 77
```

- [ ] **Step 2: 跑测试**

Run: `uv run pytest tests/test_report_main.py -v`
Expected: 全部 PASS（包含 2 条新增）

- [ ] **Step 3: Commit**

```bash
git add tests/test_report_main.py
git commit -m "$(cat <<'EOF'
test(report): assert daily report ignores 3M RS cache file

The candidate-path list in report/__main__._load_rs_lookup is exact
filenames, so hk_rs_rating_3m_*.csv is naturally not picked up. These
tests pin that guarantee so a future refactor to globbing won't quietly
break it.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `CLAUDE.md` 文档更新

**Files:**
- Modify: `/Users/xue/finviz_to_tv/CLAUDE.md`（"HK: `hk_rs.py` (computed locally, vs HSI)" 段）

- [ ] **Step 1: 找到 CLAUDE.md 中 "HK: `hk_rs.py` (computed locally, vs HSI)" 段落，定位结尾处**

Run: `grep -n "hk_rs.py.*computed locally" /Users/xue/finviz_to_tv/CLAUDE.md`
Expected: 输出一行行号（约第 80-95 行附近的 H3 标题）

- [ ] **Step 2: 在该段落末尾（"HK Shorts is NOT RS-gated." 那句话之后）追加双闸说明**

把 CLAUDE.md 里这一段（"HK: `hk_rs.py`" 章节中包含 "HK Shorts is NOT RS-gated." 的最后一句）替换为：

```markdown
The Fred6725 CSV is US-only, so HK long-side groups use a **separate local RS computation**. Same algorithm (`RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12`) but the benchmark is HSI (`^HSI` via yfinance) instead of SPY, and the percentile is ranked across the HK Main Board universe (~2,400 tickers) instead of US. Computed in-process from the same yfinance k-line batch already pulled for the metrics frame, so there's no separate fetch step. Cached to `output/state/hk_rs_rating_<date>.csv`. **HK long-side gates with a double RS filter (12M ∩ 3M)** — see below. HK Shorts is NOT RS-gated.

**Double gate (added 2026-05-21):** All 5 HK long-side groups pass through both `min_rs_percentile_longs = 90` (12-month RS, `WEIGHTS_12M = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12`) **and** `min_rs_percentile_longs_3m = 90` (3-month RS, `WEIGHTS_3M = 0.5·P1 + 0.3·P2 + 0.2·P3`). The 3M table is computed in the same `compute_rs_table` call but with `weights=WEIGHTS_3M, label="3M"` and cached to `output/state/hk_rs_rating_3m_<date>.csv`. Semantically: "old leader still leading". Either threshold can be set to 0 in `[hk_settings]` to disable that layer independently — `min_rs_percentile_longs_3m = 0` retreats to single-gate 12M behaviour; both at 0 disables RS gating entirely (still computes & caches the tables; just doesn't filter). The 3M table requires only ~64 days of history (vs 253 for 12M), so it scores a much larger universe — `filter_by_rs` "missing → passthrough" policy applies to both layers as before. Log line format: `RS 12m>=N ∩ 3m>=M: <group> <pre>→<after_12m>→<after_3m>` for each of the 5 long-side groups.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(CLAUDE): document HK long-side RS double gate (12M ∩ 3M)

Adds 'Double gate (added 2026-05-21)' subsection under the HK hk_rs.py
heading. Covers weights, config keys, cache paths, semantics, disable
mechanism, and log line format.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 全套测试 + 手工 smoke 验证

**Files:** (验证步骤；无文件修改)

- [ ] **Step 1: 跑全部单元测试一次最终确认无回归**

Run: `uv run pytest tests/ -v`
Expected: 全绿。新增项至少为：5 (test_hk_rs.py) + 2 (test_cleanup.py 现有用例 + 新断言已合并) + 2 (test_report_main.py) ≈ 9 新断言/用例。

- [ ] **Step 2: 手工跑一次 HK EOD（盘后或非交易时段都会走 use_yesterday 路径）**

Run: `uv run main.py --mode hk-eod 2>&1 | tail -80`
Expected:
- 不报错完成
- log 里能看到两行 `[HK RS 12M] computed: ...` 和 `[HK RS 3M] computed: ...`
- log 里能看到 `[HK Longs] RS 12m>=90 ∩ 3m>=90: EarningsGap A→B→C, HighVolume ..., GapUp ..., Leaders ..., RS ...`（3M 列 ≤ 12M 列 ≤ pre 列）
- `output/state/` 下两个 CSV 都生成：
  - `hk_rs_rating_2026-05-21.csv`
  - `hk_rs_rating_3m_2026-05-21.csv`
- `output/TV/HK/<date>_*.txt` 都生成（可能为空，但文件必须存在）

- [ ] **Step 3: 再跑一次确认 cache 命中**

Run: `uv run main.py --mode hk-eod 2>&1 | grep -E "(load_cache|computed|RS 12m>=)" | head -10`
Expected: 不出现 `[HK RS 12M] computed:` / `[HK RS 3M] computed:` 这两行（它们只在 compute_rs_table 实际跑时打印），但出现 `RS 12m>=90 ∩ 3m>=90: ...` 计数行。证明 load_cache 命中、跳过重算、闸门仍生效。

- [ ] **Step 4: 检查 cleanup 是否能正确处理新文件**

Run: `ls output/state/hk_rs_rating*.csv`
Expected: 至少包含今天的 12M 和 3M 两个 CSV；本步不验证保留窗口（要等隔天才能验证 cleanup 真删了昨天前的 3M 文件），但单元测试已经钉死了行为，足够安全。

- [ ] **Step 5: 全任务结束的最终 commit（如果上面 smoke 没有需要修的东西，本步跳过）**

如果 smoke 验证暴露了任何问题（例如 log 行格式不对、CSV 文件名错位），现在修了并提交。否则 plan 完成。

---

## Spec → Plan Coverage Map

| Spec 章节 | 覆盖 Task |
|----------|----------|
| 算法：WEIGHTS_3M 公式与最小历史 | Task 1 |
| 算法：百分位排名 + 重叠窗口 | Task 2（compute_rs_table 重构） |
| 闸门串联：filter_by_rs 二次调用 + 三段 log | Task 4 |
| 缺失即 passthrough（两层） | Task 4 + `test_filter_by_rs_passthrough_for_missing` 已存在 |
| 应用范围：5 个长线组（含 RS） | Task 4（对 `raw` 字典所有键应用） |
| 不应用：IPO sidecar | Task 4（IPO 段在更下方，本任务不动它） |
| 配置：`min_rs_percentile_longs_3m = 90` | Task 5 |
| 调优指南（设 0 关闸） | Task 4 阈值代码 + Task 8 文档 |
| 缓存：`hk_rs_rating_3m_<date>.csv` | Task 3 + Task 4 |
| `compute_rs_table` 调用 2 次零额外 IO | Task 4（同一 hsi_kline 复用） |
| cleanup 保留规则 | Task 6 |
| 日报不误读 3M 表 | Task 7 |
| CLAUDE.md HK RS 章节 | Task 8 |
| Failure modes | Task 4 代码处理 + Task 9 smoke 验证 |
| Risk: 候选池缩水 | Task 4 三段 log + Task 9 smoke 观察 |
