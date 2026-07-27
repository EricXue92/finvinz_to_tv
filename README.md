# 每日选股扫描流水线 (Daily Stock Screener Pipeline)

多数据源的动量 (momentum) 与做空 (short) 筛选器(美股走 Finviz,盘中缺口走 Futu 快照),输出可导入 TradingView / Webull 的自选列表,通过 OpenAPI 自动同步到 Futu(富途牛牛)自定义分组(也可选同步到 TradingView 列表,走其非官方 REST API),并生成每日 CANSLIM 风格的研究简报。方法学基于 Oliver Kell 和 Kristjan Kullamägi。

> **状态 (2026-07-26):** 美股 + 港股。美股用 Finviz + yfinance + 一张 12M IBD RS CSV + 一张 3M RS 表。港股用 yfinance 取 k 线 + HSI 历史(最初的 Futu-only 方案被回滚——Futu 免费/Lv1 档把 12 个月历史覆盖率压到了主板全市场的 ~12%)。Futu 仍负责港股市值、条件 RS 触发用的 HSI 实时日涨幅快照,以及自选同步。**百分位 RS 表(美股 3M、港股 12M+3M)和港股长线侧 metrics frame 现在每天在 GitHub Actions 上算好、以 CSV 形式发布到 `data/` 下;本地流水线只负责拉取**(家用 IP 的 yfinance 计算跑到一半就被限流)。两个市场都对 Leaders/RS/Shorts 用 **12M ∩ 3M RS 双闸**(Longs 5 组保持仅 12M),对不足 12 个月的票用**按历史深度分级的 IPO ladder**。港股流水线跑在自己独立的 20:00 HKT 计划槽(美股跑 10:00 HKT)——两者各写各自的分市场日志。每次 EOD 跑完后,wrapper 脚本还会对该市场调用一次 `--mode report`,为当天新发现的长线侧票生成 CANSLIM Markdown + 独立 HTML 简报(后端可选——默认 DeepSeek V4 + Tavily,Anthropic `web_search` 为备选)。

## 筛选器 (Screeners)

所有基于 Finviz 的扫描都用 `ind_stocksonly` 排除 ETF/ETN。Morning Gap(Futu `stock_type=STOCK`)按构造就只含个股。

### 全局闸门 (long-side)

在 Finviz 屏之后、任何昂贵的 yfinance 工作之前套用。可在 `[settings]` 中配置。

| 闸门                                            | 作用范围                                | 阈值                                                           | 数据源                                                                                                                                                                                                                                                 |
| ----------------------------------------------- | --------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **IBD RS Percentile 12M (Leaders)**             | Leaders                                 | ≥ 90(top 10%;表中缺失的 ticker 保留)                           | [Fred6725/rs-log](https://github.com/Fred6725/relative-strength),`RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` 对 SPY 归一,工作日 ~01:30 UTC 刷新                                                                                                          |
| **IBD RS Percentile 12M (Longs/RS/US Shorts)**  | Longs(5 组)+ RS 组 + US Shorts          | ≥ 90(top 10%;缺失 ticker 保留)                                 | 同上                                                                                                                                                                                                                                                   |
| **IBD RS Percentile 3M (Leaders/RS/US Shorts)** | Leaders + RS 组 + US Shorts(不含 Longs) | ≥ 90(top 10%;缺失 ticker 保留)                                 | `RS_3M = 0.5·R21 + 0.3·R42 + 0.2·R63` 对 SPY,universe = Fred6725 ticker 列表(~6100),**云端在 GitHub Actions 上计算**并发布到 `data/us_rs_3m/<date>.csv`(带 `raw_score` 供 IPO 的 out-of-universe 查排名);`us_rs_3m.py` 负责拉取(拉不到就往回退 ≤ 3 天) |
| **Dollar Volume**                               | Longs + Leaders                         | 价 × 20 日均量 ≥ $100M                                         | yfinance 日线                                                                                                                                                                                                                                          |
| **ADR%**                                        | Longs + Leaders                         | mean(`(High − Low) / Close`) × 100,取最近 20 根完整 bar ≥ 4.0% | yfinance 日线                                                                                                                                                                                                                                          |

**RS 作用范围(两层闸):**

| 分组                                                            | 12M 闸                    | 3M 闸                                                                |
| --------------------------------------------------------------- | ------------------------- | -------------------------------------------------------------------- |
| Longs 5 组 (EarningsGap/HighVolume/GapUp/NewHigh52W/TopGainers) | `min_rs_percentile_longs` | —(仅 12M)                                                            |
| Leaders                                                         | `min_rs_percentile`       | `min_rs_percentile_3m`                                               |
| 条件 RS 组                                                      | `min_rs_percentile_longs` | `min_rs_percentile_3m`                                               |
| US Shorts                                                       | `min_rs_percentile_longs` | `min_rs_percentile_3m`                                               |
| US IPO ladder (≥ 64 天)                                         | —                         | `min_rs_percentile_3m`(用 `np.searchsorted` 对 Fred6725 `raw_score`) |

语义:12M ≥ 90 = "长期领头羊";3M ≥ 90 = "近期仍在领跑"。交集 = "老领头羊还在领跑"。**上面这些 `≥ 90` 是设计意图;权威阈值是 `[settings]` 里的配置值**——每一层都可独立调,任一层设为 `0` 即关闭(例如 Leaders 12M 层的 `min_rs_percentile` 目前是 `0`)。**Longs 5 组保持仅 12M** 是刻意为之——它们本身就有很强的事件过滤(EarningsGap / RVol 放量 / GapUp / 52W 新高 / Top Gainer),再叠 3M 会把 universe 收得过紧。把 `min_rs_percentile_3m = 0` 可关掉整个 3M 层(跳过云端 CSV 拉取)。HK Shorts 和 Morning Gap 不做 RS 闸。

ADR% 取代了旧的 Finviz `beta > 1.5` 过滤——beta 度量的是多年与大盘的相关性,会把当下正在活跃、其实 in-play 的中/大盘 catalyst 票误排掉。ADR%(Kullamägi 式)直接度量一只股票当下是否在动。

### Longs(5 个策略,互斥)

Oliver Kell 的动量/突破 setup。优先级顺序——靠前者胜出,每只 ticker 每天最多出现在一个 Longs 文件里。

| 优先级 | 策略          | Finviz 过滤                                                                                              |
| ------ | ------------- | -------------------------------------------------------------------------------------------------------- |
| 1      | `EarningsGap` | Small Cap+, Earnings Today, Avg Vol > 500K, Price > $10, Rel Vol > 1.5, Gap Up 5%+, Above SMA50 & SMA200 |
| 2      | `HighVolume`  | Small Cap+, Avg Vol > 500K, Price > $10, Day Up, Above SMA50 & SMA200 + yfinance Rel Vol ≥ 3× 20 日均量  |
| 3      | `GapUp`       | Small Cap+, Avg Vol > 500K, Price > $10, Gap Up 3%+, Above SMA50 & SMA200                                |
| 4      | `NewHigh52W`  | Small Cap+, Avg Vol > 500K, Price > $10, New 52W High, Above SMA50 & SMA200                              |
| 5      | `TopGainers`  | Small Cap+, Avg Vol > 500K, Price > $10, Above SMA50 & SMA200, Signal: Top Gainers                       |

这 5 组同样要过全局的 Dollar Volume / ADR% 闸和 IBD RS 12M ≥ 90。**Longs 不加 3M 层**(事件过滤本身已经在选新鲜动量)。

### Leaders(5 个策略,合并)

站上 SMA50 + SMA200 的长期趋势领头羊。五个策略共用同一套基础过滤,只在 perf 窗口上不同。

**基础过滤:** Small Cap+, Avg Vol > 500K, Price > $10, Above SMA50, Above SMA200,外加全局闸 **含 12M ∩ 3M RS 双闸**(阈值见 `[settings]`)。

| 策略              | Performance 阈值  |
| ----------------- | ----------------- |
| Leaders 4W +30%   | 4 周 perf ≥ 30%   |
| Leaders 13W +50%  | 13 周 perf ≥ 50%  |
| Leaders 26W +100% | 26 周 perf ≥ 100% |
| Leaders YTD +100% | YTD perf ≥ 100%   |
| Leaders 52W +150% | 52 周 perf ≥ 150% |

### US Shorts

Kullamägi 抛物线 blow-off setup。两阶段:先做 Finviz Ownership 预过滤,再在一次共享下载上做 yfinance 后处理。

**Phase 1 — Finviz Ownership:** SMA20 +20%, Above SMA50, Avg Vol > 1M(Finviz 3 个月均量), Cap > $300M。然后 **IBD RS 12M ∩ 3M ≥ 90**(在 yfinance batch 之前先砍掉)。

**Phase 2 — yfinance + Futu 市值快照,顺序:performance → dollar volume → ADR% → 连续上涨天数。**

| 过滤                                | 阈值                                     | 数据源                                               |
| ----------------------------------- | ---------------------------------------- | ---------------------------------------------------- |
| 市值(perf 分桶用)                   | 实时 USD 值                              | Futu 快照 `total_market_val` → Finviz Ownership 兜底 |
| Dollar Volume                       | ≥ $100M(20 日均量)                       | yfinance                                             |
| ADR%                                | ≥ 4.0%(20 日)                            | yfinance                                             |
| Performance — Large Cap (≥ $10B)    | 2、3 或 4 周内 Up 50%+                   | yfinance                                             |
| Performance — Mid Cap ($2B–$10B)    | 2、3 或 4 周内 Up 200%+                  | yfinance                                             |
| Performance — Small Cap ($300M–$2B) | 2、3 或 4 周内 Up 300%+                  | yfinance                                             |
| 连续上涨天数                        | ≥ 3 个绿天(若开市则排除今日未完成的 bar) | yfinance                                             |

市值取自 Futu(无截断),而非 Finviz 那种粗糙的 `"6.96M"`/`"1.23B"` 字符串——后者在 $2B / $10B 边界附近容易分错桶。

### RS — Relative Strength(条件触发)

Oliver Kell 的相对强度打法。**只有当 SPY _和_ QQQ 当日都跌 ≥ 1.2% 时才运行**(`check_market_down`;阈值在代码里)——挑出在弱市里扛住的股票。

过滤:Small Cap+, Avg Vol > 500K, Price > $10, Day Up, Above SMA50 & SMA200, Dollar Volume ≥ $100M, ADR% ≥ 4.0%, **IBD RS 12M ∩ 3M ≥ 90**(双闸)。

### RS-line 趋势标注(仅日志)

云端脚本发布 `rs_below_ma` / `rs_days_below_ma` / `rs_frac_below_ma`(TraderLion 式 **RS line** = 价 ÷ 基准 vs 它自己的 EMA21)进 `data/{us_rs_3m,hk_rs}/<date>.csv`。EOD 日志会*标注*那些 RS line 持续在其均线下方(走弱)的长线侧 survivors。这是**仅日志**(不影响 `.txt` / dedup);跨日 master 的手动裁剪走 `--mode rs-line-audit`。配置:`[rs_line]`。

### IPO(自动收集的 sidecar)

那些通过了某个 Longs/Leaders/RS Finviz 屏、但被 yfinance 因日线历史不足而丢弃的长线候选——典型就是最近几个月内 IPO 的票。候选集再过一条按历史深度分级的 ladder(镜像 HK `filter_hk_ipo_candidates`,实现在 `us_ipo.filter_us_ipo_candidates`),这样第 30 天的 IPO 仍能浮现,而第 200 天的 IPO 被卡到几乎完整的长线基线:

| 闸门         | 阈值                             | 条件                                          |
| ------------ | -------------------------------- | --------------------------------------------- |
| min history  | ≥ 20 交易日                      | 总是(砍掉第 1-19 天——量太吵)                  |
| cap          | ≥ $300M                          | 总是(cap 来自 screener pass 时抓的 Finviz 值) |
| price        | ≥ $10                            | 总是                                          |
| avg vol      | ≥ 500K 股/天                     | 仅当 ≥ 20 天                                  |
| $vol         | ≥ $100M                          | 仅当 ≥ 20 天                                  |
| ADR%         | ≥ 4.0%                           | 仅当 ≥ 20 天                                  |
| above SMA50  | —                                | 仅当 ≥ 50 天                                  |
| above SMA200 | —                                | 仅当 ≥ 200 天                                 |
| 3M RS        | ≥ 90(对 Fred6725 raw_score 分布) | 仅当 ≥ 64 天                                  |

阈值对齐 US Longs 基线,历史攒满后能无缝晋升。3M RS 闸比较特殊:IPO 候选不在 Fred6725 universe 里(< 120 天),故 ladder 在本地算它的分数,再用 `np.searchsorted` 对 Fred6725 `raw_score` 分布排名——"这只 IPO 如果今天加入 universe 会排在哪"。当 `min_rs_percentile_3m = 0` 时整个 RS 闸跳过。

- 输出:`output/TV/US/<date>_IPO.txt` + Webull 镜像 + Futu 分组 `IPO`
- 跨日 master:`output/state/eod_seen_IPO.txt`(独立于 `eod_seen_US.txt`,这样一只晋升的 ticker 会在第一个合格日落进它本该的长线侧分组)
- 保护:出现在 12M Fred6725 RS 表里的 ticker 有 ≥ 12mo 历史、不可能是新 IPO——这类丢弃会被标为瞬时的 yfinance 缺口,在 ladder 之前从 IPO 桶里排除。

### HK Shorts

方法学与 US Shorts 相同,数据源为 HKEX 股票列表(~2,400 只主板股)+ yfinance,阈值用 HKD 原生(cap ≥ HKD 300M,avg vol ≥ 1M 股/天 [独有下限——长线侧用 500K],dollar volume ≥ HKD 100M,ADR% ≥ 4.0%,按 HKD 10B / 2B / 300M 市值桶对应 perf 50/200/300%,连续 3+ 上涨天数;输出 `HKEX:NNN` 格式、去掉前导零)。2026-05-06 重新启用。

### HK 长线侧:EarningsGap / HighVolume / GapUp / Leaders / RS

五个策略,数据源为 **yfinance**(k 线 + HSI)加 **Futu**(市值 + HSI 实时日涨幅)。最初的方案是 Futu-only,但 Futu 免费/Lv1 档把 12 个月历史覆盖率压到了主板全市场的 ~12%——IBD 12 个月 RS 算法没东西可排——于是长线 k 线改走 yfinance,后者对几乎每只 ticker 都能稳定给出 2+ 年数据。方法学对齐 US Longs/Leaders/RS,阈值用 HKD 原生。Universe = HKEX 主板股(~2,400)。输出:`output/TV/HK/<date>_{EarningsGap,HighVolume,GapUp,Leaders,RS}.txt`,`HKEX:NNN` TradingView 格式(去前导零——TV 会静默拒绝 `HKEX:0148` 这种)。

**统一基线 (`[hk_settings]`):**

| 闸门                 | 阈值                  | 备注                                                                                                            |
| -------------------- | --------------------- | --------------------------------------------------------------------------------------------------------------- |
| Market Cap           | ≥ HK$300M             | 对小盘友好;HK 流动性约比美股薄 10×                                                                              |
| Avg Volume           | ≥ 500K 股/天(20 日)   | 对齐 US `sh_avgvol_o500`                                                                                        |
| Dollar Volume        | ≥ HK$100M(20 日)      | 与 HK Shorts 持平                                                                                               |
| ADR%                 | ≥ 3.5%(20 日)         | 从 4.0% 调低;HK 蓝筹波动率结构性偏低                                                                            |
| Last Price           | ≥ HK$20               | HK 原生(`min_price`)                                                                                            |
| Above SMA50 & SMA200 | 两者                  | 对齐 US `ta_sma50_pa` + `ta_sma200_pa`,套在每个长线侧过滤上                                                     |
| RS Percentile        | 12M ∩ 3M 双闸(vs HSI) | **12M ≥ 80, 3M ≥ 90**(`min_rs_percentile_longs` / `min_rs_percentile_longs_3m`);IBD 算法 vs HSI,非 Fred6725 CSV |

**各策略闸门**(优先级顺序——靠前者胜出,每只 ticker 每天最多出现在一个 HK 长线侧文件里)。五个都继承上面的统一基线(现在含 SMA50 & SMA200 趋势过滤),故下面列的附加闸门是叠加在其之上:

| 优先级 | 策略           | 附加闸门                                                                     |
| ------ | -------------- | ---------------------------------------------------------------------------- |
| 1      | HK EarningsGap | gap ≥ 3% + RVol ≥ 3(形态代理——HK 无财报日历)                                 |
| 2      | HK HighVolume  | RVol ≥ 3                                                                     |
| 3      | HK GapUp       | gap ≥ 5%                                                                     |
| 4      | HK Leaders     | 满足任一(4w +30 / 13w +50 / 26w +100 / YTD +100 / 52w +150)                  |
| 5      | HK RS          | 基线之外无附加;**条件触发**——仅当 HSI 日涨幅 ≤ −1.2%(`hsi_rs_trigger`)时运行 |

**HK RS 算法**:与美股同样的 `0.4·R3 + 0.2·R6 + 0.2·R9 + 0.2·R12` 加权季度收益公式(再加一层 3M),基准换成 HSI(`^HSI`),百分位在 HK 主板 universe 上排名。**云端在 GitHub Actions 上计算**(12M + 3M + RS-line 列合成一张 CSV)并发布到 `data/hk_rs/<date>.csv`;`hk_rs.py` 负责拉取并拆分(拉不到就往回退 ≤ 3 天)。HK 长线侧 **metrics frame** 同样云端发布到 `data/hk_metrics/`,通过 `hk_metrics.build_hk_metrics_cloud` 拉取,云端拉不到时回退到本地实时 yfinance 抓取。

**OpenD 软依赖**:HK 长线侧 k 线 + HSI 历史来自 yfinance,所以 OpenD 挂掉不会清空 .txt 文件。OpenD 挂掉时:市值变 NaN(cap≥HK$300M 基线会把所有票砍掉),条件 RS 的 HSI 触发快照跳过,Futu 同步跳过——但排序与写文件逻辑本身能跑完。OpenD 在线时流水线完整填充。每个策略写进它自己的 append-only Futu 分组(`HKEarningsGap`、`HKHighVolume`、`HKGapUp`、`HKLeaders`、`HKRS`)——首次运行前须在 Futu PC 客户端手动建好。

### HK IPO(自动收集的 sidecar)

镜像 US IPO sidecar。HKEX 主板 universe 里 yfinance 返回了、但 `< 253 行日线收盘`(不足以做 IBD 12 个月 RS 计算)的 ticker——几乎都是刚进 yfinance、还没攒满 12 个月数据的 HK 新股。

- **基线按历史深度分级条件触发。** 每个闸门只在 ticker 攒够数据时才启用——真正第 1 天的 IPO 仍能浮现,而 200 天大的 IPO 被卡到几乎完整的长线基线:

  | 闸门         | 阈值         | 条件                          |
  | ------------ | ------------ | ----------------------------- |
  | cap          | ≥ HK$300M    | 总是                          |
  | price        | ≥ HK$20      | 总是                          |
  | avg vol      | ≥ 500K 股/天 | 仅当 ≥ 20 交易日              |
  | $vol         | ≥ HK$100M    | 仅当 ≥ 20 交易日              |
  | ADR%         | ≥ 3.5%       | 仅当 ≥ 20 交易日              |
  | above SMA50  | —            | 仅当 ≥ 50 交易日              |
  | above SMA200 | —            | 仅当 ≥ 200 交易日             |
  | RS           | —            | 总是跳过(按定义就是 sub-12mo) |

  ADR 阈值对齐长线侧的 3.5% 下限——让 IPO 基线与 HK 长线侧其余部分一致,历史攒到 253 行时晋升无缝。

- **输出:** `output/TV/HK/<date>_IPO.txt`,镜像到 Webull。
- **独立跨日 master:** `output/state/eod_seen_HKIPO.txt`。一旦 IPO 攒到 ≥253 行,它就从 IPO 桶里掉出、在第一个合格日落进本该的长线侧分组(长线侧 master `eod_seen_HK.txt` 是分开的,不交叉污染)。
- **Futu 分组:** append-only `HKIPO`——首次运行前须在 Futu PC 客户端手动建好。

### Morning Gap(盘前 + 盘中,每日 9 次扫描)

两阶段盘中缺口扫描器。**盘前(-20/-10/-5 分钟)**写 `MorningGapPre.txt`。**盘后(+5/+10/+15/+20/+25/+30 分钟)**写 `MorningGap.txt`,并加一层盘中累计量闸门——挑出在开盘头 30 分钟就已经把当日全天均量跑完的股票,这是 Kullamägi 判断 catalyst 驱动的机构买入的信号。

**Phase 1 — Futu 快照 discovery(取代 Finviz `ta_topgainers`——后者按常规盘 perf 排名、错过盘前 gapper):**

| 过滤       | 阈值                                                | 数据源                     |
| ---------- | --------------------------------------------------- | -------------------------- |
| Universe   | NASDAQ / NYSE / AMEX, listed, `stock_type = STOCK`  | Futu `get_stock_basicinfo` |
| Market Cap | ≥ $300M                                             | `total_market_val`         |
| Price      | ≥ $10                                               | `last_price`               |
| Gap(盘前)  | `pre_change_rate` ≥ 5%(且 `pre_volume > 0`)         | `pre_change_rate`          |
| Gap(盘后)  | `(last_price − prev_close) / prev_close × 100` ≥ 5% | 由快照推导                 |

**Phase 2 — yfinance 后处理 + Futu 盘中量:**

| 过滤             | 阈值                                     | 盘前 | 盘后 |
| ---------------- | ---------------------------------------- | ---- | ---- |
| Dollar Volume    | ≥ $100M(20 日均量)                       | ✓    | ✓    |
| ADR%             | ≥ 4.0%(20 日)                            | ✓    | ✓    |
| SMA50 / SMA200   | 最新收盘站上两者                         | ✓    | ✓    |
| 20 日 Avg Volume | ≥ 500K 股/天                             | ✓    | ✓    |
| 盘中累计量       | 自 9:30 ET 起的 RTH 累计量 ≥ 20 日均日量 | —    | ✓    |

需要 FutuOpenD 运行,且有 US Lv1 BBO 实时报价权限。没有它,Phase 1 discovery 和盘后量过滤都返回空——没有 Finviz 兜底。每次扫描若浮现*新*票(今天早些扫描里没见过的)会推一条 ntfy 通知。

## 每日 CANSLIM 报告

每次 EOD 跑完后,`--mode report --market {us,hk}` 读取当天带日期的长线侧 `.txt` 文件,按分组优先级排序,每市场上限 30 只,调用所配置的 LLM 后端为每只 ticker 生成 CANSLIM 风格的基本面 + 展望简报。输出:`output/Reports/<date>_{us,hk}.md` 和一个自包含的 `<date>_{us,hk}.html`(内联 CSS、无外部依赖——双击即可在任何浏览器打开)。

**后端(`[report] backend`,大小写不敏感;两者都走 Anthropic Python SDK):**

| 后端                              | Web 上下文                             | 模型                                  | 密钥                                  |
| --------------------------------- | -------------------------------------- | ------------------------------------- | ------------------------------------- |
| `deepseek`(**shipped 默认**)      | 手动 tool-loop → Tavily 搜索           | `deepseek-v4-pro`(Anthropic 兼容端点) | `DEEPSEEK_API_KEY` + `TAVILY_API_KEY` |
| `anthropic`(密钥未设时的代码兜底) | 原生 `web_search_20250305` server tool | `claude-sonnet-4-6`                   | `ANTHROPIC_API_KEY`                   |

| 方面              | 细节                                                                                                                                                                                                                                                                                    |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **输入 (US)**     | 8 个带日期文件:`EarningsGap`, `HighVolume`, `Leaders`, `GapUp`, `NewHigh52W`, `IPO`, `TopGainers`, `RS`                                                                                                                                                                                 |
| **输入 (HK)**     | 6 个带日期文件:`EarningsGap`, `HighVolume`, `Leaders`, `GapUp`, `IPO`, `RS`(无 NewHigh52W / TopGainers)                                                                                                                                                                                 |
| **上限 & 优先级** | 30 只/市场(`MAX_TICKERS_PER_REPORT`);`EarningsGap > HighVolume > Leaders > GapUp > NewHigh52W > IPO > TopGainers > RS`。溢出的列在 "Truncated" 尾部小节。                                                                                                                               |
| **结构化字段**    | yfinance:Market Cap, Price, EPS(最新季 + YoY), Revenue(最新季 + YoY), **3 年年度 YoY**(两者), PE, ROE, Inst. Hold %, 最近财报日。RS 百分位取自缓存的 IBD/HSI 表。                                                                                                                       |
| **定性小节**      | 模型生成,每只 ticker 最多 2 次 web 搜索(`web_search_max_uses` / `max_search_calls`):公司速览, 基本面/财报, 竞争力, 政策/政府支持, 新产品/催化剂, 风险点, 综合判断。                                                                                                                     |
| **双语**          | 快照字段保持英文/数字;定性分析用简体中文。                                                                                                                                                                                                                                              |
| **软失败**        | 与 Futu-sync 契约一致——wrapper 退出码只反映 EOD 步。缺后端密钥(DeepSeek 缺 `DEEPSEEK_API_KEY`/`TAVILY_API_KEY`,Anthropic 缺 `ANTHROPIC_API_KEY`)→ 该步跳过并 warning,`.txt` 产物不受影响。4xx 配置错误快速失败,给出独立的 `[配置错误]` 占位;5xx/429/超时重试一次后回退到 `[分析失败]`。 |
| **排除**          | US Shorts, HK Shorts, Morning Gap——技术/盘中打法,基本面不驱动入场。                                                                                                                                                                                                                     |
| **成本区间**      | DeepSeek + Tavily ~$0.5/天/市场(默认,便宜约 80%);Anthropic 原生 `web_search` ~$1–2/天/市场。                                                                                                                                                                                            |

**配置:** 把后端密钥放进 `.env`(拷 `.env.example`)——默认 DeepSeek 后端需 `DEEPSEEK_API_KEY` + `TAVILY_API_KEY`,Anthropic 后端需 `ANTHROPIC_API_KEY`。wrapper 脚本(`scripts/run_eod.sh` / `scripts/run_hk_eod.sh`)在报告步之前 `source .env`;交互式运行时 `report/state.py` 也会自动从项目根加载 `.env`。

### 盘前 catalyst 报告

一个**独立**的短报告,当盘前扫描发现新的 US gapper 时从 morning-gap 路径 **spawn 出的 detached 子进程**(`[morning_gap_catalyst]`)。它绝不能阻塞 morning-gap 进程,无论 `[report] backend` 设成什么都**固定用 DeepSeek + Tavily**,且只读 JSON 快照 sidecar(不调 Futu / yfinance)。输出:`output/Reports/<date>_us_premarket.md`,跨 −20/−10 两次扫描追加(上限 `max_tickers_per_run`,默认 10;每只 ticker 最多 `max_search_calls` = 3 次搜索)。

## Dedup(去重)

- **Longs 内部** — 5 个策略互斥(优先级 `EarningsGap > HighVolume > GapUp > NewHigh52W > TopGainers`)。
- **跨组** — 长线侧优先级 `Longs > Leaders > RS`。
- **跨日 master** — `output/state/eod_seen_{US,IPO}.txt`。每只 ticker 首次出现时进且仅进一个长线侧分组;后续运行只发*新*票。IPO 有自己的 master,这样一只晋升的票之后能出现在它本该的分组。删文件即重置。
- **不进跨日 master**:Shorts, Morning Gap。对它们来说重新检测才有意义。

## 输出 (Output)

```
output/
├── TV/                        # 逗号分隔,供 TradingView "Import list..."
│   ├── US/<date>_{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers,Leaders,Shorts,RS,IPO,MorningGapPre,MorningGap}.txt
│   └── HK/<date>_{EarningsGap,HighVolume,GapUp,Leaders,Shorts,RS,IPO,HKMorningGap}.txt
├── Webull/                    # 换行分隔镜像,供 Webull "Upload as File"
│   ├── US/<date>_*.txt
│   └── HK/<date>_*.txt
├── Reports/                   # 每日 CANSLIM 简报(Markdown + 独立 HTML)+ 盘前 catalyst 报告
│   ├── <date>_{us,hk}.{md,html}
│   └── <date>_us_premarket.md
└── state/                     # 跨日 "seen" master、RS 表缓存、morning-gap 每日 seen
    ├── eod_seen_US.txt        # US 长线侧 master(5 Longs 组 + Leaders + RS)
    ├── eod_seen_HK.txt        # HK 长线侧 master(EarningsGap/HighVolume/GapUp/Leaders/RS)
    ├── eod_seen_IPO.txt       # US IPO sidecar(独立——就绪时晋升进 US 分组)
    ├── eod_seen_HKIPO.txt     # HK IPO sidecar(独立——就绪时晋升进 HK 分组)
    ├── morning_gap_seen_{pre,post}_<date>.txt  # 每日 MorningGap 去重(盘前/盘后,每日自动重置)
    ├── rs_rating_<date>.csv         # US 12M IBD RS 百分位缓存(来自 Fred6725/rs-log)
    ├── rs_rating_3m_<date>.csv      # US 3M RS 云端 CSV 的本地缓存(raw_score + 百分位, vs SPY)
    └── hk_rs_rating_<date>.csv      # HK RS 云端 CSV 的本地缓存(12M + 3M, vs HSI)
```

每次运行都为每个分组写一个全新的带日期 `.txt`(为空时写 0 字节文件)。结果为空时 Futu 同步会**跳过**,这样休市日不会清掉已有分组。

**TradingView ticker 格式:** US 分组用 `NASDAQ:AAPL` / `NYSE:WMT` / `AMEX:GLD`(Finviz 派生)。HK 分组用 `HKEX:NNN` 并**去掉前导零**——TradingView 会静默拒绝 `HKEX:0148` 这种,必须是 `HKEX:148`。≥ 1000 的代码(4 位)原样写:`HKEX:1810`(小米)、`HKEX:9988`(阿里)。< 1000 的代码去掉补位:`HKEX:148`(凯基)、`HKEX:522`(ASMPT)、`HKEX:700`(腾讯)。

## Futu 自动同步

在 `config.toml` 配 `[futu]`。同步 hook 在每次自选写成功后触发——失败只 warning,绝不阻塞 `.txt` 输出。

**一次性设置:**

1. 启动 [FutuOpenD](https://openapi.futunn.com/futu-api-doc/intro/intro.html),登录(默认 `127.0.0.1:11111`)。
2. 在 Futu PC 客户端手动建这些自定义分组(API 只能改已存在的分组,不能新建):
   `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`, `Leaders`, `Shorts`, `RS`, `IPO`(US)。

多数 EOD 分组是 append-only——太满时在客户端手动清空(Futu 上限:非交易户 500/组,活跃交易户 2000)。

## TradingView 自动同步(可选,`tv_sync.py`)

`[tv_sync]`(默认 **`enabled = false`**)把同一批自选同步到 TradingView 列表,走其**非官方 REST API**,用 `sessionid` cookie 认证。凭证读取顺序:先 env(`TV_SESSIONID`, `TV_SESSIONID_SIGN`),再 `~/.config/momentum-scanner/tv_cookie.json`。18 个列表须先在 TV 网页手动建好(名字区分大小写、精确匹配)——找不到的名字会 warning 并跳过该列表。与 Futu 同样的软失败契约(cookie 过期绝不阻塞 `.txt` 输出)。Append-only 语义镜像 `[futu].append_only_groups`;注意 TV 把 `MorningGap` 保留为独立列表(Futu 那边并入 `EarningsGap`)。

## 推送通知 (ntfy)

Morning-gap 扫描在**新**票(今天早些扫描里没见过的)出现时推一条 [ntfy.sh](https://ntfy.sh) 通知。在 `config.toml` 配 `[notify]`;在 ntfy iOS/Android app 里订阅该 topic。

## 安装 (Setup)

```bash
uv sync                                              # 安装
uv run main.py --mode us-eod                         # US EOD (Longs/Leaders/Shorts/RS/IPO)
uv run main.py --mode hk-eod                         # HK EOD (Shorts + Longs/Leaders/RS)
uv run main.py --mode morning-gap                    # US 盘中缺口扫描(自动检测窗口,窗口外干净退出)
uv run main.py --mode hk-morning-gap                 # HK 盘中缺口扫描(仅盘后)
uv run main.py --mode report --market us             # 为今日 US 票生成 CANSLIM 简报(需后端密钥)
uv run main.py --mode report --market hk --date YYYY-MM-DD   # 回填某一天
uv run main.py --mode rs-line-audit --market both    # 按 RS-line 趋势给跨日 master 打分,提示裁剪(手动)
```

> `--mode eod`(裸)仍会 US + HK 一起跑,但计划槽用分市场的 `us-eod` / `hk-eod`(10:00 HKT 时 HK bar 未完成)。

## 自动化(macOS launchd + pmset)

两个每日 EOD 槽(按收盘拆分)、两个盘中 morning-gap 扫描器、两个 RS-workflow 自触发。各写各自的日志到 `output/` 下:

| 槽             | 触发                                 | Mode             | Plist                                         |
| -------------- | ------------------------------------ | ---------------- | --------------------------------------------- |
| US EOD         | Tue–Sat 10:00 HKT                    | `us-eod`         | `com.xue.finviz-to-tv.plist`                  |
| HK EOD         | Mon–Fri 20:00 HKT                    | `hk-eod`         | `com.xue.finviz-to-tv.hk-eod.plist`           |
| US Morning Gap | 90 条/周(ET-aware)                   | `morning-gap`    | `com.xue.finviz-to-tv.morning-gap.plist`      |
| HK Morning Gap | Mon–Fri × 6 offsets(9:40–10:30 HKT)  | `hk-morning-gap` | `com.xue.finviz-to-tv.hk-morning-gap.plist`   |
| US RS trigger  | Tue–Sat 08:45 HKT(`gh workflow run`) | —                | `com.xue.finviz-to-tv.us-rs-3m-trigger.plist` |
| HK RS trigger  | Mon–Fri 18:45 HKT(`gh workflow run`) | —                | `com.xue.finviz-to-tv.hk-rs-trigger.plist`    |

所有 plist 都放在 `~/Library/LaunchAgents/`(源拷贝在 `scripts/`)。两个 RS trigger 在每个 EOD 前 **75 分钟**派发云端 RS/metrics workflow,因为 GitHub 的计划 cron 不可靠(观察到延迟数小时 / 被跳过);workflow 的 commit 步是幂等的,所以双重触发(GH cron + launchd)无害。

10:00 HKT 槽落在 EDT 和 EST 两种情况下的美股收盘之后,且在每日上游 RS Rating commit 之后。20:00 HKT 槽在 HK 收盘(16:00 HKT)后留了 4 小时余量给 k 线数据定稿。US 槽用 `--mode us-eod`(刻意跳过 HK——10:00 HKT 时 HK 才开市 30 分钟、今日 k 线 bar 未完成)。每个 EOD 步成功后,wrapper 脚本会软失败地调一次 `--mode report --market {us,hk}`,让当天 CANSLIM 简报在同一窗口产出——那里失败不影响 EOD 退出码。

```bash
# US 槽
sudo pmset repeat wakeorpoweron TWRFS 09:59:00
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# HK 槽(无 pmset——Mac 在 20:00 HKT 通常醒着;若睡着 launchd 在下次唤醒时触发)
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.hk-eod.plist

# Morning-gap(独立 plist,90 条日历项:Mon–Fri × 9 offsets × EDT/EST)
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist
sudo uv run scripts/schedule_morning_gap_wakes.py    # 排一次性唤醒(每周重跑)
```

morning-gap 脚本在每次触发时自校验 ET 时间,窗口外干净退出。

## 导入 (Importing)

- **TradingView**:Watchlist → "Import list..." → 选最新的 `output/TV/{US,HK}/<date>_*.txt`。HK ticker 写成不带前导零(`HKEX:148`,不是 `HKEX:0148`)——TradingView 会静默拒绝补位形式。
- **Webull**:Watchlist → "Upload as File" → 从 `output/Webull/{US,HK}/` 选对应文件(换行分隔;逗号格式会被静默截断)。

## 配置 (Configuration)

所有 screener 过滤、阈值、Futu/ntfy 设置都在 `config.toml`。架构与贡献说明见 [`CLAUDE.md`](CLAUDE.md)。

## 依赖 (Dependencies)

Python ≥ 3.12(见 `pyproject.toml`)—— [finviz](https://github.com/mariostoev/finviz), [yfinance](https://github.com/ranaroussi/yfinance), [openpyxl](https://openpyxl.readthedocs.io/), [curl-cffi](https://pypi.org/project/curl-cffi/), [futu-api](https://pypi.org/project/futu-api/), [anthropic](https://pypi.org/project/anthropic/)(报告——Anthropic 和 DeepSeek 两个后端都用它), [httpx](https://www.python-httpx.org/)(Tavily 搜索 + TV 同步), [markdown](https://pypi.org/project/Markdown/)。开发:pytest + pytest-asyncio。
