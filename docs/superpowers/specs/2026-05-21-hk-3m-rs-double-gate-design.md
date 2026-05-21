# HK 长线 RS 双闸门 (12月 ∩ 3月)

**日期**：2026-05-21
**作者**：xue
**状态**：Draft — 待用户审阅

## 背景

当前 HK 长线流水线（`hk_eod.py`）的 5 个组（EarningsGap / HighVolume / GapUp / Leaders / RS）共用一道 RS 闸门：

```
RS_12M = 0.4·R3 + 0.2·R6 + 0.2·R9 + 0.2·R12   (vs HSI)
```

跨能算 12 月分数的 HK 主板子集（~282/2400 只）做百分位，配置 `[hk_settings] min_rs_percentile_longs = 90`。

**问题**：12 月 RS 高的 ticker 里有相当一部分是「已经掉头但年化数字还没掉下来」的旧 leader。在追 momentum 的策略里这是噪声源——这些股票在闸门通过后会被基线门槛（成交量、SMA、ADR）和事件类闸门（gap、RVol）部分截掉，但偶发漏网会污染当日清单。

## 目标

引入第二道 3 月 RS 闸门，与 12 月 RS 闸门串联（AND）：

```
RS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63   (vs HSI)
```

只允许「既是 12 月长线 leader、又是 3 月仍在加速」的 ticker 通过。语义：**老 leader 且还在领跑**。

**非目标**：本期不在 US 侧实施。US 用的是 Fred6725 提供的 CSV（只发 12 月版），需要本地实现整套 ~6000 只的 RS 计算才能加 3 月版，工程量与风险与 HK 不对称。HK 试运行稳定后再讨论 US。

## 设计

### 算法

3 月 RS 计算复用 `hk_rs._score_from_kline` 的现有重叠窗口结构，仅替换权重元组：

```python
WEIGHTS_12M = [(3, 0.4), (6, 0.2), (9, 0.2), (12, 0.2)]   # 现有
WEIGHTS_3M  = [(1, 0.5), (2, 0.3), (3, 0.2)]              # 新增
```

`months` 字段是「往回看 months × 21 个交易日」的偏移；权重按 IBD 风格压在最短窗口上，重叠窗口产生隐式时间衰减。

最小历史要求从 `max(months) * 21 + 1` 推导：12M 需 ~253 行，3M 需 ~64 行。3M 表覆盖的 ticker 数会显著大于 12M（HK 新上市股普遍能凑齐 64 天但不到 253 天）。

百分位排名：对所有可算分的 ticker 做 `(stock_score - HSI_score)` 排序，`rank(method='average', pct=True) * 99` 取整。两个表的分母（参与排名的 ticker 集合）独立，分数不可跨表比较，但 AND 闸门只关心各自是否 ≥ 90。

### 闸门串联

`hk_eod.py` 第 870-884 行附近的 RS 过滤段改造：

```python
threshold_12m = int(hk_settings.get("min_rs_percentile_longs", 90))
threshold_3m  = int(hk_settings.get("min_rs_percentile_longs_3m", 90))

pre_counts = {n: len(c) for n, c in raw.items()}
after_12m  = {n: filter_by_rs(c, rs_table_12m, threshold_12m) for n, c in raw.items()}
after_3m   = {n: filter_by_rs(c, rs_table_3m,  threshold_3m)  for n, c in after_12m.items()}
raw = after_3m

logger.info(
    f"[HK Longs] RS 12m>={threshold_12m} ∩ 3m>={threshold_3m}: "
    + ", ".join(
        f"{n} {pre_counts[n]}→{len(after_12m[n])}→{len(after_3m[n])}"
        for n in HK_STRATEGY_PRIORITY
    )
)
```

「缺失即 passthrough」策略两层都遵守（与现有 `filter_by_rs` 行为一致）。阈值任一设为 0 即关闭该层闸门；都设 0 时 GitHub 表加载步骤可以跳过（但本期不做这层短路优化，让代码路径保持简单）。

**应用范围**：与现有 12 月闸门完全一致。先施加到 `raw` 字典的所有 5 个组（EarningsGap/HighVolume/GapUp/Leaders/RS）。RS 组在条件触发（HSI 跌幅 ≤ -1.2%）下才有内容，闸门作用于它的内容物——这意味着弱市日的 RS 组现在要求「老 leader 且仍在领跑」，符合「surface 真正还能扛跌的强势股」的语义。

**不应用**：IPO sidecar 维持现状（不过 RS）。IPO 桶定义就是「凑不齐 12 个月历史」，本来就跳过 RS 闸门。

### 配置

`config.toml` 第 194 行下新增一行：

```toml
[hk_settings]
# ... (上方既有字段不动) ...
min_rs_percentile_longs    = 90    # 12月 RS 闸门 — 与 HSI 比较，本地 IBD 算法 (top 10%)
min_rs_percentile_longs_3m = 90    # 3月 RS 闸门 — 0.5·R21+0.3·R42+0.2·R63 vs HSI (top 10%)
```

调优指南：
- 候选池太小 → 把 `min_rs_percentile_longs_3m` 下调到 80
- 想退回单闸 12 月 → `min_rs_percentile_longs_3m = 0`
- 想完全关 RS 闸门 → 两个都设 0

### 缓存与持久化

3 月 RS 表写到独立缓存文件：

```
output/state/hk_rs_rating_<YYYY-MM-DD>.csv      # 既有 12月
output/state/hk_rs_rating_3m_<YYYY-MM-DD>.csv   # 新增 3月
```

`hk_rs.cache_path / save_cache / load_cache` 加 `suffix=""` 参数（默认空 → 12 月路径，向后兼容）。

`compute_rs_table` 被同一次 HK EOD 调用 2 次（同一份 `klines` 和 `hsi_kline`，权重不同），同一次 HSI fetch 复用，零额外 IO。

### 影响到的下游

1. **`cleanup.py`**：在 `_RETENTION_RULES` 加一条 `^hk_rs_rating_3m_(\d{4}-\d{2}-\d{2})\.csv$`，2 日保留窗口（与 12m 一致）。
2. **`report/__main__.py`**：日报当前用 `_load_rs_lookup` 读 RS 表。候选路径列表是精确文件名，**天然不会**匹配到 `hk_rs_rating_3m_*.csv`（候选列表里没有 `_3m` 字面量）。无需改动，但要在测试里加一条断言确认这个保证。
3. **`tests/test_cleanup.py`**：加一组 3m CSV 的保留断言。
4. **`tests/test_hk_rs.py`**：覆盖 3m weights 路径、最小历史阈值（应 64 而非 253）、双闸串联场景。
5. **CLAUDE.md**：在 「HK: `hk_rs.py`」章节加一段，记录双闸 + 3m 公式 + 关闸方式（`= 0`）。

### Failure modes

| 情景 | 行为 |
|------|------|
| HSI k 线 fetch 失败 | 两个表都不算（与现状一致），过滤层走 None 表 passthrough |
| `klines` 为空 | 两个表都跳过计算，passthrough |
| 3m 表所有 ticker 都不够 64 天历史 | 表为空 DataFrame，`filter_by_rs` 对所有 ticker passthrough → 等价于只有 12m 闸门生效。Log 会出现 `[HK RS 3M] computed: 0/N`，足以告警 |
| `min_rs_percentile_longs_3m` 配置缺失 | `get(..., 90)` 默认值兜底，行为与显式 90 一致 |
| 同一天重跑（cache 命中） | 两个表分别 load_cache，命中即跳过重算（与现状一致） |

### 风险

**候选池缩水**。当前 12m ≥ 90 在 ~282 候选里筛出约 28 只；3m 闸门是相关但不完全相关的信号，乐观估计交集 15-20 只，悲观估计 < 10 只。弱市场日（RS 组触发）可能更少。

**应对**：
1. log 每个组输出「pre → 12m 后 → 3m 后」三段计数，便于运行 3-5 天后量化 3m 闸门的额外切割力度
2. 配置侧留 `min_rs_percentile_longs_3m` 独立旋钮，不重跑代码就能放宽
3. 不引入「保底数量」一类的兜底逻辑——「真没东西就是真没东西」与现有空 .txt 文件契约一致

## 实施清单

1. `hk_rs.py`：
   - 加 `WEIGHTS_12M` / `WEIGHTS_3M` 常量
   - `_score_from_kline(df, weights=WEIGHTS_12M)` 接受 weights，min_rows 从权重推导
   - `compute_rs_table(klines, hsi_kline, weights=WEIGHTS_12M, label="12M")` 接受 weights/label，log 里使用 label
   - `cache_path / save_cache / load_cache` 加 `suffix=""` 参数
2. `hk_eod.py`：
   - 同一次 HSI fetch 算两张表，分别 save_cache
   - `filter_by_rs` 串联 12m → 3m
   - log 改为三段计数
3. `config.toml`：新增 `min_rs_percentile_longs_3m = 90`
4. `cleanup.py`：新增 `hk_rs_rating_3m_*.csv` 保留规则
5. `tests/test_hk_rs.py`：新增 3m 路径用例（权重、最小历史、AND 串联）
6. `tests/test_cleanup.py`：新增 3m CSV 保留断言
7. `tests/test_report_main.py`：新增断言确认日报不会误读 3m CSV
8. `CLAUDE.md`：HK RS 章节加双闸说明

## 验证

1. `uv run pytest tests/ -v` 全绿
2. 手跑 `uv run main.py --mode hk-eod` 一次（盘后或 use_yesterday 路径），观察：
   - `output/state/` 下出现 `hk_rs_rating_<date>.csv` 和 `hk_rs_rating_3m_<date>.csv`
   - log 三段计数合理（3m 闸门切掉的数量 > 0 但不至于清零）
   - `.txt` 输出非空（除非真是冷门日）
3. 第二天再跑一次确认 cache 命中（两张表都不重算）
4. 跑 3-5 个交易日后回看 3m 闸门切割力度，决定是否调整阈值

## 决策记录

- **为什么不用「12m + 3m 加权合分」单闸门**：交集语义更直观，能在 log 里独立量化两条闸门各自切了多少；加权合分会把信号纠缠在一起，调参困难。
- **为什么 IPO sidecar 不加 RS**：IPO 桶现有定义就是「12m 历史不够」，加 RS 自相矛盾。3m 闸门虽然只要 64 天，但 IPO 桶里有大量 < 64 天的真新股，加上去会出现「<64 天的走 passthrough、≥64 天的走 3m 闸门」的不一致行为。
- **为什么不为「关闸优化」加短路**：两个闸门都是 0 的情况极少，多算一次 RS 表 IO 开销可忽略，省下分支逻辑更值。
- **为什么不直接覆盖 12 月闸门成 3 月闸门**：12 月版是经过验证的稳态信号，3 月版是新引入的、噪声特征未知。AND 串联是「在已知基线上叠加」，可观察可回滚；直接替换是「换信号」，回滚要改代码。
