# Shorts yfinance Download Consolidation

## 背景

现在 Shorts 流程对同一批 tickers 调用 `yf.download` 三次:

1. `filter_shorts` (main.py:291) — 下载 2mo,做 performance filter
2. `filter_dollar_volume_yf` (main.py:388) — 又下载 2mo,做 dollar volume filter
3. `filter_consecutive_up_days` (main.py:342) — 再下载 1mo,做 consecutive up days filter

每次批量下载都有概率某些 ticker 返回不全(insufficient data warning)。三次下载放大了失败概率,且每次的 lenient fallback 把数据残缺的 ticker 直接放行,影响最终清单质量。

## 目标

把 Shorts 流程的 yfinance 下载从 3 次降到 1 次,所有 filter 共享同一份数据。

## 设计

### 函数签名变更

新增两个内部 helper(只供 `filter_shorts` 用):

```python
def _filter_dollar_volume_from_data(
    tickers: list[str],
    data,
    min_dollar_volume: float,
    market_open: bool,
    today_date,
    days: int = 20,
) -> list[str]: ...

def _filter_consecutive_up_days_from_data(
    tickers: list[str],
    data,
    min_days: int,
    market_open: bool,
    today_date,
) -> list[str]: ...
```

老的 `filter_dollar_volume_yf` 和 `filter_consecutive_up_days` **保留** —— Longs 和 RS 路径继续用,因为它们各自只调一次,没必要改。

### filter_shorts 改造

把 dollar volume 和 consecutive up days 两个 filter 移进 `filter_shorts` 内部,共用 performance filter 已经下载的那份 DataFrame。

新签名:

```python
def filter_shorts(
    filters: list[str],
    signal: str | None,
    perf_large_cap: float,
    perf_mid_cap: float,
    perf_small_cap: float,
    min_dollar_volume: float,
    min_consecutive_up_days: int,
) -> tuple[int, list[str]]:
    """Run shorts pipeline: finviz Ownership → single yfinance download →
    performance / dollar-volume / consecutive-up-days filters."""
```

内部流程:

```
1. finviz Ownership screener → tickers + market_caps
2. _yf_download_with_retry(tickers, period="2mo")  ← 唯一下载
3. performance filter (用 data) → passed_perf
4. _filter_dollar_volume_from_data(passed_perf, data) → passed_dv
5. _filter_consecutive_up_days_from_data(passed_dv, data) → passed_final
6. return (total, passed_final)
```

### main.py 改动

`main()` 里 Shorts 部分简化为:

```python
total, shorts_tickers = filter_shorts(
    shorts_cfg["filters"],
    shorts_cfg.get("signal"),
    perf_large_cap=...,
    perf_mid_cap=...,
    perf_small_cap=...,
    min_dollar_volume=shorts_cfg.get("min_dollar_volume", 100_000_000),
    min_consecutive_up_days=shorts_cfg.get("min_consecutive_up_days", 3),
)
```

不再单独调 `filter_dollar_volume_yf` 和 `filter_consecutive_up_days`。日志输出移到 `filter_shorts` 内部。

### 不变的部分

- Longs 路径:继续用 `filter_dollar_volume_yf` 和 `filter_relative_volume`
- RS 路径:继续用 `filter_dollar_volume_yf`
- HK Shorts:本来就是单次下载,不动
- "宽松保留"语义:数据残缺时仍然 keep,行为不变
- 市场未收盘时剔除今天的 bar:仍然剔除,只是判断点上提到 `filter_shorts` 顶部
- finviz 调用、performance filter 阈值、cap 分桶逻辑:全部不变

## 影响

**好处:**
- 网络请求 3 次 → 1 次
- 三个 filter 看到同一份数据快照,跨 filter 一致性提升
- `_yf_download_with_retry` 已存在的整批重试自动覆盖这次单次下载

**潜在差异:**
- 如果一只 ticker 在共享 data 里 closes 不够长,会在 performance filter 阶段被 drop(原本可能在 dv/up-days 阶段被宽松保留)。这种 ticker 本来就没有可信数据,过滤掉更准确,不算 regression。

## 测试方式

无单元测试基础设施。手动验证:
- 跑 `uv run main.py`,观察日志
- 对比 `output/US/Shorts.txt` 与最近一次的差异(应该差异很小,且 insufficient data warning 显著减少)
- 检查日期归档文件是否正常生成
