# HK Shorts Local Price Cache

## 背景

HK Shorts 流程目前每次跑都对 ~2,400 只 HKEX 股票做 yfinance 批量下载(分 5 批,每批 500),大约 5 分钟。yfinance 批量下载有"抖动":每次跑都有一些 ticker 返回不全,被 Phase 1 的 `if len(closes) < 20: continue` 直接 skip,导致结果不稳定且数据完整度受网络/Yahoo 限流影响。

每天的 HKEX 上市公司列表基本不变,价格数据大部分都是已经拿过的——重复下载等于在同一份"昨天前的数据"上反复消耗带宽和 Yahoo 配额。

## 目标

引入本地 SQLite 缓存,把 HK 价格数据"按需增量"获取,使得:

1. 每天跑只需要拉**当天新增的 1 根 bar**,而不是 2 个月的全部历史
2. yfinance 抖动不再丢数据——拉不到时退化到 cache 里已有的(可能是 1 天前的)数据
3. 总耗时从 ~5 分钟降到几十秒(稳态)

范围限定:**只缓存 HK Shorts 相关数据**。US Longs / US Shorts / RS 不在本次范围内(它们的抖动可以用更轻量的 per-ticker 重试解决,如果以后需要)。

## 设计

### 1. 存储位置

`./cache/hk_prices.db`(项目目录内 SQLite)

- 添加 `cache/` 到 `.gitignore`
- 项目内便于查看/调试(`sqlite3 cache/hk_prices.db` 直接进 REPL)
- macOS 单机定时跑,不需要 XDG/全局位置

### 2. Schema

```sql
CREATE TABLE bars (
  ticker  TEXT NOT NULL,        -- yfinance 形式,如 "0700.HK"
  date    TEXT NOT NULL,        -- ISO YYYY-MM-DD
  open    REAL,
  high    REAL,
  low     REAL,
  close   REAL,
  volume  INTEGER,
  PRIMARY KEY (ticker, date)
);

CREATE INDEX idx_bars_ticker_date ON bars(ticker, date DESC);

CREATE TABLE market_cap (
  ticker      TEXT PRIMARY KEY,
  cap         REAL,
  updated_at  TEXT              -- ISO YYYY-MM-DD
);
```

OHLCV 全字段存:虽然当前过滤器只用到 close/volume,留余地以后加新过滤器(MACD、ATR 等)。

market_cap 单独表:它来自 `Ticker.fast_info.market_cap`,不属于 OHLCV 时间序列;每只 ticker 只保留一个最新值,每天刷新。

### 3. 历史保留

保留 **90 个日历日**(约 60 个交易日)。够用:
- SMA20、20-day avg volume → 20 个交易日
- Performance 4 周窗口 → 22 个交易日
- 余 30+ 交易日缓冲

每次跑结束后 prune,删除 `date < today - 90 days` 的 bars。market_cap 表不 prune(每只 ticker 只有一行)。

### 4. 刷新策略

每次跑 HK Shorts 开头(在 filter 循环前):

```
1. 从 HKEX XLSX 拿 universe(2,400 只代码)
2. 从 cache 查每只 ticker 的 last_bar_date,分三类:
   - never_seen:cache 里没数据  → 走 bootstrap(period=3mo)
   - stale:last_bar < 期望最新交易日  → 增量(从 last_bar+1 到 today)
   - fresh:last_bar 已是最新  → 跳过
3. 对 stale 组按"缺失天数"再分组,合并同 period 的请求
4. 用 _yf_download_with_retry 批量下载,写入 bars 表(INSERT OR REPLACE)
5. 对下载后仍然残缺的 ticker(yfinance batch 抖动) → 单只重试一次,使用同一个 period(bootstrap 用 3mo,增量用 stale 组对应的 period)
6. 仍然残缺 → 日志 warning,但 cache 里现有的数据保留(关键:数据降级,不丢失)。"残缺"的判定:增量场景下,期望最新交易日的 bar 缺失;bootstrap 场景下,小于 20 个交易日
7. 同步刷新 market_cap 表(对 Phase 1 通过的 ticker)
8. 跑完所有 filter 后 prune 90 天外 bars
```

"期望最新交易日"的计算:
- 现在是港股周一-周五 9:30-16:00 收盘前 → 期望 = 上一个交易日
- 收盘后 → 期望 = 今天(只看日期,只要今天是交易日)
- 周末 / 法定假期 → 期望 = 上一个交易日

简化版:不查交易日历,直接看 `weekday < 5` 来判断是否交易日。逢节假日 yfinance 会返回 0 行,缓存就保持上一个交易日的数据,不会出错。

### 5. Filter 集成

`filter_hk_shorts` 改造,内部不再调 yfinance 拿 OHLCV:

```
def filter_hk_shorts(config, conn):
    codes = fetch_hkex_equities()
    yf_tickers = [code + ".HK" for code in codes]

    # 替换原来的 phase 1 batch download
    refresh_cache(conn, yf_tickers, expected_latest_date)

    # 从 cache 一次性读所有 ticker 的 closes/volumes
    ticker_closes = {}
    ticker_volumes = {}
    for ticker in yf_tickers:
        closes, volumes = read_bars_from_cache(conn, ticker)
        if closes is not None:
            ticker_closes[ticker] = closes
            ticker_volumes[ticker] = volumes

    # phase 1: SMA20 + volume(逻辑不变,数据源换成 cache)
    phase1 = [...]

    # phase 2: market cap(从 cache 读,缺则现拉)
    for ticker in phase1:
        cap = get_market_cap_cached(conn, ticker)
        if cap is None:
            cap = _get_market_cap(ticker)
            if cap:
                upsert_market_cap(conn, ticker, cap)
        ...

    # phase 3-5:不变(仍然复用 ticker_closes / ticker_volumes)
```

### 6. 新模块 `cache.py`

抽出新文件,接口:

```python
def open_cache(db_path: Path) -> sqlite3.Connection
def get_last_bar_dates(conn, tickers: list[str]) -> dict[str, date | None]
def upsert_bars(conn, ticker: str, df) -> None       # df 是 yfinance 单 ticker DataFrame
def read_ticker_bars(conn, ticker: str) -> tuple[Series, Series] | None  # closes, volumes
def get_market_cap_cached(conn, ticker: str) -> float | None
def upsert_market_cap(conn, ticker: str, cap: float) -> None
def prune_old_bars(conn, days: int = 90) -> int     # 返回删除行数

def refresh_cache(conn, tickers: list[str], expected_latest_date: date) -> dict
    """主入口:带分批/重试/降级的刷新。返回 stats dict(包含 fresh/stale/bootstrapped/failed 计数)。"""
```

`main.py` 里 `filter_hk_shorts` 接收一个 `conn` 参数;`main()` 在调用前打开 cache。

### 7. 失败处理矩阵

| 情况 | 行为 |
|---|---|
| yfinance 整批失败 | `_yf_download_with_retry` 已有 3 次重试;最终失败则跳过本批 |
| batch 部分 ticker 残缺 | 单只重试一次 |
| 单只仍失败 + cache 有数据 | 用 cache 数据(可能 1-N 天前),log warning |
| 单只仍失败 + cache 完全没有 | 跳过该 ticker,log warning |
| sqlite write 失败 | 让异常冒泡,不静默吞 |
| 数据库文件损坏 | 用户手动删 `cache/hk_prices.db` 重建 |

### 8. Bootstrap

第一次跑(cache 空):
- 所有 2,400 只走 bootstrap 分支
- yfinance 批量下载 period=3mo,跟现在的行为接近
- 加上 SQLite 写入开销,首跑比当前慢 ~1.5x(可接受,一次性)
- 此后每天只拉 1 天数据,几十秒搞定

### 9. 测试与验证

无单元测试基础设施。验收:

1. 第一次跑(空 cache):output/HK/Shorts.txt 与重构前的结果对比,差异应该很小(只来自 yfinance 自身随机性)
2. 第二次跑(cache 已 warm):耗时应大幅缩短
3. 检查 `sqlite3 cache/hk_prices.db "select count(*) from bars; select count(*) from market_cap"` —— 行数合理
4. 模拟 yfinance 失败:断网跑一次,output 应该仍然产出(用 cache 降级数据)

## 不做的

- 不缓存 US 数据(本次范围 B 已确定)
- 不缓存 HKEX XLSX 文件(后续可单独加)
- 不做 split/dividend 自定义调整 —— 用 yfinance 默认 auto_adjust 行为(跟现在一致)
- 不做并发写锁(单进程)
- 不做 schema migration —— 改 schema 时删 cache 重建
- 不做交易日历查询 —— 用 weekday + yfinance 自然行为代替
