import numpy as np
import pandas as pd

from rs_line import compute_rs_line_features, params_from_config, summarize_rs_line


def _kline(closes, start="2026-01-01"):
    idx = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame({"time_key": idx, "close": np.asarray(closes, dtype=float)})


def _flat_bench(n, level=100.0, start="2026-01-01"):
    return _kline([level] * n, start=start)


def test_persistently_above_ma_not_flagged():
    n = 80
    stock = _kline([100 + i for i in range(n)])      # rising
    bench = _flat_bench(n)
    out = compute_rs_line_features({"UP": stock}, bench, ma_length=21,
                                   persistence_window=20, min_history=42)
    assert int(out.loc["UP", "rs_below_ma"]) == 0
    assert int(out.loc["UP", "rs_days_below_ma"]) == 0
    assert out.loc["UP", "rs_frac_below_ma"] < 0.25


def test_persistently_below_ma_flagged():
    n = 80
    stock = _kline([200 - i for i in range(n)])      # falling
    bench = _flat_bench(n)
    out = compute_rs_line_features({"DOWN": stock}, bench, ma_length=21,
                                   persistence_window=20, min_history=42)
    assert int(out.loc["DOWN", "rs_below_ma"]) == 1
    assert int(out.loc["DOWN", "rs_days_below_ma"]) >= 10
    assert out.loc["DOWN", "rs_frac_below_ma"] > 0.75


def test_late_two_bar_dip_keeps_streak_small():
    n = 80
    closes = [100 + i for i in range(n - 2)] + [100, 95]  # late 2-bar dip
    stock = _kline(closes)
    bench = _flat_bench(n)
    out = compute_rs_line_features({"CHOP": stock}, bench, ma_length=21,
                                   persistence_window=20, min_history=42)
    assert int(out.loc["CHOP", "rs_days_below_ma"]) <= 2
    assert out.loc["CHOP", "rs_frac_below_ma"] < 0.5


def test_scale_invariance_spy_vs_spx():
    n = 80
    stock = _kline([200 - i for i in range(n)])
    bench_spy = _flat_bench(n, level=50.0)
    bench_spx = _flat_bench(n, level=500.0)   # x10
    a = compute_rs_line_features({"X": stock}, bench_spy, min_history=42)
    b = compute_rs_line_features({"X": stock}, bench_spx, min_history=42)
    assert int(a.loc["X", "rs_below_ma"]) == int(b.loc["X", "rs_below_ma"])
    assert int(a.loc["X", "rs_days_below_ma"]) == int(b.loc["X", "rs_days_below_ma"])
    assert a.loc["X", "rs_frac_below_ma"] == b.loc["X", "rs_frac_below_ma"]


def test_short_history_excluded():
    n = 30  # < min_history
    stock = _kline([100 + i for i in range(n)])
    bench = _flat_bench(n)
    out = compute_rs_line_features({"NEW": stock}, bench, min_history=42)
    assert "NEW" not in out.index


def test_sma_path_runs():
    n = 80
    stock = _kline([200 - i for i in range(n)])
    bench = _flat_bench(n)
    out = compute_rs_line_features({"D": stock}, bench, ma_type="sma",
                                   ma_length=21, min_history=42)
    assert int(out.loc["D", "rs_below_ma"]) == 1


def test_date_alignment_handles_missing_bar():
    n = 80
    bench = _flat_bench(n)
    stock = _kline([200 - i for i in range(n)])
    stock = stock.drop(index=40).reset_index(drop=True)  # drop one bar
    out = compute_rs_line_features({"D": stock}, bench, min_history=42)
    assert "D" in out.index
    assert not pd.isna(out.loc["D", "rs_below_ma"])


def test_empty_benchmark_returns_empty_schema():
    out = compute_rs_line_features({"X": _kline([1, 2, 3])}, None)
    assert list(out.columns) == ["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"]
    assert out.empty


def test_params_from_config_defaults_and_overrides():
    assert params_from_config({}) == {
        "ma_length": 21, "ma_type": "ema",
        "persistence_window": 20, "min_history": 42,
    }
    cfg = {"rs_line": {"ma_length": 50, "ma_type": "sma",
                       "persistence_window": 30, "min_history": 60}}
    assert params_from_config(cfg) == {
        "ma_length": 50, "ma_type": "sma",
        "persistence_window": 30, "min_history": 60,
    }


def test_summarize_counts_and_lists_below():
    feats = pd.DataFrame.from_dict(
        {"AAA": (0, 0, 0.05), "BBB": (1, 14, 0.90), "CCC": (1, 3, 0.40)},
        orient="index", columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"],
    )
    s = summarize_rs_line(["AAA", "BBB", "CCC"], feats)
    assert "1 above MA" in s
    assert "2 below" in s
    assert "BBB" in s and "14d" in s   # most-persistent listed first


def test_summarize_handles_missing_and_none():
    assert summarize_rs_line(["X"], None) is None
    feats = pd.DataFrame(columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"])
    assert summarize_rs_line(["X"], feats) is None
    feats2 = pd.DataFrame.from_dict({"AAA": (0, 0, 0.0)}, orient="index",
                                    columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"])
    assert "1 above MA, 0 below" in summarize_rs_line(["AAA", "ZZZ"], feats2)
