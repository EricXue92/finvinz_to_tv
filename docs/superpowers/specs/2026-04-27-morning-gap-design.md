# Morning Gap Up 盘中扫描

## 背景

现有流程都是美股盘后 EOD 跑(8:30 HKT 一次),抓的是日线信号(Longs / Shorts / RS / HK Shorts)。
但有一类高质量信号只在开盘后 30 分钟内显现:**有催化剂(财报/新闻/并购)** 的票,在头 15-30 分钟就交易完一天的均量,gap up + 持续放量是盘中入场的最强组合。截图(Kullamägi 方法论)直接说明了这一点。

## 目标

新增"盘中 gap-up"扫描器,开盘后 +10/+15/+20/+25/+30 分钟各扫描一次,输出符合标准的 ticker 列表到 TradingView watchlist。

## Filter 标准

Finviz screener filters(URL 参数):
- `sh_avgvol_o500` — 日均成交量 > 500K
- `sh_price_o10` — 股价 > $10
- `ta_beta_o1.5` — beta > 1.5
- `ta_gap_u5` — 跳空高开 ≥ 5%
- `ta_sma200_pa` — 价格在 SMA200 之上

(无 `earningsdate_today` — 任何利好都可触发,volume 过滤会自动筛选真有催化剂的票)

后处理(yfinance):
- **Dollar volume:** 当前价 × 20 日均量 ≥ $100M
- **盘中累计 volume:** 9:30 ET 起到 `9:30 + offset` ET 的累计成交量 ≥ 过去 20 个交易日全天平均 volume

举例:某股 20 天日均成交量 100 万股。开盘后 10 分钟它累计成交了 120 万股 → 通过。这正是"在头 15-30 分钟就交易完一天均量"的实操标准。

## 设计

### 1. CLI 入口

`main.py` 新增可选参数 `--mode`,默认 `eod`(现有行为不变):

```bash
uv run main.py                    # 现有 EOD 流程(Longs / Shorts / RS / HK Shorts)
uv run main.py --mode morning-gap # 盘中扫描(自动从 ET 时间算 offset)
```

`--mode morning-gap` 时**只跑** morning gap,不动 EOD 流程。

### 2. Config

`config.toml` 新增 section:

```toml
[morning_gap]
name = "Morning Gap Up"
filters = ["sh_avgvol_o500","sh_price_o10","ta_beta_o1.5","ta_gap_u5","ta_sma200_pa"]
avg_volume_days = 20
min_dollar_volume = 100_000_000
scan_offsets = [10, 15, 20, 25, 30]   # 开盘后第几分钟扫描
archive_offset = 30                    # 仅 30 分钟那次写当日存档
offset_tolerance_minutes = 2          # ET 时间匹配 offset 的容差
```

### 3. 主流程(新增函数 `run_morning_gap`)

1. **算 offset**
   - 读当前 ET 时间(`datetime.now(ZoneInfo("America/New_York"))`)
   - `minutes_since_open = (now_et - 9:30 ET) 分钟数`
   - 在 `scan_offsets` 中找最接近的 offset,差值 ≤ `offset_tolerance_minutes` 则用之,否则 log warning 并退出(`return 0`,不算错误)
   - 若是周末或 ET 时间不在 9:30-10:05 之间,直接退出

2. **Finviz screener** — 用 `run_screener(filters)` 拿候选 ticker

3. **拉 20 天日 K 线** — `_yf_download_with_retry(tickers, period="2mo", interval="1d", group_by="ticker")`

4. **Dollar volume 过滤** — 复用现有 `_filter_dollar_volume_from_data`(已有 helper,签名匹配)

5. **算 20 日日均 volume** — 对剩余 ticker,从同一份日 K 线数据取 `volumes.iloc[-20:].mean()`(若市场开盘则先 `_trim_today` 剔除当天不完整数据)→ 存到 `dict[str, float]`

6. **拉当天 1 分钟 K 线** — `_yf_download_with_retry(tickers, period="1d", interval="1m", group_by="ticker")`(只对剩余 ticker)

7. **盘中累计 volume 过滤**
   - 对每个 ticker,从 1m 数据中取 9:30 ET 起、到 `9:30 + offset - 1` ET(含)的所有 1 分钟 bar,volume 求和
   - 若求和 ≥ 20 日日均 volume → 入选
   - 数据缺失/不足的 ticker 直接 drop(strict,与现有 Shorts 一致)

8. **写文件**
   - `output/US/MorningGap.txt` — 覆盖
   - 若 `offset == archive_offset`(默认 30):额外写 `output/US/{YYYY_MM_DD}_MorningGap.txt`
   - 用现有 `safe_write_watchlist`(50% 跌幅守护沿用)

### 4. Helper 复用

| 现有 helper | 用途 |
|---|---|
| `_yf_download_with_retry` | 两次 yfinance 下载 |
| `_filter_dollar_volume_from_data` | dollar volume 过滤 |
| `_get_closes_volumes` | 从下载结果里取 closes/volumes 序列 |
| `_trim_today` | 市场开盘时剔除当天不完整日 K 线 |
| `run_screener` | Finviz screener 调用 |
| `safe_write_watchlist` | 输出 + drop guard |

新增内容只有 `run_morning_gap` 一个函数 + 一段累计 1 分钟 volume 的逻辑。

### 5. Scheduling

新建 launchd plist:`~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist`

10 条 `StartCalendarInterval`(Mon–Fri × 5 个 HKT 时间 × EDT/EST 共 10 条):

| HKT 时间 | DST 假设 | NY 时间 | 对应 offset |
|---|---|---|---|
| 21:40 | EDT | 09:40 | 10 |
| 21:45 | EDT | 09:45 | 15 |
| 21:50 | EDT | 09:50 | 20 |
| 21:55 | EDT | 09:55 | 25 |
| 22:00 | EDT | 10:00 | 30 |
| 22:40 | EST | 09:40 | 10 |
| 22:45 | EST | 09:45 | 15 |
| 22:50 | EST | 09:50 | 20 |
| 22:55 | EST | 09:55 | 25 |
| 23:00 | EST | 10:00 | 30 |

脚本里 ET 时间自校验:无论 plist 在哪个时刻触发,都从当前 ET 时间反推 offset,不会跑错。在 DST 切换日如果 plist 误触发,脚本会发现 offset 不匹配并退出。

### 6. `pmset` 唤醒

约束:`pmset repeat` 全系统只支持一组配置(已被 8:29 HKT 占用,服务现有 EOD 流程),不能再加第二组 `repeat`。

策略:**不动现有 `pmset repeat`**,假设 21:00-23:00 HKT 期间 Mac 处于活跃使用状态(用户晚上在用)。launchd plist 在 Mac 醒着时正常触发。

如果实测发现夜间 Mac 经常自动睡眠导致漏跑,后续可在 README 增补一条 `pmset schedule` 用法(每日单次预约,不 repeat)— 但**不在本次范围内**,先观察是否真有问题。

## 边界与异常

- **节假日:** Finviz 仍可能返回 ticker(其 `ta_gap_u5` 等是基于上一次开盘),但 1 分钟数据为空 → 所有 ticker 被过滤 → 0 入选 → `safe_write_watchlist` 触发 50% 守护 → 上一份 `MorningGap.txt` 保留
- **DST 切换日:** plist 可能在错误的 HKT 时间触发,脚本 ET 时间自校验会退出
- **手动跑:** 用户在错误时间手动跑 `--mode morning-gap`,脚本退出并 log "not in scan window"
- **yfinance 失败:** 1 分钟数据拉失败 → 该 ticker drop(strict);若整批失败 → safe_write 守护
- **多 offset 间累计 volume 单调递增:** 候选集只会变大,safe_write 50% 跌幅守护不会日内误伤

## 不在范围内

- 不动现有 EOD 流程
- 不加 consecutive up days 过滤(intraday 不适用)
- 不做实时 push 通知 / 报警(只写文件)
- 不切换数据源到 Polygon/Alpaca,先用 yfinance 1m,后期发现漏票严重再升级

## 测试

- 在交易日 21:40 HKT(EDT)或 22:40 HKT(EST)手动跑一次,看输出
- 周末跑应直接退出
- 修改 `scan_offsets` 临时加 `1` 测短窗口路径(回退后删)
- 节假日运行,验证 `MorningGap.txt` 不被覆盖为空

## 文件变更

- `main.py` — 新增 `run_morning_gap()`,`main()` 加 `--mode` argparse
- `config.toml` — 新增 `[morning_gap]` section
- 新增 `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist`
- `README.md` — 增补盘中扫描使用说明 + `pmset` 配置注意事项
