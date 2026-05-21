# US 长线 RS 双闸门 (12月 ∩ 3月)

**日期**：2026-05-21
**作者**：xue
**状态**：Draft — 待用户审阅

## 背景

当前 US 长线流水线（`main.py` / `rs_rating.py`）的所有 RS-gated 组——Longs 五个分支（EarningsGap / HighVolume / GapUp / NewHigh52W / TopGainers）、Leaders、conditional RS 组、Shorts——共用一道 12 月 RS 闸门(实际两个旋钮都设 90)：

```
RS_12M = 0.4·Q1 + 0.2·Q2 + 0.2·Q3 + 0.2·Q4   (vs SPY，IBD 加权)
```

数据来自 Fred6725 / rs-log 每日发布的 `rs_stocks.csv`，主列 `Percentile` 即此分数的全市场百分位（0-99，~6100 只 NYSE/NASDAQ 主板股票）。`[settings] min_rs_percentile = 90`（Leaders）和 `min_rs_percentile_longs = 90`（其余）都设 90。

港股侧（`hk_eod.py` / `hk_rs.py`）2026-05 已经引入 3 月 RS 第二道闸门并稳定运行，语义：「老 leader 且还在领跑」。本期把同样的语义引入 US。

## 目标

引入第二道 3 月 RS 闸门，与 12 月闸门串联（AND）:

```
RS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63   (vs SPY,3M 等权 IBD 风格)
```

应用顺序:先 12M ≥ 阈值砍一刀,再在幸存者上 3M ≥ 阈值砍第二刀。

**3M 闸门的应用范围(收窄)**:仅对 **Leaders、conditional RS 组、Shorts** 三个组生效。**Longs 五个分支(EarningsGap / HighVolume / GapUp / NewHigh52W / TopGainers)保持 12M-only 不变。**

理由:Longs 五个分支已经各自有强事件型闸门(earnings 缺口 / 高 RVol / gap up / 52 周新高 / Top Gainer),12M RS 90 + 这些事件本身已经把候选池缩得很紧;再叠一层 3M 会过度收紧导致 .txt 经常空仓。Leaders / RS / Shorts 这三个组没有同等强度的事件闸门(它们是纯走势型挑选),需要更严格的 RS 双层筛选才能把"老 leader 且还在领跑"的语义压实。

**Fred6725 CSV 里的 `3M_RS_Percentile` 列不可复用**:它表达的是「3 个月前那一天的 12 月 RS 排名」,语义是「持续性 / 不是昙花一现」,不是「短窗口动量」。和港股 3M 闸门含义不一致;本期要的是和港股镜像。

**附带改造:US IPO 阶梯** — 同时把 US IPO 改造为镜像 HK `filter_hk_ipo_candidates` 的"按数据深度分级"过滤器(细节见下方 "US IPO 阶梯" 节)。

**非目标**:
- 不在 Longs 5 splits 上加 3M 闸门(见上方理由)。
- 不引入 1M / 6M 闸门(YAGNI)。
- 不改动 morning-gap(intraday 不需要长线 RS)。

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

仅当 `min_rs_percentile_3m > 0` 时执行：

1. 触发 Fred6725 CSV 拉取（如未触发）→ 拿 universe
2. yfinance batch 拉取 universe + SPY
3. 计算 `rs_3m_table`,缓存到 `output/state/rs_rating_3m_<date>.csv`
4. 在 Leaders / RS / Shorts 三个组上串联应用

`min_rs_percentile_3m = 0` → 完全跳过 yfinance batch 与 `rs_3m_table` 生成,既有 12M 行为不受影响。

### 闸门串联

`main.py` 现有 4 个 `filter_by_rs` 调用点,**只在其中 3 个**(Leaders / RS / Shorts)后面串联 3M 调用。Longs/{key} 调用点不动:

| 调用点 | 既有 12M 阈值 | 新增 3M 阈值 |
|---|---|---|
| Longs/{key}(line ~1490) | `min_rs_percentile_longs` | **不加 — 保持 12M only** |
| Leaders/{name}(line ~1540) | `min_rs_percentile` | `min_rs_percentile_3m` |
| RS group(line ~1606) | `min_rs_percentile_longs` | `min_rs_percentile_3m` |
| run_shorts(line ~355) | `min_rs_percentile_longs`(caller 传入) | `min_rs_percentile_3m`(caller 传入) |

伪代码片段(以 Leaders 为例):

```python
if min_rs_percentile > 0 and tickers:
    tickers = filter_by_rs(tickers, rs_table_12m, min_rs_percentile, f"  [Leaders/{name}]")
if min_rs_percentile_3m > 0 and tickers:
    tickers = us_rs_3m.filter_by_rs(tickers, rs_table_3m, min_rs_percentile_3m)
    logger.info(f"  [Leaders/{name}] {len(tickers)} after RS_3M >= {min_rs_percentile_3m}")
```

`run_shorts` 签名扩展:

```python
def run_shorts(
    ...,
    rs_table: dict[str, int] | None = None,         # 既有 — 12M(caller 传 min_rs_percentile_longs)
    min_rs_percentile: int = 0,                     # 既有 — 形参名遗留;caller 传 min_rs_percentile_longs 的值
    rs_table_3m: pd.DataFrame | None = None,        # 新增 — 3M 表
    min_rs_percentile_3m: int = 0,                  # 新增 — caller 传同名 config 的值
):
```

注意:`run_shorts` 既有形参叫 `min_rs_percentile`,但调用处实际传的是 `min_rs_percentile_longs`(见 `main.py:1577-1579`)— 这是既有命名遗留,本期不改。新加的形参直接叫 `min_rs_percentile_3m`,caller 也传同名的 config 值,无遗留歧义。

### Config

`[settings]` 新增 1 行:

```toml
min_rs_percentile       = 90    # 既有 — Leaders 12M
min_rs_percentile_longs = 90    # 既有 — Longs 5 splits / conditional RS / Shorts 12M
min_rs_percentile_3m    = 90    # 新   — Leaders / conditional RS / Shorts 共用 3M 闸门(本地短窗口 vs SPY)
```

旋钮设 0 的语义(与 HK 一致):
- `min_rs_percentile = 0` → 关闭 Leaders 12M(既有)
- `min_rs_percentile_longs = 0` → 关闭 Longs/RS/Shorts 12M(既有)
- `min_rs_percentile_3m = 0` → 关闭 3M 层(Leaders/RS/Shorts 全部),完全跳过 yfinance batch 和 `rs_rating_3m_<date>.csv` 生成(新)

说明:3M 闸门仅作用于 Leaders/RS/Shorts 三个组;Longs 5 splits 始终只看 12M 不参与 3M 表查询,即使 `min_rs_percentile_3m > 0`。

### 日志格式

延续既有「每次 `filter_by_rs` 一行」的模式,3M 紧跟 12M(仅在 Leaders/RS/Shorts 三个组打印 3M 行):

```
[Leaders/4w] 42 after RS >= 90 (dropped 318, kept-as-missing 7)
[Leaders/4w] 28 after RS_3M >= 90 (dropped 14, kept-as-missing 0)
[Longs/EarningsGap] 38 after RS >= 90 (dropped 412, kept-as-missing 5)
(no RS_3M line for Longs)
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

### US IPO 阶梯

镜像 `filter_hk_ipo_candidates`,过滤美股 IPO 候选(`ipo_drops` 集合)。新建 `us_ipo.py`(纯函数 + 单元测试,与 hk_eod 的 IPO 段落同构),签名:

```python
def filter_us_ipo_candidates(
    klines: dict[str, pd.DataFrame],          # yfinance 拉取的 IPO 子集 k 线
    finviz_caps: dict[str, float],            # 从 Finviz 屏拉过程中捕获的市值(USD)
    rs_table_3m_full: pd.DataFrame | None,    # 含 raw scores 列,可对 IPO 重排 percentile
    spy_kline: pd.DataFrame | None,           # 用于本地 score 计算
    settings: dict,
) -> tuple[list[str], dict[str, int]]:
    """阶梯(同 ticker 顺序走,命中任一即 drop 并计数):
      - len(df) < 20                   → drops['min_history']
      - cap < $300M                    → drops['cap']      (Finviz cap 兜底)
      - price < $10                    → drops['price']
      - if len(df) >= 20 with metrics:
          avg_vol_20d < 500K           → drops['avg_vol']
          avg_dollar_vol_20d < $100M   → drops['dvol']
          adr_pct < 4.0%               → drops['adr']
      - if len(df) >= 50: not above SMA50  → drops['sma50']
      - if len(df) >= 200: not above SMA200 → drops['sma200']
      - if len(df) >= 64 and rs_table_3m_full is not None and threshold > 0:
          compute IPO 3M score vs SPY via _score_from_kline(WEIGHTS_3M);
          percentile-rank against rs_table_3m_full['raw_score'] distribution;
          rs_pct < min_rs_percentile_3m → drops['rs_3m']
    """
```

阈值取自 `[settings]`(US Longs baseline,与 Finviz 屏过滤一致):

| 字段 | 取值 | 出处 |
|---|---|---|
| `min_market_cap` | $300M | `[settings]` 新增(对齐 `cap_smallover`)|
| `min_price` | $10 | `[settings]` 新增(对齐 `sh_price_o10`)|
| `min_avg_volume` | 500K | `[settings]` 新增(对齐 `sh_avgvol_o500`)|
| `min_dollar_volume` | $100M | `[settings]` 既有 |
| `min_adr_percent` | 4.0 | `[settings]` 既有 |
| `min_rs_percentile_3m` | 90 | 本期新增 |

`min_market_cap` / `min_price` / `min_avg_volume` 当前在 US `[settings]` 不存在(Finviz 屏自身已经过滤过)。本期作为 IPO 阶梯专用知识引入到 `[settings]`,方便后续也复用到其他场景。

**集成流程**(`main.py` Write IPO list 段落 line ~1676-1701):

```python
# 1. 旧的 RS-history 兜底剔除("transient yfinance gaps, not IPOs")保持不变
ipo_drops -= non_ipo

# 2. yfinance batch 拉取 IPO 子集 k 线(period="1y" 足够覆盖 200-day SMA)
ipo_klines = fetch_yfinance_klines(sorted(ipo_drops), period="1y")  # ~50 ticker, 1 batch

# 3. 应用阶梯
sorted_ipo, drops = filter_us_ipo_candidates(
    klines=ipo_klines,
    finviz_caps=ipo_finviz_caps,   # 在屏拉过程中已捕获
    rs_table_3m_full=rs_table_3m_full,
    spy_kline=spy_kline,           # 已在 us_rs_3m 模块中拉取
    settings=settings,
)
logger.info(f"[IPO] {len(sorted_ipo)} kept; drops={drops}")

# 4. 老的 cross-day master dedup
sorted_ipo = _dedup_seen("[IPO]", sorted_ipo, ipo_seen, ipo_seen_path)

# 5. 写 .txt / Webull / Futu(不变)
```

**`finviz_caps` 捕获**:`run_screener` 内部既有 `parse_number(stock["Market Cap"])` 逻辑,本期把它额外存到一个传入的 dict(同 `ipo_drops` set 的传入模式)。改动小,不影响主流程。

**3M RS percentile for IPO**:核心问题是 IPO 候选不在 Fred6725 universe(< 120 天历史)。解决方案:`us_rs_3m.compute_us_rs_3m_table` 返回的 DataFrame 同时包含 `rs_percentile` 列(既有需求)和 `raw_score` 列(新增)— 缓存文件也存这两列。IPO 阶梯里:
- 计算 IPO 候选的 3M raw_score(用同一份 SPY 数据)
- 通过 `np.searchsorted(sorted Fred6725 raw_scores, ipo_score)` 在 Fred6725 分数分布上反查百分位
- 阈值 ≥ 90 比较即可

这等价于"如果这个 IPO 跟 Fred6725 的 6100 只股票一起排,它会排第几"。语义清晰,不影响 Fred6725 内部的 percentile 排名。

**失败模式**:
- yfinance batch 整体失败 → `ipo_klines` 空 → 阶梯全部命中 `min_history` → IPO.txt 当日空仓 + warning(可接受 — 比错放进虚假候选更安全)
- `rs_table_3m_full` 为 None(主层 3M 拉取失败)→ 跳过 3M 闸门,其余阶梯正常运行(passthrough,镜像 HK)
- IPO 候选不在 `finviz_caps`(理论上不应该发生 — `ipo_drops` 一定来自 Finviz 屏)→ drops['cap'] 计数(安全侧)

**测试**:`tests/test_us_ipo.py` 镜像 `tests/test_hk_ipo.py`(若存在),纯逻辑覆盖每个 drop bucket。

### Cache 与 cleanup

- 新缓存路径 `output/state/rs_rating_3m_<date>.csv`,文件 schema: `ticker,raw_score,rs_percentile`(`raw_score` 列是新增,IPO 阶梯反查百分位用;`hk_rs_rating_3m_*.csv` 现在只有 `rs_percentile`,本期不强制 HK 同步加 `raw_score`,但若改 HK 也加上有助于未来 HK IPO 阶梯演化)。
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

### CLAUDE.md 改动 — IPO 节

`## Architecture` 节 `**IPO** (no config)` 子项,从"自动收集 sidecar,无过滤"改为"自动收集 sidecar + 阶梯过滤":

- 加 ladder 描述(20 天 floor → cap/price → 20d metrics → SMA50/200 → 3M RS)
- 加阈值清单与 `[settings]` 配置项(`min_market_cap` / `min_price` / `min_avg_volume`)
- 加缓存 / 失败模式说明
- 保留既有的 cross-day master / Futu group / 假 IPO 兜底语义

### CLAUDE.md 改动 — RS 节

`## IBD Relative Strength Rating` 节 `### US` 子节大改:

- 标题改为 `### US: Fred6725 CSV (12M, vs SPY) + 本地 3M (Leaders/RS/Shorts only, vs SPY)`
- 加段落说明 3M 层仅作用于 Leaders/RS/Shorts 三个组,Longs 5 splits 保持 12M-only;附决策理由(Longs 已有强事件型闸门,叠 3M 会过度收紧)
- 加 `WEIGHTS_3M` 公式和实现指向 `us_rs_3m.py`
- 加 universe / SPY 数据来源说明
- 加触发条件(`min_rs_percentile_3m = 0` 则跳过 batch)
- 加缓存路径 `rs_rating_3m_<date>.csv`
- 加 `min_rs_percentile_3m` 配置项
- 更新"All US EOD long-side groups plus US Shorts gate at RS ≥ 90"→「12M ≥ 90 对全部 RS-gated 组生效;3M ≥ 90 额外作用于 Leaders/RS/Shorts」
- "Missing tickers" 段保持("缺表 kept-as-missing"对两层都适用)

`### HK` 子节无改动。

### 文件清单

新建:
- `us_rs_3m.py` — 3M RS 算法 + 缓存 + filter(含 `raw_score` 列)
- `us_ipo.py` — `filter_us_ipo_candidates` 阶梯函数
- `tests/test_us_rs_3m.py` — 单元测试
- `tests/test_us_ipo.py` — 单元测试
- (本文件)`docs/superpowers/specs/2026-05-21-us-3m-rs-double-gate-design.md`

修改:
- `main.py` — RS 闸门 3 个调用点(Leaders/RS/Shorts)串联 3M 调用;`run_shorts` 签名扩展;IPO 段落串联 `filter_us_ipo_candidates`;`run_screener` 捕获 Finviz caps 到外部 dict
- `config.toml` — 加 `min_rs_percentile_3m` / `min_market_cap` / `min_price` / `min_avg_volume`(`min_dollar_volume` / `min_adr_percent` 已有)
- `cleanup.py` — 加 `rs_rating_3m_*.csv` 4 天保留 glob
- `CLAUDE.md` — 文档更新(RS 节 + IPO 节)

不动:
- `rs_rating.py` — 12M CSV fetcher 完全不变
- `hk_rs.py` / `hk_eod.py` — 港股侧零改动
- `report/` — 报告生成不读 RS 表
- Futu sync — 不依赖 RS

## 时间成本估算

- **3M RS 主层** — US universe ~6100 ticker × 6 个月日线 ≈ ~120 行每 ticker。batch=500 → 13 个 batch。yfinance 经验值每 batch 10-30 秒 → ~2-5 分钟
- **IPO 阶梯** — `ipo_drops` 典型 ~30-80 ticker × 1 年日线 → 1 个 batch → ~10-30 秒
- **总计**:加在 us-eod 既有 ~5 分钟基础上,变成 ~7-10 分钟

launchd 10:00 HKT 槽位充裕(下一个调度事件是 20:00 HKT 港股),时间预算无压力。

## 风险与回退

1. **yfinance 拉取慢 / 超时** — batch 内部 retry 由 yfinance 处理。整批失败 → 单批被跳过,影响该批 500 个 ticker 进不入 3M 表 → 那些 ticker 在 `filter_by_rs` 里 kept-as-missing,不会被错砍。
2. **SPY 拉取失败** — 已有 fallback 路径(`spy_score = 0.0` → 绝对分数排名)。
3. **回退**:把 `min_rs_percentile_3m` 设 0 即完全关闭 3M 层。代码可保留(没有性能影响),后续可重启。

## Open questions / 已确认

- ✅ 算法和 HK 完全一致(`WEIGHTS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63`)
- ✅ Benchmark = SPY(yfinance 拉)
- ✅ Universe = Fred6725 CSV 的 ticker 列(~6100 只)
- ✅ 模块拆分:新建 `us_rs_3m.py`,复制 `hk_rs.py` 逻辑(不复用)
- ✅ 拉取触发:仅当 `min_rs_percentile_3m > 0`
- ✅ 3M 闸门应用范围:Leaders + conditional RS 组 + Shorts(Longs 5 splits **不** 加 3M,保持 12M-only)
- ✅ 配置形状:单一旋钮 `min_rs_percentile_3m = 90` 覆盖三个组
- ✅ 百分位计算 universe = 全市场;过滤在分组上分两层串联
- ✅ US IPO 阶梯镜像 HK:20-day floor + cap/price + avg_vol/$vol/ADR + SMA50/200 + 3M RS
- ✅ IPO ADR 阈值 = 4.0%(US Longs baseline,与 HK 的 3.5% 不同)
- ✅ IPO 3M RS percentile 通过反查 Fred6725 raw score 分布得到
