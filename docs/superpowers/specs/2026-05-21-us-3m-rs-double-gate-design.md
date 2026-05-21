# US 长线 RS 双闸门 (12月 ∩ 3月)

**日期**：2026-05-21
**作者**：xue
**状态**：Draft — 待用户审阅

## 背景

当前 US 长线流水线（`main.py` / `rs_rating.py`）的所有 RS-gated 组——Longs 五个分支（EarningsGap / HighVolume / GapUp / NewHigh52W / TopGainers）、Leaders、conditional RS 组、Shorts——共用一道 12 月 RS 闸门：

```
RS_12M = 0.4·Q1 + 0.2·Q2 + 0.2·Q3 + 0.2·Q4   (vs SPY，IBD 加权)
```

数据来自 Fred6725 / rs-log 每日发布的 `rs_stocks.csv`，主列 `Percentile` 即此分数的全市场百分位（0-99，~6100 只 NYSE/NASDAQ 主板股票）。`[settings] min_rs_percentile = 90`（Leaders）和 `min_rs_percentile_longs = 90`（其余）都设 90。

港股侧（`hk_eod.py` / `hk_rs.py`）2026-05 已经引入 3 月 RS 第二道闸门并稳定运行，语义：「老 leader 且还在领跑」。本期把同样的语义引入 US。

## 目标

引入第二道 3 月 RS 闸门，与 12 月闸门串联（AND）：

```
RS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63   (vs SPY，3M 等权 IBD 风格)
```

应用顺序：先 12M ≥ 阈值砍一刀，再在幸存者上 3M ≥ 阈值砍第二刀。

**Fred6725 CSV 里的 `3M_RS_Percentile` 列不可复用**：它表达的是「3 个月前那一天的 12 月 RS 排名」，语义是「持续性 / 不是昙花一现」，不是「短窗口动量」。和港股 3M 闸门含义不一致；本期要的是和港股镜像。

**非目标**：
- 不引入 1M / 6M 闸门（YAGNI）。
- 不改动 IPO 组（IPO 现在不被 RS 闸过滤，本期保持）。
- 不改动 morning-gap（intraday 不需要长线 RS）。

## 设计

### 算法

新建 `us_rs_3m.py`，从 `hk_rs.py` 复制改造。算法照搬，benchmark 从 HSI → SPY：

```python
WEIGHTS_3M = [(1, 0.5), (2, 0.3), (3, 0.2)]   # 与 hk_rs.WEIGHTS_3M 字面相同

def _score_from_kline(df, weights=WEIGHTS_3M) -> tuple[float|None, str]:
    """Σ wᵢ·Rᵢ，最远回看 max(months)·21 = 63 个交易日。
    返回 (score, reason)，reason ∈ {ok, no_data, short_history, zero_last, zero_past}。"""

def compute_us_rs_3m_table(
    klines: dict[str, pd.DataFrame],
    spy_kline: pd.DataFrame,
) -> pd.DataFrame:
    """返回 DataFrame(index=ticker, col=rs_percentile, 0-99)。"""

def filter_by_rs(tickers, table, threshold) -> list[str]:
    """缺表 → passthrough；缺 ticker → kept-as-missing。"""

def cache_path(today, output_dir) -> Path:
    # output/state/rs_rating_3m_<date>.csv

def save_cache / load_cache
```

模块化处理与 `hk_rs.py` 等价（独立文件而不是 `hk_rs` 改名）：两个市场的 RS 计算保持物理隔离，避免 HK 改动牵动 US。`filter_by_rs` 签名与 `hk_rs.filter_by_rs` 一致（接受 DataFrame 表），保留与 `rs_rating.filter_by_rs`（接受 dict 表）的差异 — 12M 那层继续走现有 dict 路径。

### Universe & 数据来源

- **Universe = Fred6725 CSV 的 `Ticker` 列**（~6100 个）。理由：12M 百分位的分母就是它，3M 用同一分母 → 「全市场前 10%」在两层里语义一致（与 HK 镜像，HK 两层共用 HKEX 主板 ~2400 分母）。
- **SPY k 线** 通过 yfinance 同一次批量拉取，参与 `_score_from_kline` 算法，作为 benchmark。
- **拉取参数**：`yfinance.download(period="6mo", auto_adjust=True, group_by="ticker")`，给 63 个交易日留约 1 个月的缓冲（应对节假日 / 单日缺数据）。
- **批量大小**：每批 500 ticker（与 HK 既有 batch 一致），~13 个 batch。

### 触发条件

仅当 `max(min_rs_percentile_3m, min_rs_percentile_longs_3m) > 0` 时执行：

1. 触发 Fred6725 CSV 拉取（如未触发）→ 拿 universe
2. yfinance batch 拉取 universe + SPY
3. 计算 `rs_3m_table`,缓存到 `output/state/rs_rating_3m_<date>.csv`
4. 在所有 RS 闸门处串联应用

两个 3M 旋钮都为 0 → 完全跳过 yfinance batch 与 `rs_3m_table` 生成,既有 12M 行为不受影响。

### 闸门串联

`main.py` 现有的 4 个 `filter_by_rs` 调用点,每个后面紧跟一行 3M 调用:

| 调用点 | 既有 12M 阈值 | 新增 3M 阈值 |
|---|---|---|
| Longs/{key}（line ~1490） | `min_rs_percentile_longs` | `min_rs_percentile_longs_3m` |
| Leaders/{name}（line ~1540） | `min_rs_percentile` | `min_rs_percentile_3m` |
| RS group（line ~1606） | `min_rs_percentile_longs` | `min_rs_percentile_longs_3m` |
| run_shorts（line ~355） | `min_rs_percentile_longs`（注:既有传入这个,不是 `min_rs_percentile`） | `min_rs_percentile_longs_3m` |

伪代码片段:

```python
if min_rs_percentile_longs > 0 and tickers:
    tickers = filter_by_rs(tickers, rs_table_12m, min_rs_percentile_longs, f"  [Longs/{key}]")
if min_rs_percentile_longs_3m > 0 and tickers:
    tickers = us_rs_3m.filter_by_rs(tickers, rs_table_3m, min_rs_percentile_longs_3m)
    logger.info(f"  [Longs/{key}] {len(tickers)} after RS_3M >= {min_rs_percentile_longs_3m}")
```

`run_shorts` 签名扩展(同 HK shorts 处理):

```python
def run_shorts(
    ...,
    rs_table: dict[str, int] | None = None,         # 既有 — 12M
    min_rs_percentile: int = 0,                     # 既有 — 12M(参数名是这个,但 caller 传入的是 min_rs_percentile_longs 的值)
    rs_table_3m: pd.DataFrame | None = None,        # 新增
    min_rs_percentile_3m: int = 0,                  # 新增(参数名,caller 传入 min_rs_percentile_longs_3m 的值)
):
```

注意:`run_shorts` 的形参叫 `min_rs_percentile`,但调用处实际传的是 `min_rs_percentile_longs`(见 `main.py:1577-1579`)— 这是既有命名遗留,本期不改。新加的 `min_rs_percentile_3m` 形参同样接收 caller 的 `min_rs_percentile_longs_3m` 值。

### Config

`[settings]` 新增 2 行,放在既有 `min_rs_percentile_longs` 旁边:

```toml
min_rs_percentile          = 90    # 既有 — Leaders 12M
min_rs_percentile_3m       = 90    # 新 — Leaders 3M(本地短窗口 vs SPY)
min_rs_percentile_longs    = 90    # 既有 — Longs 5 splits / conditional RS / Shorts 12M
min_rs_percentile_longs_3m = 90    # 新 — Longs 5 splits / conditional RS / Shorts 3M
```

任一旋钮 = 0 即独立关闭那一层(与 HK 一致):
- `min_rs_percentile = 0` → 关闭 Leaders 12M(既有)
- `min_rs_percentile_3m = 0` → 关闭 Leaders 3M(新)
- `min_rs_percentile_longs = 0` → 关闭 Longs/RS/Shorts 12M(既有)
- `min_rs_percentile_longs_3m = 0` → 关闭 Longs/RS/Shorts 3M(新)
- 两个 3M 同时 = 0 → 完全跳过 yfinance batch 和 `rs_rating_3m_<date>.csv` 生成

### 日志格式

延续既有「每次 `filter_by_rs` 一行」的模式,3M 紧跟 12M:

```
[Longs/EarningsGap] 38 after RS >= 90 (dropped 412, kept-as-missing 5)
[Longs/EarningsGap] 31 after RS_3M >= 90 (dropped 7, kept-as-missing 0)
```

外加一行 batch 总结(镜像 hk_rs):

```
[US RS 3M] computed: 5847/6103 klines scored. Reason breakdown: {'ok': 5847, 'short_history': 198, 'zero_last': 12, 'no_data': 46}
```

### 失败模式 / passthrough

镜像 `hk_rs` + 既有 `rs_rating`:

| 故障 | 行为 |
|---|---|
| yfinance batch 全失败 → `klines` 为空 | `rs_3m_table` 为 None;`filter_by_rs` warn + passthrough(只跳 3M 层,12M 仍生效) |
| SPY k 线缺失 | fallback 到 `spy_score=0.0`(等同于绝对分数排名);警告日志 |
| 单 ticker 不足 64 行 | 不入表 → `filter_by_rs` kept-as-missing(IPO 类) |
| Fred6725 CSV 拉取整体失败(已有 fallback) | 12M 和 3M 同时退化为 passthrough — 既有行为,无新逻辑 |

### Cache 与 cleanup

- 新缓存路径 `output/state/rs_rating_3m_<date>.csv`,文件 schema: `ticker,rs_percentile`(与 `hk_rs_rating_3m_*.csv` 同构,只是 index 名为 `ticker` 不是 `code`)。
- `cleanup.py` 加新 glob `rs_rating_3m_*.csv`,保留窗口 **4 天**(与既有 12M `rs_rating_*.csv` 一致 — 给 `_FALLBACK_MAX_AGE_DAYS = 3` 留 1 天安全垫)。
- 报告生成器 `report/`:不需要改动,3M 表不参与 daily report(daily report 只读 `.txt` 文件)。但 `tests/test_report_*` 里如果有 cleanup / 文件枚举相关 assertion,要确认新文件不被误读。

### 测试

新建 `tests/test_us_rs_3m.py`(纯逻辑,无网络):

1. `_score_from_kline` 算法:
   - happy path: 构造 64 行单调上升的 close,验证 score > 0
   - `no_data`: 空 DataFrame → (None, "no_data")
   - `short_history`: 63 行 → (None, "short_history")
   - `zero_last`: 最后一行 close = 0 → (None, "zero_last")
   - `zero_past`: lookback 点 close = 0 → (None, "zero_past")

2. `compute_us_rs_3m_table` 端到端:
   - 构造 5 个 ticker + SPY 的 mock k 线,验证返回 DataFrame 索引 / 列 / 百分位范围 0-99
   - 验证 SPY 失败 fallback 触发 warning 但仍返回非空表

3. `filter_by_rs`:
   - threshold=0 passthrough
   - table=None passthrough
   - missing ticker kept-as-missing
   - 边界:rs_percentile == threshold 应通过(≥ 不是 >)

可选:加 `tests/test_main_us_rs_double_gate.py` 集成测试,用 monkeypatch 注入两个表,验证串联调用顺序。但 main.py 既有结构难单元测试(`run_us_eod` 单一巨大函数),性价比不高 — 用手动 dry-run 验证一次即可。

### CLAUDE.md 改动

`## IBD Relative Strength Rating` 节 `### US` 子节大改:

- 标题改为 `### US: Fred6725 CSV (12M, vs SPY) + 本地 3M (vs SPY)`
- 加段落说明双层 gate 的语义("老 leader 且还在领跑")
- 加 `WEIGHTS_3M` 公式和实现指向 `us_rs_3m.py`
- 加 universe / SPY 数据来源说明
- 加触发条件(两个 3M 旋钮都 0 则跳过 batch)
- 加缓存路径 `rs_rating_3m_<date>.csv`
- 加 `min_rs_percentile_3m` / `min_rs_percentile_longs_3m` 配置项
- 更新"All US EOD long-side groups plus US Shorts gate at RS ≥ 90"→「双层 gate(12M ∩ 3M),两层都 ≥ 90」
- "Missing tickers" 段保持("缺表 kept-as-missing"对两层都适用)

`### HK` 子节无改动。

### 文件清单

新建:
- `us_rs_3m.py` — 算法 + 缓存 + filter
- `tests/test_us_rs_3m.py` — 单元测试
- (本文件)`docs/superpowers/specs/2026-05-21-us-3m-rs-double-gate-design.md`

修改:
- `main.py` — RS 闸门 4 个调用点串联 3M 调用;`run_shorts` 签名扩展
- `config.toml` — 加 `min_rs_percentile_3m` 和 `min_rs_percentile_longs_3m`
- `cleanup.py` — 加 `rs_rating_3m_*.csv` 4 天保留 glob
- `CLAUDE.md` — 文档更新

不动:
- `rs_rating.py` — 12M CSV fetcher 完全不变(本期不复用它的失败 fallback,3M 表独立)
- `hk_rs.py` / `hk_eod.py` — 港股侧零改动
- `report/` — 报告生成不读 RS 表
- Futu sync — 不依赖 RS

## 时间成本估算

US universe ~6100 ticker × 6 个月日线 ≈ ~120 行每 ticker。batch=500 → 13 个 batch。yfinance 经验值每 batch 10-30 秒 → **总耗时 2-5 分钟**,加在 us-eod 既有 ~5 分钟基础上,变成 ~7-10 分钟。

launchd 10:00 HKT 槽位充裕(下一个调度事件是 20:00 HKT 港股),时间预算无压力。

## 风险与回退

1. **yfinance 拉取慢 / 超时** — batch 内部 retry 由 yfinance 处理。整批失败 → 单批被跳过,影响该批 500 个 ticker 进不入 3M 表 → 那些 ticker 在 `filter_by_rs` 里 kept-as-missing,不会被错砍。
2. **SPY 拉取失败** — 已有 fallback 路径(`spy_score = 0.0` → 绝对分数排名)。
3. **回退**:把 `min_rs_percentile_3m` 和 `min_rs_percentile_longs_3m` 都设 0 即完全关闭。代码可保留(没有性能影响),后续可重启。

## Open questions / 已确认

- ✅ 算法和 HK 完全一致(`WEIGHTS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63`)
- ✅ Benchmark = SPY(yfinance 拉)
- ✅ Universe = Fred6725 CSV 的 ticker 列(~6100 只)
- ✅ 模块拆分:新建 `us_rs_3m.py`,复制 `hk_rs.py` 逻辑(不复用)
- ✅ 拉取触发:仅当 `max(min_rs_percentile_3m, min_rs_percentile_longs_3m) > 0`
- ✅ 配置形状:两个 3M 旋钮(Leaders / Longs)— 镜像现有 2-tier 结构
- ✅ 百分位计算 universe = 全市场;过滤在分组上分两层串联
