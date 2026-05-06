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
