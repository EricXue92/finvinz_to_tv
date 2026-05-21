import pandas as pd
import pytest

from hk_eod import build_metrics_frame, apply_strategy_filters, dedup_by_priority


def _make_kline(closes, volumes=None, highs=None, lows=None):
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c * 1.02 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    dates = pd.date_range(end="2026-05-05", periods=n, freq="B")
    return pd.DataFrame({
        "time_key": dates,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def test_build_metrics_frame_basic():
    # 252 days of flat $50 close, then today's close at $52.5 (gap +5%, RVol 2x)
    closes = [50.0] * 251 + [52.5]
    volumes = [1_000_000] * 251 + [2_000_000]
    klines = {"HK.00001": _make_kline(closes, volumes=volumes)}
    caps = {"HK.00001": 5_000_000_000.0}
    df = build_metrics_frame(klines, caps)

    row = df.loc["HK.00001"]
    assert row["market_cap"] == 5_000_000_000.0
    assert row["last_price"] == pytest.approx(52.5)
    assert row["prev_close"] == pytest.approx(50.0)
    assert row["gap_pct"] == pytest.approx(5.0)
    assert row["rvol"] == pytest.approx(2.0)
    assert row["avg_vol_20d"] == pytest.approx(1_050_000)  # 19×1M + 1×2M / 20
    assert row["avg_dollar_vol_20d"] == pytest.approx(52.5 * 1_050_000)
    assert row["sma50"] == pytest.approx((49 * 50.0 + 52.5) / 50)
    assert row["sma200"] == pytest.approx((199 * 50.0 + 52.5) / 200)
    assert row["above_sma50"] is True or row["above_sma50"] == True
    assert row["above_sma200"] is True or row["above_sma200"] == True
    assert row["perf_4w"] == pytest.approx(5.0)  # 20 trading days back was 50.0


def test_build_metrics_frame_skips_short_history():
    # Only 30 days of data — not enough for SMA50 or SMA200, but should still
    # return a row with NaN for those columns.
    closes = [50.0] * 29 + [52.0]
    klines = {"HK.00002": _make_kline(closes)}
    caps = {"HK.00002": 1_000_000_000.0}
    df = build_metrics_frame(klines, caps)
    row = df.loc["HK.00002"]
    assert pd.isna(row["sma200"])
    assert row["above_sma200"] is False
    # 30 < 50 so SMA50 should also be NaN
    assert pd.isna(row["sma50"])
    assert row["above_sma50"] is False


def test_apply_strategy_filters_baseline_and_priority_inputs():
    df = pd.DataFrame([
        # Passes everything + gap 5% + rvol 3 → EarningsGap, HighVolume, GapUp, RS
        dict(market_cap=1e9, last_price=25.0, prev_close=23.81, gap_pct=5.0,
             rvol=3.0, avg_vol_20d=1e6, avg_dollar_vol_20d=2e8, adr_pct=5.0,
             sma50=22.0, sma200=20.0, above_sma50=True, above_sma200=True,
             perf_4w=10.0, perf_13w=20.0, perf_26w=30.0, perf_ytd=40.0,
             perf_52w=50.0, consecutive_up_days=2),
        # Cap too small → drops out everywhere
        dict(market_cap=1e8, last_price=25.0, prev_close=23.81, gap_pct=5.0,
             rvol=3.0, avg_vol_20d=1e6, avg_dollar_vol_20d=2e8, adr_pct=5.0,
             sma50=22.0, sma200=20.0, above_sma50=True, above_sma200=True,
             perf_4w=10.0, perf_13w=20.0, perf_26w=30.0, perf_ytd=40.0,
             perf_52w=50.0, consecutive_up_days=2),
    ], index=["HK.00001", "HK.00002"])

    settings = dict(min_market_cap=3e8, min_dollar_volume=1e8,
                    min_avg_volume=5e5, min_adr_percent=4.0, min_price=20.0)
    longs = [
        {"key": "earnings_gap", "min_relative_volume": 3, "min_gap_percent": 3.0},
        {"key": "high_volume", "min_relative_volume": 3},
        {"key": "gap_up", "min_gap_percent": 5.0},
    ]
    leaders = [
        {"min_perf_4w": 30}, {"min_perf_13w": 50}, {"min_perf_26w": 100},
        {"min_perf_ytd": 100}, {"min_perf_52w": 150},
    ]
    out = apply_strategy_filters(df, settings, longs, leaders, rs_enabled=True)

    assert out["EarningsGap"] == ["HK.00001"]
    assert out["HighVolume"] == ["HK.00001"]
    assert out["GapUp"] == ["HK.00001"]
    assert out["Leaders"] == []  # perf_4w 10 < 30, etc.
    assert out["RS"] == ["HK.00001"]


def test_dedup_by_priority_strips_lower_priority_duplicates():
    raw = {
        "EarningsGap": ["A", "B"],
        "HighVolume":  ["B", "C"],     # B already in EG → dropped
        "GapUp":       ["C", "D"],     # C already in HV → dropped
        "Leaders":     ["D", "E"],     # D already in GU → dropped
        "RS":          ["A", "F"],     # A already in EG → dropped
    }
    out = dedup_by_priority(raw)
    assert out == {
        "EarningsGap": ["A", "B"],
        "HighVolume":  ["C"],
        "GapUp":       ["D"],
        "Leaders":     ["E"],
        "RS":          ["F"],
    }


def test_dedup_by_priority_excludes_rs_when_priority_is_subset():
    """When the caller passes a 4-strategy priority subset (RS omitted),
    RS must be absent from the output dict — the caller is responsible
    for splicing it back in untouched. This pins the contract used by
    run_hk_eod to carve RS out of within-day priority dedup."""
    raw = {
        "EarningsGap": ["A"],
        "HighVolume":  ["B"],
        "GapUp":       ["C"],
        "Leaders":     ["D", "E"],
        "RS":          ["A", "D", "F"],  # would normally be dropped to ["F"]
    }
    out = dedup_by_priority(
        raw,
        priority=["EarningsGap", "HighVolume", "GapUp", "Leaders"],
    )
    assert "RS" not in out
    assert out == {
        "EarningsGap": ["A"],
        "HighVolume":  ["B"],
        "GapUp":       ["C"],
        "Leaders":     ["D", "E"],
    }


from hk_eod import filter_hk_ipo_candidates


def _ipo_metrics_row(
    market_cap=5e9,
    last_price=25.0,
    avg_vol_20d=1_000_000.0,
    avg_dollar_vol_20d=2.5e8,
    adr_pct=5.0,
    sma50=22.0,
    sma200=20.0,
    above_sma50=True,
    above_sma200=True,
):
    """Returns a metrics-frame row dict that PASSES all baseline gates."""
    return dict(
        market_cap=market_cap,
        last_price=last_price,
        avg_vol_20d=avg_vol_20d,
        avg_dollar_vol_20d=avg_dollar_vol_20d,
        adr_pct=adr_pct,
        sma50=sma50,
        sma200=sma200,
        above_sma50=above_sma50,
        above_sma200=above_sma200,
    )


def _hk_settings_default():
    return {
        "min_market_cap": 300_000_000,
        "min_price": 20.0,
        "min_avg_volume": 500_000,
        "min_dollar_volume": 100_000_000,
        "min_adr_percent": 3.5,
        "min_rs_percentile_longs_3m": 90,
    }


def test_filter_hk_ipo_drops_when_history_below_20_days():
    # 15 行历史 < 20 → 立刻砍掉，不进入任何 metric 闸门
    closes = [25.0] * 15
    klines = {"HK.NEW1": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.NEW1": _ipo_metrics_row()}, orient="index"
    )
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=None, hk_settings=_hk_settings_default()
    )
    assert kept == []
    assert drops["min_history"] == 1


def test_filter_hk_ipo_keeps_20_to_49_days_without_rs_check():
    # 30 行历史 — 过 20-day floor，未达 64 天 RS 触发线 → 走现有阶梯放行
    closes = [25.0] * 30
    klines = {"HK.MID": _make_kline(closes)}
    # 短历史：SMA50/SMA200 都是 NaN，above_sma 字段为 False（与 build_metrics_frame 一致）
    metrics = pd.DataFrame.from_dict(
        {"HK.MID": _ipo_metrics_row(
            sma50=float("nan"), sma200=float("nan"),
            above_sma50=False, above_sma200=False,
        )},
        orient="index",
    )
    # 即使 rs_table_3m 里把 HK.MID 设成 RS=10，<64 天的 ticker 也不走 RS 检查
    rs_table = pd.DataFrame({"rs_percentile": [10]}, index=["HK.MID"])
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == ["HK.MID"]
    assert drops["rs_3m"] == 0
    assert drops["rs_3m_missing"] == 0


def test_filter_hk_ipo_keeps_when_rs_3m_at_or_above_threshold():
    # 70 行历史 ≥ 64 → 触发 3M RS 闸门；表内 percentile = 95 ≥ 90 → 保留
    closes = [25.0] * 70
    klines = {"HK.A": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.A": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    rs_table = pd.DataFrame({"rs_percentile": [95]}, index=["HK.A"])
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == ["HK.A"]
    assert drops["rs_3m"] == 0


def test_filter_hk_ipo_drops_when_rs_3m_below_threshold():
    # 70 行历史 ≥ 64 → 触发 3M RS 闸门；表内 percentile = 80 < 90 → drop
    closes = [25.0] * 70
    klines = {"HK.B": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.B": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    rs_table = pd.DataFrame({"rs_percentile": [80]}, index=["HK.B"])
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == []
    assert drops["rs_3m"] == 1


def test_filter_hk_ipo_drops_when_64days_but_missing_from_rs_table():
    # 70 行历史 ≥ 64 → 触发 3M RS 闸门；但 ticker 不在表里 → drop（rs_3m_missing）
    # 这种情况一般是 _score_from_kline 因 zero_last/zero_past 排掉了
    closes = [25.0] * 70
    klines = {"HK.C": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.C": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    rs_table = pd.DataFrame({"rs_percentile": [95]}, index=["HK.OTHER"])  # 不含 HK.C
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=_hk_settings_default()
    )
    assert kept == []
    assert drops["rs_3m_missing"] == 1


def test_filter_hk_ipo_skips_rs_gate_when_table_is_none():
    # 70 行历史 ≥ 64 但 rs_table_3m=None (HSI fetch 失败) → 回退到只有 metric 闸门
    # day-20 floor 仍生效，但 RS 闸门跳过
    closes = [25.0] * 70
    klines = {"HK.D": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.D": _ipo_metrics_row(sma200=float("nan"), above_sma200=False)},
        orient="index",
    )
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=None, hk_settings=_hk_settings_default()
    )
    assert kept == ["HK.D"]
    assert drops["rs_3m"] == 0
    assert drops["rs_3m_missing"] == 0


def test_filter_hk_ipo_counts_no_metrics_when_code_missing_from_metrics_frame():
    # ticker has 30 rows of klines but is not in the metrics frame
    # (build_metrics_frame upstream dropped it for cap NaN, market_cap missing, etc.)
    # → counted in drops['no_metrics'], not in drops['min_history'] / cap / price
    closes = [25.0] * 30
    klines = {"HK.MISS": _make_kline(closes)}
    metrics = pd.DataFrame.from_dict(
        {"HK.OTHER": _ipo_metrics_row()}, orient="index"
    )
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=None, hk_settings=_hk_settings_default()
    )
    assert kept == []
    assert drops["no_metrics"] == 1
    assert drops["min_history"] == 0
    assert drops["cap"] == 0


def test_filter_hk_ipo_threshold_zero_disables_entire_3m_gate():
    # min_rs_percentile_longs_3m = 0 关闭整个 3M 闸门:
    # - percentile 任意低都不算 drop
    # - 不在表里也不算 drop
    # 这与 filter_by_rs 的 threshold<=0 短路一致.
    closes = [25.0] * 70
    klines = {
        "HK.LOW": _make_kline(closes),   # 70 行, RS = 10 (会被默认阈值砍)
        "HK.MISS": _make_kline(closes),  # 70 行, 不在表里 (会被默认阈值砍)
    }
    metrics = pd.DataFrame.from_dict(
        {
            "HK.LOW":  _ipo_metrics_row(sma200=float("nan"), above_sma200=False),
            "HK.MISS": _ipo_metrics_row(sma200=float("nan"), above_sma200=False),
        },
        orient="index",
    )
    rs_table = pd.DataFrame({"rs_percentile": [10]}, index=["HK.LOW"])
    settings = _hk_settings_default()
    settings["min_rs_percentile_longs_3m"] = 0
    kept, drops = filter_hk_ipo_candidates(
        klines, metrics, rs_table_3m=rs_table, hk_settings=settings
    )
    assert set(kept) == {"HK.LOW", "HK.MISS"}
    assert drops["rs_3m"] == 0
    assert drops["rs_3m_missing"] == 0
