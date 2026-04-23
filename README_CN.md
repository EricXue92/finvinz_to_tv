# Finviz to TradingView

自动化股票筛选工具，运行自定义 Finviz 筛选条件，并将结果导出为 TradingView 可导入的观察列表。

## 筛选标准

### 做多 (4 个策略，合并去重)

基于 **Oliver Kell** 的动量/突破方法论：

| 策略 | 关键筛选条件 |
|------|-------------|
| 相对成交量激增 | 平均成交量 > 50万, 股价 > $20, Beta > 1.5, 当日上涨, 高于 SMA200, 相对成交量 > 3倍20日均量 (通过 yfinance) |
| 涨幅榜 | 平均成交量 > 50万, 股价 > $20, Beta > 1.5, 高于 SMA50 和 SMA200, 信号: 涨幅榜 |
| 跳空高开 | 平均成交量 > 50万, 股价 > $20, Beta > 1.5, 跳空高开 3%+, 高于 SMA200 |
| 52周新高 | 小盘股+, 平均成交量 > 100万, 股价 > $20, Beta > 1.5, 52周新高, 高于 SMA50 和 SMA200 |

### 做空 (1 个策略，多阶段筛选)

基于 **Kristjan Kullamägi** 博客的做空标准：

**第一阶段 — Finviz 筛选：**

| 筛选条件 | 标准 |
|---------|------|
| SMA20 | 股价高于 20 日均线 20%+ |
| 平均成交量 | > 100万股 |
| 市值 | > $3亿（小盘股及以上） |

**第二阶段 — 后处理：**

| 筛选条件 | 标准 |
|---------|------|
| 美元成交量 | 股价 × 平均成交量 >= $1亿 |
| 月度表现（大盘股 ≥ $100亿） | 最近一个月涨幅 50%+ |
| 月度表现（中盘股 $20亿–$100亿） | 最近一个月涨幅 200%+ |
| 月度表现（小盘股 $3亿–$20亿） | 最近一个月涨幅 300%+ |
| 连续上涨天数 | 连续 3 天以上收阳（通过 yfinance 获取数据；盘中运行时排除当日未完成数据，收盘后包含当日） |

### RS - 相对强度（条件触发）

基于 **Oliver Kell** 的相对强度方法。仅在 SPY 和 QQQ 同日跌幅超过 1% 时运行——在弱势市场中寻找表现强势的股票。

| 策略 | 关键筛选条件 |
|------|-------------|
| 相对强度 | 平均成交量 > 50万, 股价 > $20, Beta > 1.5, 当日上涨, 高于 SMA50 和 SMA200 |

## 输出

```
output/
├── Longs.txt              # 最新做多候选（每日覆盖）
├── Shorts.txt             # 最新做空候选（每日覆盖）
├── RS.txt                 # 最新相对强度（仅在市场条件满足时生成）
├── 2026_04_21_Longs.txt   # 按日期归档
├── 2026_04_21_Shorts.txt
└── 2026_04_21_RS.txt
```

每次运行会同时生成最新文件（如 `Longs.txt`）和带日期的归档副本（如 `2026_04_21_Longs.txt`）以保留历史记录。文件内容为逗号分隔的股票代码（如 `AAPL,MSFT,NVDA`），可直接导入 TradingView。

## 安装

```bash
# 安装依赖
uv sync

# 手动运行
uv run main.py
```

## 导入 TradingView

1. 打开 TradingView
2. 右侧面板 -> 观察列表 -> 点击列表名称
3. 选择"导入列表..."
4. 选择 `output/Longs.txt`（或 Shorts/RS）

## 自动化 (launchd + pmset)

脚本通过 macOS launchd 在每日美股收盘后自动运行，配合 `pmset` 定时唤醒 Mac。

**调度时间：** 周二至周六 6:00 AM HKT = 周一至周五美股收盘后。6 AM HKT 在夏令时 (EDT, 收盘后2小时) 和冬令时 (EST, 收盘后1小时) 下均安全。

### 工作原理

1. **`pmset repeat`** 在每周二至周六 5:59 AM HKT 唤醒 Mac
2. **launchd** (`~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`) 在 6:00 AM 执行脚本
3. 执行完成后，Mac 自动恢复睡眠

### 设置方法

```bash
# 设置 Mac 在周二至周六 5:59 AM 自动唤醒
sudo pmset repeat wakeorpoweron TWRFS 05:59:00

# 验证唤醒计划
pmset -g sched
```

launchd 配置文件位于 `~/Library/LaunchAgents/com.xue.finviz-to-tv.plist`。管理命令：

```bash
# 加载（启用）
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# 卸载（停用）
launchctl unload ~/Library/LaunchAgents/com.xue.finviz-to-tv.plist

# 检查状态
launchctl list | grep finviz
```

> **注意：** 与 cron 不同，launchd 会补执行错过的任务——如果 Mac 在 6:00 AM 时处于睡眠状态，任务会在 Mac 唤醒后立即执行。

## 配置

所有筛选参数在 `config.toml` 中。你可以修改筛选条件、添加新策略或调整设置（请求间隔、输出格式），无需修改代码。

## 依赖

- Python >= 3.12
- [finviz](https://github.com/mariostoev/finviz) - Finviz 网页抓取（无需 API 密钥或付费账户）
- [yfinance](https://github.com/ranaroussi/yfinance) - Yahoo Finance 数据，用于做空连续上涨天数筛选
