import pandas as pd

from us_rs_3m import (
    WEIGHTS_3M,
    _score_from_kline,
    compute_us_rs_3m_table,
)


def _flat_then_jump(start_price: float, jump_pct: float, n: int = 90) -> pd.DataFrame:
    closes = [start_price] * (n - 1) + [start_price * (1 + jump_pct / 100)]
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=n, freq="B"),
        "close": closes,
    })


def test_score_from_kline_happy_path():
    # 70 行平盘 + 末尾跳 +10% → 0.5·0.1 + 0.3·0.1 + 0.2·0.1 = 0.10
    df = _flat_then_jump(100.0, jump_pct=10, n=70)
    score, reason = _score_from_kline(df)
    assert reason == "ok"
    assert abs(score - 0.10) < 1e-9


def test_score_from_kline_no_data():
    df = pd.DataFrame({"time_key": [], "close": []})
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "no_data"


def test_score_from_kline_short_history():
    # 63 行 < 64(= max(months)*21 + 1)→ short_history
    df = _flat_then_jump(100.0, jump_pct=10, n=63)
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "short_history"


def test_score_from_kline_zero_last():
    df = _flat_then_jump(100.0, jump_pct=-100, n=70)  # 末行价 = 0
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "zero_last"


def test_score_from_kline_zero_past():
    # 中间某天价 = 0(很罕见,但要测)
    closes = [100.0] * 70
    closes[-22] = 0.0  # R21 lookback 点
    df = pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=70, freq="B"),
        "close": closes,
    })
    score, reason = _score_from_kline(df)
    assert score is None
    assert reason == "zero_past"


def test_compute_table_relative_to_spy():
    klines = {
        f"T{i:02d}": _flat_then_jump(100.0, jump_pct=5 + i * 2, n=70)
        for i in range(1, 6)
    }
    spy = _flat_then_jump(400.0, jump_pct=0, n=70)
    table = compute_us_rs_3m_table(klines, spy)

    assert set(table.index) == set(klines.keys())
    assert "rs_percentile" in table.columns
    assert "raw_score" in table.columns
    assert table["rs_percentile"].between(0, 99).all()
    # 跳幅最大的应在最高百分位
    assert table["rs_percentile"].idxmax() == "T05"
    # raw_score 单调递增(因为 jump_pct 递增)
    ordered = table.sort_values("raw_score").index.tolist()
    assert ordered == sorted(klines.keys())


def test_compute_table_excludes_short_history():
    # 50 行 < 64 → 应被排除
    klines = {
        "GOOD": _flat_then_jump(100.0, jump_pct=10, n=70),
        "SHORT": _flat_then_jump(100.0, jump_pct=10, n=50),
    }
    spy = _flat_then_jump(400.0, jump_pct=0, n=70)
    table = compute_us_rs_3m_table(klines, spy)
    assert "GOOD" in table.index
    assert "SHORT" not in table.index


def test_compute_table_empty_when_all_short():
    klines = {"T01": _flat_then_jump(100.0, jump_pct=10, n=50)}
    spy = _flat_then_jump(400.0, jump_pct=0, n=70)
    table = compute_us_rs_3m_table(klines, spy)
    assert table.empty
    assert list(table.columns) == ["raw_score", "rs_percentile"]


def test_compute_table_spy_failure_falls_back_to_absolute(caplog):
    # SPY 数据不够 → fallback 到 spy_score=0(即绝对分数排名),记 warning
    klines = {"T01": _flat_then_jump(100.0, jump_pct=10, n=70)}
    spy = _flat_then_jump(400.0, jump_pct=0, n=50)  # < 64 → short_history
    with caplog.at_level("WARNING"):
        table = compute_us_rs_3m_table(klines, spy)
    assert "T01" in table.index
    assert any("SPY" in r.message for r in caplog.records)
