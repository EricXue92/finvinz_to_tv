import pandas as pd
import numpy as np
import pytest

from hk_rs import compute_rs_table, filter_by_rs


def _flat_then_jump(start_price: float, jump_pct: float, n: int = 260):
    closes = [start_price] * (n - 1) + [start_price * (1 + jump_pct / 100)]
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-05", periods=n, freq="B"),
        "close": closes,
    })


def test_compute_rs_table_relative_to_hsi():
    # Universe of 5 tickers with monotonically increasing 12mo returns.
    klines = {
        f"HK.000{i:02d}": _flat_then_jump(100.0, jump_pct=10 + i * 5)
        for i in range(1, 6)
    }
    # HSI flat → relative score = absolute score, percentiles 0..99 should
    # rank ascending by jump_pct.
    hsi = _flat_then_jump(20000.0, jump_pct=0)
    table = compute_rs_table(klines, hsi)

    # Index should match input codes; rs_percentile in [0, 99]
    assert set(table.index) == set(klines.keys())
    assert table["rs_percentile"].between(0, 99).all()
    # Highest jump should get the highest percentile
    top = table["rs_percentile"].idxmax()
    assert top == "HK.00005"


def test_filter_by_rs_passthrough_for_missing():
    table = pd.DataFrame({"rs_percentile": [95, 50]}, index=["HK.AAA", "HK.BBB"])
    out = filter_by_rs(["HK.AAA", "HK.BBB", "HK.CCC"], table, threshold=90)
    # AAA passes; BBB fails; CCC missing → kept (matches rs_rating.py policy)
    assert set(out) == {"HK.AAA", "HK.CCC"}


def test_filter_by_rs_none_table_passthrough():
    out = filter_by_rs(["HK.AAA", "HK.BBB"], None, threshold=90)
    assert out == ["HK.AAA", "HK.BBB"]


from hk_rs import WEIGHTS_12M, WEIGHTS_3M, _score_from_kline


def test_score_from_kline_default_weights_unchanged():
    # 260 行平盘 + 末尾跳 +20% → 12M 分数应等于 0.4·0.2 + 0.2·0.2 + 0.2·0.2 + 0.2·0.2 = 0.20
    df = _flat_then_jump(100.0, jump_pct=20, n=260)
    score, reason = _score_from_kline(df)  # 默认 WEIGHTS_12M
    assert reason == "ok"
    assert abs(score - 0.20) < 1e-9


def test_score_from_kline_3m_weights_short_history_ok():
    # 70 行平盘 + 末尾跳 +10% → 3M 算法仅需 max(3)*21 + 1 = 64 行
    # 分数 = 0.5·0.1 + 0.3·0.1 + 0.2·0.1 = 0.10
    df = _flat_then_jump(100.0, jump_pct=10, n=70)
    score, reason = _score_from_kline(df, weights=WEIGHTS_3M)
    assert reason == "ok"
    assert abs(score - 0.10) < 1e-9


def test_score_from_kline_3m_weights_reject_when_below_min_rows():
    # 60 行 < 64 → short_history
    df = _flat_then_jump(100.0, jump_pct=10, n=60)
    score, reason = _score_from_kline(df, weights=WEIGHTS_3M)
    assert score is None
    assert reason == "short_history"
