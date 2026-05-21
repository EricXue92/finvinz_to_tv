# HK IPO 收紧 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `hk_eod.py` 的 HK IPO sidecar 上加两道闸门——硬性 `len(df) >= 20` 最小历史 + 64+ 天时 `3M RS >= min_rs_percentile_longs_3m`——同时把 inline 过滤段抽成 `filter_hk_ipo_candidates` 纯函数以便单测。

**Architecture:** 新增模块级函数 `filter_hk_ipo_candidates(klines, metrics, rs_table_3m, hk_settings) -> (list[str], dict[str, int])`，放置在 `dedup_by_priority` 之后（~705 行），完整覆盖现有阶梯 + 两道新闸门。`run_hk_eod` 中的 inline IPO 段（984-1016 行）改为单次调用。复用 `min_rs_percentile_longs_3m` 配置键，零新增 config。

**Tech Stack:** Python 3.13, pandas, pytest。新函数复用 hk_rs 的 RS 表（已落盘到 `hk_rs_rating_3m_<date>.csv`）。

**Spec:** `docs/superpowers/specs/2026-05-21-hk-ipo-tighten-design.md`

---

## File Map

| 文件 | 责任 | 改动类型 |
|------|------|---------|
| `hk_eod.py` | 新增 `filter_hk_ipo_candidates` 纯函数；inline IPO 段改为单次调用 | Modify (~50 lines added, ~30 lines replaced) |
| `tests/test_hk_eod.py` | 6 个新 IPO 过滤场景 | Modify (append) |
| `CLAUDE.md` | HK IPO 段加 2 行阶梯说明 | Modify |

---

## Task 1: 写 6 个 `filter_hk_ipo_candidates` 失败测试

**Files:**
- Modify: `/Users/xue/finviz_to_tv/tests/test_hk_eod.py` (append at end)

- [ ] **Step 1: 在 `tests/test_hk_eod.py` 末尾追加 import + 6 个测试**

把下面这段追加到文件末尾（注意：现有 import 行 `from hk_eod import build_metrics_frame, apply_strategy_filters, dedup_by_priority` 在文件第 4 行，本任务需要把 `filter_hk_ipo_candidates` 加进去——但因为这是失败测试先行阶段，函数还不存在，先在末尾用一个独立 import 块，Task 2 实现后再到 Task 3 整理 import）：

```python
from hk_eod import filter_hk_ipo_candidates


def _ipo_metrics_row(
    market_cap=5e9,
    last_price=25.0,
    avg_vol_20d=1_000_000.0,
    avg_dollar_vol_20d=2.5e8,
    adr_pct=5.0,
    sma50=22.0,
    sma200=20.0,
    above_sma50=True,
    above_sma200=True,
):
    """Returns a metrics-frame row dict that PASSES all baseline gates."""
    return dict(
        market_cap=market_cap,
        last_price=last_price,
        avg_vol_20d=avg_vol_20d,
        avg_dollar_vol_20d=avg_dollar_vol_20d,
        adr_pct=adr_pct,
        sma50=sma50,
        sma200=sma200,
        above_sma50=above_sma50,
        above_sma200=above_sma200,
    )


def _hk_settings_default():
    return {
        "min_market_cap": 300_000_000,
        "min_price": 20.0,
        "min_avg_volume": 500_000,
        "min_dollar_volume": 100_000_000,
        "min_adr_percent": 3.5,
        "min_rs_percentile_longs_3m": 90,
    }


def test_filter_hk_ipo_drops_when_history_below_20_days():
    # 15 行历史 < 20 → 立刻砍掉，不进入任何 metric 闸门
    closes = [25.0] * 15
    klines = {"HK.NEW1": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.NEW1": _ipo_metrics_row()}, orient="index"
    )
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=None, hk_settings=_hk_settings_default()
    )
    assert kept == []
    assert drops["min_history"] == 1


def test_filter_hk_ipo_keeps_20_to_49_days_without_rs_check():
    # 30 行历史 — 过 20-day floor，未达 64 天 RS 触发线 → 走现有阶梯放行
    closes = [25.0] * 30
    klines = {"HK.MID": _make_kline(closes)}
    # 短历史：SMA50/SMA200 都是 NaN，above_sma 字段为 False（与 build_metrics_frame 一致）
    metrics = pd.DataFrame.from_dict(
        {"HK.MID": _ipo_metrics_row(
            sma50=float("nan"), sma200=float("nan"),
            above_sma50=False, above_sma200=False,
        )},
        orient="index",
    )
    # 即使 rs_table_3m 里把 HK.MID 设成 RS=10，<64 天的 ticker 也不走 RS 检查
    rs_table = pd.DataFrame({"rs_percentile": [10]}, index=["HK.MID"])
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == ["HK.MID"]
    assert drops["rs_3m"] == 0
    assert drops["rs_3m_missing"] == 0


def test_filter_hk_ipo_keeps_when_rs_3m_at_or_above_threshold():
    # 70 行历史 ≥ 64 → 触发 3M RS 闸门；表内 percentile = 95 ≥ 90 → 保留
    closes = [25.0] * 70
    klines = {"HK.A": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.A": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    rs_table = pd.DataFrame({"rs_percentile": [95]}, index=["HK.A"])
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == ["HK.A"]
    assert drops["rs_3m"] == 0


def test_filter_hk_ipo_drops_when_rs_3m_below_threshold():
    # 70 行历史 ≥ 64 → 触发 3M RS 闸门；表内 percentile = 80 < 90 → drop
    closes = [25.0] * 70
    klines = {"HK.B": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.B": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    rs_table = pd.DataFrame({"rs_percentile": [80]}, index=["HK.B"])
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == []
    assert drops["rs_3m"] == 1


def test_filter_hk_ipo_drops_when_64days_but_missing_from_rs_table():
    # 70 行历史 ≥ 64 → 触发 3M RS 闸门；但 ticker 不在表里 → drop（rs_3m_missing）
    # 这种情况一般是 _score_from_kline 因 zero_last/zero_past 排掉了
    closes = [25.0] * 70
    klines = {"HK.C": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.C": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    rs_table = pd.DataFrame({"rs_percentile": [95]}, index=["HK.OTHER"])  # 不含 HK.C
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == []
    assert drops["rs_3m_missing"] == 1


def test_filter_hk_ipo_skips_rs_gate_when_table_is_none():
    # 70 行历史 ≥ 64 但 rs_table_3m=None (HSI fetch 失败) → 回退到只有 metric 闸门
    # day-20 floor 仍生效，但 RS 闸门跳过
    closes = [25.0] * 70
    klines = {"HK.D": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.D": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=None, hk_settings=_hk_settings_default()
    )
    assert kept == ["HK.D"]
    assert drops["rs_3m"] == 0
    assert drops["rs_3m_missing"] == 0
```

- [ ] **Step 2: 跑测试确认 6 条都失败**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/test_hk_eod.py -v 2>&1 | tail -25`
Expected: 6 条新测试 FAIL（`ImportError: cannot import name 'filter_hk_ipo_candidates' from 'hk_eod'`）。其它现有测试不受影响。

- [ ] **Step 3: 不提交（Task 2 一起提交）**

继续到 Task 2 实现函数后再 commit 这一份测试改动。

---

## Task 2: 实现 `filter_hk_ipo_candidates` 纯函数

**Files:**
- Modify: `/Users/xue/finviz_to_tv/hk_eod.py` (插入新函数；约 ~705 行附近，紧接 `dedup_by_priority` 之后、`hsi_day_change_pct` 之前)

- [ ] **Step 1: 在 `hk_eod.py` 第 664 行（`dedup_by_priority` 函数结束括号之后空行）和 `hsi_day_change_pct` 函数之前插入新函数**

具体定位：找到这一行
```python
def hsi_day_change_pct(
    host: str = "127.0.0.1", port: int = 11111
) -> float | None:
```
（应在第 667 行附近）

在它之前（保留一个空行分隔）插入：

```python
def filter_hk_ipo_candidates(
    klines: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
    rs_table_3m: pd.DataFrame | None,
    hk_settings: dict,
) -> tuple[list[str], dict[str, int]]:
    """筛选 HK IPO 候选 (< 253 行历史的 HKEX 主板 ticker)。

    过滤阶梯（同一 ticker 按顺序走，命中任一即 drop 并计数）：
      - len(df) < 20          → drops['min_history']     (新增 day-20 floor)
      - cap < min_market_cap  → drops['cap']
      - price < min_price     → drops['price']
      - if has 20-day metrics:
          avg_vol < min_avg_volume       → drops['avg_vol']
          $vol   < min_dollar_volume     → drops['dvol']
          ADR%   < min_adr_percent       → drops['adr']
      - if has SMA50: not above SMA50    → drops['sma50']
      - if has SMA200: not above SMA200  → drops['sma200']
      - if len(df) >= 64 and rs_table_3m is not None:        (新增 3M RS 闸门)
          ticker not in rs_table_3m      → drops['rs_3m_missing']
          rs_percentile < threshold      → drops['rs_3m']

    253 行及以上的 ticker 不在此函数范围（走长线流水线），直接跳过。

    Returns:
        (kept_codes, drop_counts) — kept_codes 是 Futu 5-digit 格式
        (例如 "HK.00700")；drop_counts 包含上述所有 reason keys，
        每个 key 至少为 0。
    """
    ipo_cap = hk_settings.get("min_market_cap", 300_000_000)
    ipo_min_price = hk_settings.get("min_price", 20.0)
    ipo_min_avg_vol = hk_settings.get("min_avg_volume", 500_000)
    ipo_min_dvol = hk_settings.get("min_dollar_volume", 100_000_000)
    ipo_min_adr = hk_settings.get("min_adr_percent", 3.5)
    rs_3m_threshold = int(hk_settings.get("min_rs_percentile_longs_3m", 90))

    kept: list[str] = []
    drops: dict[str, int] = {
        "min_history": 0,
        "cap": 0, "price": 0,
        "avg_vol": 0, "dvol": 0, "adr": 0,
        "sma50": 0, "sma200": 0,
        "rs_3m": 0, "rs_3m_missing": 0,
    }

    for code, df in klines.items():
        if len(df) >= 253:
            continue  # 完整历史 — 由长线流水线处理
        if len(df) < 20:
            drops["min_history"] += 1
            continue
        if code not in metrics.index:
            continue
        row = metrics.loc[code]
        if not (pd.notna(row["market_cap"]) and row["market_cap"] >= ipo_cap):
            drops["cap"] += 1
            continue
        if not (pd.notna(row["last_price"]) and row["last_price"] >= ipo_min_price):
            drops["price"] += 1
            continue
        # 20-day-conditional gates — build_metrics_frame 在 < 20 行时把这三个字段
        # 置 NaN，所以 pd.notna 检查既挡掉短历史 ticker 的 KeyError 又保留了
        # "未达 20 天的 IPO 不走这三条闸门" 的旧语义。但因为 len(df) < 20 已被
        # 砍掉，这里实际上一定有 20-day metrics。
        if pd.notna(row["avg_vol_20d"]) and row["avg_vol_20d"] < ipo_min_avg_vol:
            drops["avg_vol"] += 1
            continue
        if pd.notna(row["avg_dollar_vol_20d"]) and row["avg_dollar_vol_20d"] < ipo_min_dvol:
            drops["dvol"] += 1
            continue
        if pd.notna(row["adr_pct"]) and row["adr_pct"] < ipo_min_adr:
            drops["adr"] += 1
            continue
        # 50/200-day conditional — sma50/sma200 为 NaN 时不查 above_sma
        if pd.notna(row["sma50"]) and not bool(row["above_sma50"]):
            drops["sma50"] += 1
            continue
        if pd.notna(row["sma200"]) and not bool(row["above_sma200"]):
            drops["sma200"] += 1
            continue
        # 3M RS gate — 仅当 len(df) >= 64 (即 3M RS 算法可计算) 且表存在时触发
        if len(df) >= 64 and rs_table_3m is not None and not rs_table_3m.empty:
            if code not in rs_table_3m.index:
                drops["rs_3m_missing"] += 1
                continue
            if int(rs_table_3m.loc[code, "rs_percentile"]) < rs_3m_threshold:
                drops["rs_3m"] += 1
                continue
        kept.append(code)

    return kept, drops
```

- [ ] **Step 2: 跑 Task 1 的 6 条测试确认全过**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/test_hk_eod.py -v 2>&1 | tail -20`
Expected: 全部 PASS（包含 6 条新增 + 5 条现有 = 11 条至少）。

- [ ] **Step 3: 跑全部测试确认无回归**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/ -q 2>&1 | tail -5`
Expected: 全绿。

- [ ] **Step 4: Commit (Task 1 + Task 2)**

```bash
git add hk_eod.py tests/test_hk_eod.py
git commit -m "$(cat <<'EOF'
feat(hk_eod): add filter_hk_ipo_candidates with 20-day floor + 3M RS gate

Extracts the previously-inline IPO filter into a testable pure function.
Two new gates layered onto the existing conditional ladder:
  - hard minimum 20 trading days of history (drops day 1-19 fresh IPOs)
  - at len(df) >= 64 (3 months), require 3M RS >= threshold (default 90,
    reuses min_rs_percentile_longs_3m). Missing from rs_table_3m → drop;
    rs_table_3m is None → skip the gate (HSI fetch failure fallback).

run_hk_eod still uses the inline copy in this commit — the next commit
swaps it for a call to the new helper.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

注：此 commit 引入新函数但 `run_hk_eod` 还没切过来（Task 3 才切）。这是 TDD pattern：先实现 + 测试，再接入。

---

## Task 3: 让 `run_hk_eod` 调用新函数；删除 inline IPO 段

**Files:**
- Modify: `/Users/xue/finviz_to_tv/hk_eod.py:944-1028` (inline IPO 段 → 单次调用)

- [ ] **Step 1: 替换 `hk_eod.py` 第 944-1028 行**

找到这段（从 `# --- HK IPO sidecar ---` 注释到 `logger.info(f"[HK IPO] {len(ipo_codes)}` 之前所有内容）：

```python
    # --- HK IPO sidecar ---
    # Mirrors the US IPO sidecar contract: catch tickers in the HKEX Main
    # Board universe that yfinance returned but with insufficient history
    # ... (大段注释 + filter loop + ipo_codes 收集) ...
    ipo_seen_path = eod_seen_path(output_dir, "HKIPO")
    ipo_seen = load_seen(ipo_seen_path)
    ipo_tv = sorted(_to_tv(c) for c in ipo_codes)
    logger.info(
        f"[HK IPO] {len(ipo_codes)} candidates after conditional filters "
        f"(cap>={ipo_cap:,.0f}, price>={ipo_min_price}, "
        f"+if 20d: avg_vol>={ipo_min_avg_vol:,.0f} & $vol>={ipo_min_dvol:,.0f} & ADR>={ipo_min_adr}%, "
        f"+if 50d: above SMA50, +if 200d: above SMA200); "
        f"raw klines<253: {sum(1 for df in klines.values() if len(df) < 253)}; "
        f"dropped: {ipo_dropped}"
    )
```

替换为：

```python
    # --- HK IPO sidecar ---
    # Tickers with insufficient history for the IBD 12-month RS calc
    # (< 253 rows). Filtered through filter_hk_ipo_candidates (see its
    # docstring for the gate ladder). Independent cross-day master at
    # output/state/eod_seen_HKIPO.txt — a ticker collected today as IPO
    # still lands in its proper long-side group on the first day it has
    # enough history (the long-side master eod_seen_HK.txt is separate).
    #
    # 2026-05-21 tightening: added (1) hard minimum 20 trading days, and
    # (2) 3M RS >= min_rs_percentile_longs_3m at len(df) >= 64. See
    # filter_hk_ipo_candidates for the full ladder.
    ipo_codes, ipo_dropped = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m, hk_settings
    )

    ipo_seen_path = eod_seen_path(output_dir, "HKIPO")
    ipo_seen = load_seen(ipo_seen_path)
    ipo_tv = sorted(_to_tv(c) for c in ipo_codes)
    rs_3m_threshold = int(hk_settings.get("min_rs_percentile_longs_3m", 90))
    logger.info(
        f"[HK IPO] {len(ipo_codes)} candidates after conditional filters "
        f"(>=20d hist; cap>={hk_settings.get('min_market_cap', 300_000_000):,.0f}, "
        f"price>={hk_settings.get('min_price', 20.0)}, "
        f"+if 20d: avg_vol/dvol/ADR, +if 50d: SMA50, +if 200d: SMA200, "
        f"+if 64d: RS_3M>={rs_3m_threshold}); "
        f"raw klines<253: {sum(1 for df in klines.values() if len(df) < 253)}; "
        f"dropped: {ipo_dropped}"
    )
```

- [ ] **Step 2: 跑全部测试无回归**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/ -q 2>&1 | tail -5`
Expected: 全绿（数量与 Task 2 后一致）。

- [ ] **Step 3: 静态 sanity check**

Run: `cd /Users/xue/finviz_to_tv && uv run python -c "import hk_eod; print('ok')"`
Expected: `ok`，无 ImportError / SyntaxError。

- [ ] **Step 4: Commit**

```bash
git add hk_eod.py
git commit -m "$(cat <<'EOF'
refactor(hk_eod): inline IPO segment → call filter_hk_ipo_candidates

Behavioral change is contained to the new gates already covered by
tests in the previous commit:
  - day 1-19 IPOs dropped (counted as drops['min_history'])
  - 64+ day IPOs require 3M RS >= 90 (counted as drops['rs_3m'])
  - 64+ day IPOs missing from rs_table_3m dropped (drops['rs_3m_missing'])

Log line format updated to surface the two new gate conditions; the
'dropped' dict gains 'min_history', 'rs_3m', 'rs_3m_missing' keys.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: CLAUDE.md 更新 HK IPO 段

**Files:**
- Modify: `/Users/xue/finviz_to_tv/CLAUDE.md` (HK IPO 段，搜索 "HK IPO" + "conditionally" 锚点)

- [ ] **Step 1: 找到 HK IPO 段**

Run: `grep -n "HK IPO.*auto-collected\|Always.*day 1\|≥ 200 trading days" /Users/xue/finviz_to_tv/CLAUDE.md`
Expected: 输出 HK IPO 段的几行行号（在 65-75 行附近）。

- [ ] **Step 2: 在「**If ≥ 200 trading days**」那一行之后追加两条新规则**

打开 CLAUDE.md 找到这一行（应该长这样）：

```markdown
  - **If ≥ 200 trading days** (i.e., `sma200` is non-NaN): price above SMA200.
```

在它之后立刻插入两行 + 一段说明（保持现有缩进）：

```markdown
  - **Hard minimum 20 trading days** (added 2026-05-21): `len(df) < 20` → dropped (drops['min_history']). Day 1-19 fresh IPOs are too noisy on volume to be useful candidates.
  - **If ≥ 64 trading days** (i.e., the 3M RS algorithm can score the ticker; added 2026-05-21): `3M RS percentile ≥ min_rs_percentile_longs_3m` (default 90). The 3M RS gate reuses the long-side `[hk_settings] min_rs_percentile_longs_3m` knob — set to 0 to disable. Tickers with ≥ 64 days that are **missing** from the 3M RS table (typically `_score_from_kline` rejections on `zero_last` / `zero_past` data-hygiene grounds) are also dropped (drops['rs_3m_missing']). When `rs_table_3m is None` (HSI fetch failure) the RS gate is skipped entirely — same fallback shape as the long-side flow.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(CLAUDE): document HK IPO 20-day floor + 3M RS gate

Adds two lines to the HK IPO conditional ladder: the new hard 20-day
minimum and the 3M RS >= 90 gate at 64+ days. Both gates share the
existing min_rs_percentile_longs_3m config knob.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 全套测试 + 手工 smoke 验证

**Files:** (验证步骤；无文件修改)

- [ ] **Step 1: 跑完整单元测试**

Run: `cd /Users/xue/finviz_to_tv && uv run pytest tests/ -v 2>&1 | tail -20`
Expected: 全绿。新增 6 条测试应都在 PASSED 列表里：
- `test_filter_hk_ipo_drops_when_history_below_20_days`
- `test_filter_hk_ipo_keeps_20_to_49_days_without_rs_check`
- `test_filter_hk_ipo_keeps_when_rs_3m_at_or_above_threshold`
- `test_filter_hk_ipo_drops_when_rs_3m_below_threshold`
- `test_filter_hk_ipo_drops_when_64days_but_missing_from_rs_table`
- `test_filter_hk_ipo_skips_rs_gate_when_table_is_none`

- [ ] **Step 2: 手工跑一次 HK EOD**

Run: `cd /Users/xue/finviz_to_tv && timeout 600 uv run main.py --mode hk-eod 2>&1 | tail -80`
Expected:
- 不报错完成
- log 里能看到 `[HK IPO] N candidates after conditional filters (>=20d hist; cap>=..., ... +if 64d: RS_3M>=90); raw klines<253: M; dropped: {min_history: A, cap: B, price: C, avg_vol: D, dvol: E, adr: F, sma50: G, sma200: H, rs_3m: I, rs_3m_missing: J}`
- 至少 `min_history` 计数应该 > 0（确认 day 1-19 真新股被砍）
- `output/TV/HK/<date>_IPO.txt` 候选数量较改造前明显减少

- [ ] **Step 3: 健全性检查 — IPO 桶不应为空（除非真是冷门日）**

Run: `wc -l /Users/xue/finviz_to_tv/output/TV/HK/*_IPO.txt 2>&1 | head -3 && cat /Users/xue/finviz_to_tv/output/TV/HK/$(date +%Y_%m_%d)_IPO.txt 2>/dev/null | head -5`
Expected: 至少有今天的 `<date>_IPO.txt` 文件，内容可能为空（IPO 数据本来就少；尤其叠加新闸门后），但文件必须存在。

- [ ] **Step 4: 健全性检查 — log 中所有 `dropped` keys 都该出现**

Run: `cd /Users/xue/finviz_to_tv && grep -E "\[HK IPO\].*dropped" output/launchd_HK.log 2>/dev/null | tail -1 || echo "no log; check stdout from Step 2"`

如果 launchd 还没跑过（你是手动 smoke），直接看 Step 2 的 stdout。验证 `dropped:` 字典里有这些 keys：`min_history`, `cap`, `price`, `avg_vol`, `dvol`, `adr`, `sma50`, `sma200`, `rs_3m`, `rs_3m_missing`。每个 key 至少应该是 0；`min_history` 大概率 > 0。

- [ ] **Step 5: （非必要）跑 3-5 天后回看收紧效果**

观察 IPO 桶日均规模是否符合预期（应该比改造前小 50-75%）。如果太少（< 1/天），考虑把 `min_rs_percentile_longs_3m` 从 90 调到 80（top 20%）。

- [ ] **Step 6: 无需 commit**（本 task 是纯验证）

---

## Spec → Plan Coverage Map

| Spec 章节 | 覆盖 Task |
|----------|----------|
| 过滤栈：20-day floor + 3M RS gate | Task 1 测试 + Task 2 实现 |
| 关键决策：20 硬编码 | Task 2 `if len(df) < 20` |
| 关键决策：复用 `min_rs_percentile_longs_3m` | Task 2 `hk_settings.get("min_rs_percentile_longs_3m", 90)` |
| 关键决策：缺失即 DROP | Task 1 `test_filter_hk_ipo_drops_when_64days_but_missing_from_rs_table` + Task 2 `drops["rs_3m_missing"]` 分支 |
| 关键决策：`rs_table_3m is None` 时跳过 | Task 1 `test_filter_hk_ipo_skips_rs_gate_when_table_is_none` + Task 2 `if ... rs_table_3m is not None and not rs_table_3m.empty` 守卫 |
| 代码结构：抽出 `filter_hk_ipo_candidates` | Task 2 |
| run_hk_eod 改为单次调用 | Task 3 |
| 日志格式更新 | Task 3 (log line) |
| Failure modes (klines 空 / metrics 空 / 多日重跑) | Task 1 测试覆盖；剩余靠 build_metrics_frame / load_cache 既有契约 |
| CLAUDE.md 更新 | Task 4 |
| 实施清单 1-3 | Task 1, 2, 3 |
| 实施清单 4（无 config 改动）| 整个 plan 不动 config.toml |
| 验证 1-3（pytest + 手动 smoke + 多日观察） | Task 5 |
