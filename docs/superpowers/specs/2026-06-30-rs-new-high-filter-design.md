# RS New High 强势子清单 — 设计

**日期:** 2026-06-30
**状态:** 已批准设计,待写实现计划

## 背景与目标

从当日已选出的**长线侧**股票(US Longs+Leaders、HK 长线侧 Longs/Leaders)里再筛一层,
选出 **RS line 在最高点或接近最高点**的票 —— 即 TraderLion 式的 **RS New High (RS-NH)**,
代表"强势股里最强的"。这与现有 `[rs_line]` 的"RS line 在均线下方"信号(`rs_below_ma`)
正好互补。

输出形式:**单独的强势子清单**(独立 `.txt` + 独立 Futu 组),原有清单不动。

## 关键决策(已确认)

| 维度 | 决策 |
|------|------|
| 产出形式 | 单独的强势子清单(`<date>_RSNewHigh.txt` / `<date>_HKRSNewHigh.txt`),原清单不变 |
| 覆盖范围 | US + HK **长线侧全部**(Longs+Leaders)。Shorts 不纳入(看空不适用 RS-NH) |
| 回看窗口 | 全部可用历史(约 6 个月,云端已抓的 k 线),`window_max = rs.max()` |
| 接近容差 | `nh_tolerance` **起步 0.02(≤2%)**,做成本地可调阈值 |
| 阈值落点 | **云端只发布连续列 `rs_pct_off_high`,阈值留本地** —— 改带宽永不重抓 k 线 |
| 跨日 dedup | 不引入独立 master;作为已 dedup 长线侧的纯子集,可每日重新检测(同 RS/Shorts) |
| Futu 组 | diff 式(DEL+ADD),**不**加入 `append_only_groups` |

## 总体数据流

沿用现有"云端算列 / 本地读列"架构:

```
云端 (GitHub Actions, compute_{us_rs_3m,hk_rs}_cloud.py)
  └─ rs_line.compute_rs_new_high(klines, benchmark)
        → 新列 rs_pct_off_high  (= (window_max − rs_now) / window_max, 0=当日新高)
        → 并入已发布的 data/{us_rs_3m,hk_rs}/<date>.csv
本地 EOD (main.py / hk_eod.py)
  └─ 写完 Longs+Leaders 后, 取长线侧并集
        ∩ {0 ≤ rs_pct_off_high ≤ nh_tolerance}
        → 写 <date>_RSNewHigh.txt (TV) + Webull 镜像 + Futu 同步 + 日志分布
```

## 组件 1 — 云端 compute(`rs_line.py`)

新增 `compute_rs_new_high(klines, benchmark_kline, *, min_history=42)`,与现有
`compute_rs_line_features` 同构:纯计算、无网络/IO、不抛异常、按 `klines` dict key 索引。

- RS line `rs = close / bench_close`(date-aligned inner join,与现有函数一致)。
- 窗口 = 全部对齐后历史,`window_max = rs.max()`。
- 输出单列 **`rs_pct_off_high` = round((window_max − rs[-1]) / window_max, 4)**。
  当日新高 = 0,越大越弱(理论上 ≥ 0;数值噪声夹到 0 下限)。
- `min_history` 复用 42 根门槛;不足 → 不进 frame → 本地视为 unknown。
- 复用现有 `_has_bar_anomaly`(单日 ≥50% 跳变 → 排除,避免拆股污染 max)。
- 空 benchmark / 空 klines → 返回 `DataFrame(columns=["rs_pct_off_high"])`(同现有约定)。

两个云端脚本(`compute_us_rs_3m_cloud.py` / `compute_hk_rs_cloud.py`)在现有
`compute_rs_line_features` 的 `table.join(feats)` 旁边再 `join` 这一列,受同一个
`is_enabled([rs_line])` 开关控制。US 并入 `data/us_rs_3m/` CSV(本地 EOD 已读
`rs_table_3m`,零额外读取);HK 并入 `data/hk_rs/`。

## 组件 2 — 本地 EOD 筛选 + 输出

`main.py`(US)在写完 Leaders、做 RS-line 标注的同段(约 `main.py:1987`)新增 RS-NH 区块:

```
候选 = set(written_longs 各组并集) ∪ set(sorted_leaders)     # 当日最终长线侧 survivors
rs_nh = [t for t in 候选 if 0 ≤ rs_pct_off_high(t) ≤ nh_tolerance]
```

- 读 `rs_table_3m`(已在手)的 `rs_pct_off_high` 列。
- **unknown(缺列/缺票/历史不足)→ 不纳入**(正向精选:无法确认新高就不收);
  日志记录因 unknown 排除的数量。
- 输出 `output/TV/US/<date>_RSNewHigh.txt` + Webull 镜像 + `_futu_sync(config,"rs_new_high",…)`
  + `_tv_sync`。
- **不引入独立跨日 master**:它是已 dedup 长线侧的纯子集,dedup 由父清单继承。
  `write_watchlist` 沿用"空也写 0 字节"惯例。
- **分布日志**:打印 `RS-NH: N/M 通过 (≤1%: a, ≤2%: b, ≤5%: c; unknown: u)`,
  供日后按真实分布校准 `nh_tolerance`。

`hk_eod.py` 镜像同一逻辑:候选 = HK 长线侧 survivors,读 `data/hk_rs/` 的列,
组键 `hk_rs_new_high`。

## 组件 3 — 配置

`config.toml` `[rs_line]` 段新增(沿用现有 TUNABLE 风格):

```toml
nh_enabled     = true
nh_tolerance   = 0.02   # TUNE ⭐ RS line 距 6 个月高点 ≤ 此值 → 进 RSNewHigh。起步 2%,按分布日志校准
nh_min_history = 42     # 不足 → unknown, 不纳入
```

`[futu.groups]` 新增两条映射:

```toml
rs_new_high    = "RSNewHigh"
hk_rs_new_high = "HKRSNewHigh"
```

**不**加入 `append_only_groups`(RS-NH 是 diff 式精选,随当日子集变化,不单调累积)。

⚠️ **手动一次性操作**:`RSNewHigh`、`HKRSNewHigh` 两个 Futu 组须在 Futu 客户端手建
(API 建不了组)。否则那两条 Futu 同步是 no-op(软失败,只 warning)。TV / Webull / `.txt`
无需手建。

## 边界处理

- **空清单**:候选为空或全 unknown → 写 0 字节 `.txt`,Futu no-op(不擦组),符合现有不变量。
- **云端列缺失**(旧 CSV 未含新列):`rs_pct_off_high` 整列不存在 → 全 unknown →
  当日 RS-NH 为空 + 日志提示"列未就绪",静默降级,不报错(对齐 `summarize_rs_line` 的 None 行为)。
- **回看窗口短**:用全历史 max,只要过 `min_history`(42)即可。

## 测试(`tests/`,`uv run python -m pytest`)

- `compute_rs_new_high`:当日新高 → 0;回踩 → 正值;拆股跳变 → 排除;历史不足 → 不在 frame;
  空 benchmark → 空 frame。
- 本地筛选:候选 ∩ 阈值正确;unknown 不纳入;空集写 0 字节;列缺失 → 全 unknown 当日空。

## 相关

- 互补信号:`docs/superpowers/specs/2026-05-27-rs-line-trend-filter-design.md`(RS line vs MA)
- 配置段:`config.toml` `[rs_line]`
