import math

import numpy as np
import pandas as pd


def _make_kline(closes: list[float], highs=None, lows=None, volumes=None) -> pd.DataFrame:
    n = len(closes)
    highs = highs if highs is not None else [c * 1.02 for c in closes]
    lows = lows if lows is not None else [c * 0.98 for c in closes]
    volumes = volumes if volumes is not None else [1_000_000.0] * n
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=n, freq="B"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def test_build_ipo_metrics_short_history_returns_nans():
    from us_ipo import _build_ipo_metrics
    klines = {"NEW": _make_kline([25.0] * 15)}
    caps = {"NEW": 1e9}
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["NEW"]
    assert row["market_cap"] == 1e9
    assert row["last_price"] == 25.0
    assert math.isnan(row["avg_vol_20d"])
    assert math.isnan(row["sma50"])
    assert row["above_sma50"] is False
    assert row["above_sma200"] is False


def test_build_ipo_metrics_20day_metrics_populated():
    from us_ipo import _build_ipo_metrics
    klines = {"OK": _make_kline([20.0] * 25)}
    caps = {"OK": 5e9}
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["OK"]
    assert row["avg_vol_20d"] == 1_000_000.0
    assert abs(row["avg_dollar_vol_20d"] - 20.0 * 1_000_000.0) < 1e-6
    assert row["adr_pct"] > 0  # (high - low) / close ≠ 0


def test_build_ipo_metrics_sma50_populated_at_50_days():
    from us_ipo import _build_ipo_metrics
    klines = {"OK": _make_kline([20.0] * 55)}
    caps = {"OK": 5e9}
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["OK"]
    assert not math.isnan(row["sma50"])
    assert math.isnan(row["sma200"])  # < 200 days


def test_build_ipo_metrics_missing_cap_is_nan():
    from us_ipo import _build_ipo_metrics
    klines = {"NOCAP": _make_kline([25.0] * 30)}
    caps = {}  # no cap for NOCAP
    metrics = _build_ipo_metrics(klines, caps)
    row = metrics.loc["NOCAP"]
    assert math.isnan(row["market_cap"])


def test_build_ipo_metrics_skips_single_row():
    """1-row klines must be skipped (len(df) < 2 guard, parity with hk_eod)."""
    from us_ipo import _build_ipo_metrics
    klines = {"ONEROW": _make_kline([25.0] * 1)}
    caps = {"ONEROW": 1e9}
    metrics = _build_ipo_metrics(klines, caps)
    assert metrics.empty
