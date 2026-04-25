# Finviz to TradingView

Automated stock screener that runs custom Finviz scans (US) and HKEX + yfinance scans (Hong Kong), exporting results as TradingView-importable watchlists.

自动化股票筛选工具，运行自定义 Finviz 扫描（美股）和 HKEX + yfinance 扫描（港股），导出为 TradingView 可导入的自选股列表。

## Screening Criteria / 筛选标准

### Longs / 做多（4 strategies, merged & deduplicated / 4 个策略，合并去重）

Based on **Oliver Kell**'s momentum/breakout methodology:

基于 **Oliver Kell** 的动量/突破方法论：

| Strategy / 策略 | Key Filters / 关键筛选条件 |
|----------|-------------|
| Relative Volume Surge / 相对成交量飙升 | Avg Vol > 500K, Price > $20, Beta > 1.5, Day Up, Above SMA200, Rel Vol > 3x 20-day avg (via yfinance) |
| Top Gainers / 涨幅榜 | Avg Vol > 500K, Price > $20, Beta > 1.5, Above SMA200, Signal: Top Gainers |
| Gap Up / 跳空高开 | Avg Vol > 500K, Price > $20, Beta > 1.5, Gap Up 3%+, Above SMA200 |
| 52W New High / 52 周新高 | Small Cap+, Avg Vol > 1M, Price > $20, Beta > 1.5, New 52W High, Above SMA50 & SMA200 |

All longs strategies also require **Dollar Volume >= $100M** (Price × 20-day avg volume, via yfinance). The "Avg Vol" filters above are Finviz pre-filters using Finviz's 3-month average to reduce result count before post-processing.

所有做多策略还要求**成交额 >= 1 亿美元**（价格 × 20 日均成交量，通过 yfinance 计算）。上表中的"Avg Vol"筛选条件为 Finviz 预过滤，使用 Finviz 的 3 个月均量来减少后处理前的结果数量。

### Shorts / 做空（1 strategy, multi-phase filtering / 1 个策略，多阶段筛选）

Based on **Kristjan Kullamägi**'s short-selling criteria:

基于 **Kristjan Kullamägi** 的做空标准：

**Phase 1 — Finviz filters / 第一阶段 — Finviz 筛选：**

| Filter / 筛选条件 | Criteria / 标准 |
|--------|----------|
| SMA20 | Price 20%+ above 20-day moving average / 价格高于 20 日均线 20% 以上 |
| Avg Volume / 日均成交量 | > 1M shares (Finviz 3-month avg, pre-filter) / > 100 万股（Finviz 3 个月均量，预过滤） |
| Market Cap / 市值 | > $300M (small cap and above) / > 3 亿美元（小盘股及以上） |

**Phase 2 — Post-processing (via yfinance) / 第二阶段 — 后处理（通过 yfinance）：**

| Filter / 筛选条件 | Criteria / 标准 |
|--------|----------|
| Dollar Volume / 成交额 | Price × 20-day avg volume >= $100M / 价格 × 20 日均量 >= 1 亿美元 |
| Performance (Large Cap ≥ $10B) / 涨幅（大盘股 ≥ 100 亿美元） | Up 50%+ over 2, 3, or 4 weeks / 2、3 或 4 周内涨幅 >= 50% |
| Performance (Mid Cap $2B–$10B) / 涨幅（中盘股 20-100 亿美元） | Up 200%+ over 2, 3, or 4 weeks / 2、3 或 4 周内涨幅 >= 200% |
| Performance (Small Cap $300M–$2B) / 涨幅（小盘股 3-20 亿美元） | Up 300%+ over 2, 3, or 4 weeks / 2、3 或 4 周内涨幅 >= 300% |
| Consecutive Up Days / 连续上涨天数 | 3+ consecutive green days (excludes today's incomplete data if market is still open) / 连续 3 天以上收涨（盘中扫描排除当天未完成数据） |

Performance is checked over 2-week (10 trading days), 3-week (15 trading days), and 4-week (22 trading days) windows via yfinance. A ticker passes if it meets the cap-conditional threshold in any window. Results are aggregated. Then the 3+ consecutive up days filter is applied to the aggregated results.

涨幅通过 yfinance 在 2 周（10 个交易日）、3 周（15 个交易日）和 4 周（22 个交易日）窗口中检查。任意窗口满足对应市值级别的阈值即通过，三个窗口的结果合并。然后对合并后的结果再过滤，要求最近至少连续 3 天收涨。

### RS - Relative Strength / 相对强度（conditional / 条件触发）

Based on **Oliver Kell**'s relative strength approach. Only runs when both SPY and QQQ drop more than 1.5% on the day — identifies stocks showing strength in a weak market.

基于 **Oliver Kell** 的相对强度方法。仅当 SPY 和 QQQ 当日同时下跌超过 1.5% 时运行 — 识别弱市中表现强势的股票。

| Strategy / 策略 | Key Filters / 关键筛选条件 |
|----------|-------------|
| Relative Strength / 相对强度 | Avg Vol > 500K, Price > $20, Beta > 1.5, Day Up, Above SMA50 & SMA200, Dollar Volume >= $100M (via yfinance) |

### HK Shorts / 港股做空（1 strategy, multi-phase filtering / 1 个策略，多阶段筛选）

Hong Kong market short candidates using the same methodology as US Shorts, sourced from **HKEX + yfinance** instead of Finviz.

港股做空候选，使用与美股做空相同的方法论，数据来源为 **HKEX + yfinance** 而非 Finviz。

**Phase 1 — HKEX universe + yfinance filtering / 第一阶段 — HKEX 股票池 + yfinance 筛选：**

| Filter / 筛选条件 | Criteria / 标准 |
|--------|----------|
| Universe / 股票池 | HKEX Main Board equities (~2,400 stocks) / 港交所主板股票（约 2,400 只） |
| SMA20 | Price 20%+ above 20-day moving average / 价格高于 20 日均线 20% 以上 |
| Avg Volume / 日均成交量 | > 1M shares/day (20-day average) / > 100 万股/天（20 日均量） |

**Phase 2 — Post-processing / 第二阶段 — 后处理：**

| Filter / 筛选条件 | Criteria / 标准 |
|--------|----------|
| Market Cap / 市值 | >= HKD 300M / >= 3 亿港元 |
| Dollar Volume / 成交额 | Price × 20-day avg volume >= HKD 100M / 价格 × 20 日均量 >= 1 亿港元 |
| Performance (Large Cap ≥ HKD 10B) / 涨幅（大盘股 ≥ 100 亿港元） | Up 50%+ over 2, 3, or 4 weeks / 2、3 或 4 周内涨幅 >= 50% |
| Performance (Mid Cap HKD 2B–10B) / 涨幅（中盘股 20-100 亿港元） | Up 200%+ over 2, 3, or 4 weeks / 2、3 或 4 周内涨幅 >= 200% |
| Performance (Small Cap HKD 300M–2B) / 涨幅（小盘股 3-20 亿港元） | Up 300%+ over 2, 3, or 4 weeks / 2、3 或 4 周内涨幅 >= 300% |
| Consecutive Up Days / 连续上涨天数 | 3+ consecutive green days / 连续 3 天以上收涨 |

HK tickers are output in `HKEX:XXXX` format for TradingView (e.g. `HKEX:0700`).

港股代码以 `HKEX:XXXX` 格式输出，适用于 TradingView（如 `HKEX:0700`）。

## Output / 输出

```
output/
├── US/
│   ├── Longs.txt              # Latest US long candidates / 最新美股做多候选
│   ├── Shorts.txt             # Latest US short candidates / 最新美股做空候选
│   ├── RS.txt                 # Latest relative strength (conditional) / 最新相对强度（条件触发）
│   ├── 2026_04_21_Longs.txt   # Date-stamped archives / 日期归档
│   ├── 2026_04_21_Shorts.txt
│   └── 2026_04_21_RS.txt
└── HK/
    ├── Shorts.txt             # Latest HK short candidates / 最新港股做空候选
    └── 2026_04_21_Shorts.txt
```

Each run generates both a latest file (e.g. `Shorts.txt`) and a date-stamped copy to preserve history. Files are comma-separated ticker symbols, ready for TradingView import.

每次运行生成最新文件（如 `Shorts.txt`）和日期归档副本。文件为逗号分隔的股票代码，可直接导入 TradingView。

## Setup / 安装

```bash
# Install dependencies / 安装依赖
uv sync

# Run manually / 手动运行
uv run main.py
```

## Import to TradingView / 导入 TradingView

1. Open TradingView / 打开 TradingView
2. Right panel -> Watchlist -> Click the list name / 右侧面板 -> 自选股 -> 点击列表名称
3. Select "Import list..." / 选择"导入列表..."
4. Choose `output/US/Longs.txt` (or Shorts/RS for US, `output/HK/Shorts.txt` for HK) / 选择对应文件

## Automation (launchd + pmset) / 自动化

The script runs daily after US market close via macOS launchd, with `pmset` to wake the Mac from sleep.

脚本通过 macOS launchd 在美股收盘后每日自动运行，使用 `pmset` 从睡眠中唤醒 Mac。

**Schedule / 时间表:** Tue-Sat 8:30 AM HKT = Mon-Fri after US market close. 8:30 AM HKT is safe for both EDT (4.5h after close) and EST (3.5h after close), and allows yfinance/Finviz EOD data to fully settle before the run — earlier times (e.g. 6 AM) can produce noisier results due to stale or partial data.

**时间表：**周二至周六早上 8:30（香港时间）= 周一至周五美股收盘后。8:30 HKT 在 EDT（收盘后 4.5 小时）和 EST（收盘后 3.5 小时）下均安全，且能让 yfinance/Finviz 的 EOD 数据完全落盘再运行 —— 过早的时间（如 6:00）可能因数据未稳定而产生波动较大的结果。

### How it works / 工作原理

1. **`pmset repeat`** wakes the Mac at 8:29 AM HKT (Tue-Sat) / 在 8:29 AM HKT 唤醒 Mac
2. **launchd** (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`) runs the script at 8:30 AM / 在 8:30 AM 运行脚本
3. After execution, the Mac automatically returns to sleep / 执行完毕后 Mac 自动回到睡眠

### Setup / 设置

```bash
# Schedule Mac to wake at 8:29 AM Tue-Sat / 设置 Mac 在周二至周六 8:29 AM 唤醒
sudo pmset repeat wakeorpoweron TWRFS 08:29:00

# Verify wake schedule / 验证唤醒计划
pmset -g sched
```

The launchd plist is installed at `~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`. To manage it:

launchd 配置文件位于 `~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`。管理命令：

```bash
# Load (enable) / 加载（启用）
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# Unload (disable) / 卸载（停用）
launchctl unload ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# Check status / 检查状态
launchctl list | grep finviz
```

> **Note / 注意:** Unlike cron, launchd will catch up on missed runs — if the Mac was asleep at 8:30 AM, the task executes as soon as the Mac wakes up. / 与 cron 不同，launchd 会补执行错过的任务 — 如果 Mac 在 8:30 AM 处于睡眠状态，任务会在唤醒后立即执行。

## Configuration / 配置

All screener parameters are in `config.toml`. You can modify filters, add new screeners, or adjust settings (delay between requests, output format) without touching the code.

所有筛选参数在 `config.toml` 中配置。可修改筛选条件、添加新策略或调整设置（请求间隔、输出格式），无需修改代码。

## Dependencies / 依赖

- Python >= 3.12
- [finviz](https://github.com/mariostoev/finviz) - Finviz web scraper (no API key or premium account required) / Finviz 网页爬虫（无需 API 密钥或付费账户）
- [yfinance](https://github.com/ranaroussi/yfinance) - Yahoo Finance data for post-processing filters and HK market data / Yahoo Finance 数据，用于后处理筛选和港股数据
- [openpyxl](https://openpyxl.readthedocs.io/) - HKEX securities list xlsx parsing / HKEX 证券列表 xlsx 解析
