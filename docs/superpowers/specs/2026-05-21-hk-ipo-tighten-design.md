# HK IPO 收紧：20-day floor + 3M RS 闸门

**日期**：2026-05-21
**作者**：xue
**状态**：Draft — 待用户审阅

## 背景

`hk_eod.py:944-1034` 的 HK IPO sidecar 当前对历史 < 253 行的 HKEX Main Board 股票做条件过滤：

| 历史长度 | 触发的闸门 |
|----------|-----------|
| 永远 | cap ≥ HK$300M, price ≥ HK$20 |
| ≥ 20 天 | avg_vol ≥ 500K, $vol ≥ HK$100M, ADR ≥ 3.5% |
| ≥ 50 天 | above SMA50 |
| ≥ 200 天 | above SMA200 |

日 1-19 的 IPO 只过 cap + price 两道闸门，导致每天 IPO 桶里有相当数量的真新股（成交量噪声大、价格波动尚未充分释放）。即使 60-252 天的 IPO，也没有任何相对强度信号——可能是"假突破"也可能是"主升起步"，靠肉眼分不清。

我们刚在 2026-05-21 的双闸门改动里引入了本地 3M RS 表（`output/state/hk_rs_rating_3m_<date>.csv`），算法 `0.5·R21 + 0.3·R42 + 0.2·R63` vs HSI，最小历史要求 64 行 = 3 个月。这张表对 IPO 段天然可用——它的覆盖面比 12M RS 表广得多（98% vs 93%，2026-05-21 实测）。

## 目标

把 HK IPO 桶从「条件阶梯放行」收紧成「条件阶梯 + 两道额外硬闸门」：

1. **最小历史 20 天**：day 1-19 的真新股一律不进 IPO 桶
2. **3M RS ≥ 90**：64 天以上的 IPO 必须过 3 月相对强度闸门；64 天以下走现有阶梯，跳过 RS

语义：「IPO 桶 = 已经熬过 20 天暖机 + 在 3 个月时间窗里已经显出强度的候选 leader」。

**非目标**：本期不在 US IPO 侧实施。US IPO 用的是 Fred6725 CSV 的 12M RS 体系，目前没有 3M 等价物——加 3M 闸门需要先在 US 侧实现本地 3M RS 计算，工程量大且不在本期范围。

## 设计

### 过滤栈

```
For each ticker with 20 ≤ len(df) < 253:        ← (1) 新增最小 20 行
  cap >= HK$300M                                 ← 现有
  price >= HK$20                                 ← 现有
  if has 20-day metrics: avg_vol, $vol, ADR     ← 现有
  if has SMA50: above SMA50                      ← 现有
  if has SMA200: above SMA200                    ← 现有
  if len(df) >= 64 (3-month):                    ← (2) 新增 3M RS 闸门
    rs_3m_pct >= min_rs_percentile_longs_3m
    missing-from-table → drop (data hygiene)
```

`len(df) < 20` 的 ticker 直接 `continue`，进入新的 `ipo_dropped["min_history"]` 计数桶。`len(df) < 64` 的 ticker 跳过 3M RS 检查（现有阶梯放行）。`len(df) ≥ 64` 必须在 `rs_table_3m` 里找到且 `rs_percentile ≥ threshold`，否则进入 `ipo_dropped["rs_3m"]`。

### 关键决策

| 决策 | 默认 | 理由 |
|------|------|------|
| 最小历史阈值 | **硬编码 20**（不配 config） | 现有"20-day-conditional gates"注释里的 20 已是硬编码；不为这个再加一个 key |
| 3M RS 阈值 | 复用 `[hk_settings] min_rs_percentile_longs_3m` (当前=90) | 语义同长线 3M RS 闸门一致；增配置反而割裂 |
| `rs_table_3m` 缺失但 len ≥ 64 | **DROP**（不 passthrough） | 与长线 `filter_by_rs` 的 passthrough 不同——IPO 是收紧场景，缺失即 `zero_last` / `zero_past` 数据脏问题，宁可漏不可错 |
| `rs_table_3m is None`（HSI fetch 失败） | 跳过 3M 闸门，回退到只有 metric 闸门 | 与现有"OpenD 故障时长线流水线降级"行为对称；day-20 floor 仍生效 |
| 阈值 `min_rs_percentile_longs_3m = 0` | IPO 3M 闸门也跟着关 | 复用 key 的自然语义——一关全关 |
| 20-63 天 IPO | 走现有阶梯，不做 RS 检查 | 用户已选 (option A) — 给"刚 20 天但已显出成交量"的 IPO 留位置 |

### 代码结构

把现有 inline IPO 过滤段抽出成纯函数，沿用本模块已有模式（`build_metrics_frame` / `apply_strategy_filters` / `dedup_by_priority` 全是纯函数 + 单测）：

```python
def filter_hk_ipo_candidates(
    klines: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
    rs_table_3m: pd.DataFrame | None,
    hk_settings: dict,
) -> tuple[list[str], dict[str, int]]:
    """过滤 < 253 行的 HKEX 主板股票，返回 (kept_codes, drop_counts)。

    Drop reason keys:
      min_history (< 20 rows), cap, price, avg_vol, dvol, adr,
      sma50, sma200, rs_3m, rs_3m_missing
    """
```

放置位置：`dedup_by_priority` 之后（~705 行），inline 段改为单次调用 + 一行 log。

### 日志格式

```
[HK IPO] N candidates after conditional filters
  (cap>=300M, price>=20, +if 20d: avg_vol/dvol/ADR, +if 50d: SMA50,
   +if 200d: SMA200, +if 64d: RS_3M>=90);
  raw klines<253: M; dropped: {
    min_history: A,        # < 20 rows
    cap: B, price: C, avg_vol: D, dvol: E, adr: F,
    sma50: G, sma200: H,
    rs_3m: I,              # < threshold
    rs_3m_missing: J,      # >= 64 rows but not in rs_table_3m
  }
```

`min_history` 和 `rs_3m_missing` 加在末尾以保留前向兼容性（log scrapers 看不到顺序变化），其它键位维持现有顺序。

### Failure modes

| 情景 | 行为 |
|------|------|
| `klines` 为空 | 空 list 返回，零 drop count；现有 day-1 IPO 路径不变 |
| `metrics` 为空（OpenD 故障） | 现有 `code not in metrics.index` 分支接住，全部跳过 |
| `rs_table_3m is None` | 跳过 3M 闸门，只走 metric 阶梯（与现有行为一致） |
| `rs_table_3m` 为空 DataFrame | 同 `None`——`code not in table.index` 路径接住 |
| ticker 在 rs_table_3m 但 percentile 字段缺失 | KeyError 上抛——这是数据脏，不静默 |
| 多日重跑同一天（cache 命中） | `rs_table_3m` 来自 `load_cache(today_d, output_dir, suffix="3m")`，行为完全确定性 |

### 风险

**候选池缩水**。HK IPO 桶今天大约 5-15 只，加 day-20 floor 砍掉约 1/3-1/2，再加 3M RS 闸门可能再砍一半——稳定状态可能跌到 1-5 只/天。

**应对**：
1. log 里的 `min_history` 和 `rs_3m` 两个新计数让你能定量看每道闸门切了多少
2. `min_rs_percentile_longs_3m` 是已有旋钮，需要时改 config 即可放宽（设到 80 = top 20%）
3. 不引入"保底数量"——空 .txt 与现有契约一致

## 实施清单

1. **`hk_eod.py`**：
   - 抽出 `filter_hk_ipo_candidates(klines, metrics, rs_table_3m, hk_settings) -> (list[str], dict[str, int])`，放在 `dedup_by_priority` 之后（~705 行附近）
   - inline 段（984-1016 行）改为单次调用
   - 增加 `min_history` 闸门（loop 顶部 `if len(df) < 20: continue`）
   - 增加 3M RS 闸门（在 SMA200 检查之后）
   - 日志行更新 `dropped` 字典格式
2. **CLAUDE.md**：HK IPO 段的阶梯说明加 2 行（≥ 20、≥ 64）
3. **`tests/test_hk_eod.py`**：新增 5 个 IPO 过滤场景
4. **(无 config 改动)**：复用 `min_rs_percentile_longs_3m`，零新增 key

## 验证

1. `uv run pytest tests/ -v` 全绿（5 个新增 case 全过）
2. `uv run main.py --mode hk-eod` 跑一次，观察：
   - log 里 `dropped` 字典包含 `min_history` 和 `rs_3m` 两个新键
   - `min_history` 计数 > 0（确认 day 1-19 真新股被砍）
   - `rs_3m` 计数 ≥ 0（确认 3M 闸门在跑；当天 IPO 桶里 ≥ 64 天的样本可能少）
   - 最终 IPO 候选数 < 改造前同口径数
3. 跑 3-5 个交易日观察 IPO 桶日均规模，确认收紧效果符合预期

## 决策记录

- **为什么 20-63 天 IPO 不做 RS 检查**：用户已选 (A) — 给"刚 20 天但已显出成交量"的 IPO 留位置；3 个月后才用 RS 卡。
- **为什么 3M RS 表缺失即 drop（与长线 passthrough 不同）**：IPO 是收紧场景，缺失 = 数据脏 (`zero_last` / `zero_past`)，过宽放行会放进"价格归零"或"刚拆股没归一"的脏数据。长线的 passthrough 是给"我们没看到，但可能没问题"留位置，IPO 场景下这个放宽逻辑不成立。
- **为什么复用 `min_rs_percentile_longs_3m` 而不是新加 `min_rs_percentile_ipo_3m`**：语义同源、YAGNI、避免多旋钮调参矩阵。如果未来发现需要独立调参（如"长线 90、IPO 95"），加一个 key 是 1 行改动；先保持简单。
- **为什么 20 是硬编码而不是配置**：现有代码里 `20` 已是"20-day metrics 可计算"的硬编码常量（写在 build_metrics_frame 的 NaN 行为里），加 config key 反而引入双源真相风险。
- **为什么抽函数**：本模块的纯函数 + 单测模式已经成熟（`build_metrics_frame` 等都是这么测的）；inline 闸门难单测。抽函数是 ~30 行重构，换来 5 个 case 的可测性。
