from datetime import date as _date
from pathlib import Path

import pandas as pd

from us_rs_3m import (
    WEIGHTS_3M,
    _score_from_kline,
    compute_us_rs_3m_table,
)
from us_rs_3m import (
    cache_path,
    filter_by_rs,
    load_cache,
    save_cache,
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


def test_filter_by_rs_keeps_at_or_above_threshold():
    table = pd.DataFrame({
        "raw_score": [0.2, 0.1, 0.05],
        "rs_percentile": [95, 90, 50],
    }, index=["AAA", "BBB", "CCC"])
    out = filter_by_rs(["AAA", "BBB", "CCC"], table, threshold=90)
    assert set(out) == {"AAA", "BBB"}


def test_filter_by_rs_missing_passthrough():
    table = pd.DataFrame({"raw_score": [0.2], "rs_percentile": [95]}, index=["AAA"])
    out = filter_by_rs(["AAA", "ZZZ"], table, threshold=90)
    # ZZZ not in table → kept-as-missing (US long-side passthrough policy)
    assert set(out) == {"AAA", "ZZZ"}


def test_filter_by_rs_none_table_passthrough():
    out = filter_by_rs(["AAA", "BBB"], None, threshold=90)
    assert out == ["AAA", "BBB"]


def test_filter_by_rs_threshold_zero_passthrough():
    table = pd.DataFrame({
        "raw_score": [0.05],
        "rs_percentile": [10],
    }, index=["LOW"])
    out = filter_by_rs(["LOW"], table, threshold=0)
    assert out == ["LOW"]


def test_cache_path():
    p = cache_path(_date(2026, 5, 21), Path("/tmp/out"))
    assert p == Path("/tmp/out/state/rs_rating_3m_2026-05-21.csv")


def test_save_and_load_cache_roundtrip(tmp_path):
    df = pd.DataFrame({
        "raw_score": [0.2, 0.05],
        "rs_percentile": [95, 50],
    }, index=["AAA", "BBB"])
    save_cache(df, _date(2026, 5, 21), tmp_path)

    loaded = load_cache(_date(2026, 5, 21), tmp_path)
    assert loaded is not None
    assert list(loaded.index) == ["AAA", "BBB"]
    assert loaded.loc["AAA", "rs_percentile"] == 95
    assert abs(loaded.loc["AAA", "raw_score"] - 0.2) < 1e-9


def test_fetch_us_klines_yf_shapes(monkeypatch):
    """Smoke test that the fetcher returns the right DataFrame shape.

    We don't hit real yfinance — we stub _yf_download_with_retry to return
    a yfinance-shaped multiindex DataFrame.
    """
    import us_rs_3m

    def _fake_download(tickers, **kwargs):
        # Mimic yfinance batch shape: MultiIndex columns (ticker, field)
        idx = pd.date_range(end="2026-05-21", periods=80, freq="B")
        cols = pd.MultiIndex.from_product(
            [tickers, ["Open", "High", "Low", "Close", "Volume"]]
        )
        data = pd.DataFrame(100.0, index=idx, columns=cols)
        return data

    monkeypatch.setattr("us_rs_3m._yf_download_with_retry", _fake_download, raising=False)
    klines = us_rs_3m.fetch_us_klines_yf(["AAPL", "MSFT"], period="6mo", batch_size=500)
    assert set(klines.keys()) == {"AAPL", "MSFT"}
    for t, df in klines.items():
        assert "close" in df.columns
        assert "time_key" in df.columns
        assert len(df) == 80


def test_fetch_us_klines_yf_empty_input():
    import us_rs_3m
    assert us_rs_3m.fetch_us_klines_yf([], period="6mo") == {}
