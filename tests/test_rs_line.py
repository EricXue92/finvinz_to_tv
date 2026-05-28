import numpy as np
import pandas as pd

from rs_line import compute_rs_direction, compute_rs_line_features, params_from_config, summarize_rs_line


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
        "lookback": 5, "tolerance": 0.005,
    }
    cfg = {"rs_line": {"ma_length": 50, "ma_type": "sma",
                       "persistence_window": 30, "min_history": 60}}
    assert params_from_config(cfg) == {
        "ma_length": 50, "ma_type": "sma",
        "persistence_window": 30, "min_history": 60,
        "lookback": 5, "tolerance": 0.005,
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


def test_summarize_all_below_none_above():
    feats = pd.DataFrame.from_dict(
        {"AAA": (1, 5, 0.7), "BBB": (1, 9, 0.8)},
        orient="index", columns=["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"],
    )
    s = summarize_rs_line(["AAA", "BBB"], feats)
    assert "0 above MA, 2 below" in s
    assert s.index("BBB") < s.index("AAA")   # 9d before 5d


# ---------------------------------------------------------------------------
# compute_rs_direction tests
# ---------------------------------------------------------------------------


def _const_bench(n, start="2026-01-01"):
    return _kline([100.0] * n, start=start)


def test_direction_positive_on_rising_ratio():
    n = 80
    closes = [10.0 * (1.01 ** i) for i in range(n)]  # steadily rising vs flat bench
    feats = compute_rs_direction({"UP": _kline(closes)}, _const_bench(n))
    assert "UP" in feats.index
    assert feats.loc["UP", "rs_ema_chg_5d"] > 0


def test_direction_negative_on_falling_ratio():
    n = 80
    closes = [10.0 * (1.01 ** i) for i in range(n - 10)] + [
        10.0 * (1.01 ** (n - 10)) * (0.97 ** j) for j in range(1, 11)
    ]  # rises then rolls over in the last ~2 weeks
    feats = compute_rs_direction({"DN": _kline(closes)}, _const_bench(n))
    assert feats.loc["DN", "rs_ema_chg_5d"] < 0


def test_direction_flat_is_near_zero():
    n = 80
    feats = compute_rs_direction({"FLAT": _kline([10.0] * n)}, _const_bench(n))
    assert abs(feats.loc["FLAT", "rs_ema_chg_5d"]) < 1e-9


def test_direction_scale_invariant_to_benchmark():
    n = 80
    closes = [10.0 + 0.05 * i for i in range(n)]
    a = compute_rs_direction({"T": _kline(closes)}, _kline([100.0] * n))
    b = compute_rs_direction({"T": _kline(closes)}, _kline([1000.0] * n))  # ×10
    assert abs(a.loc["T", "rs_ema_chg_5d"] - b.loc["T", "rs_ema_chg_5d"]) < 1e-12


def test_direction_short_history_excluded():
    feats = compute_rs_direction({"SHORT": _kline([10.0] * 20)}, _const_bench(20))
    assert "SHORT" not in feats.index  # < min_history => unknown => omitted


def test_direction_lookback_measures_five_bars():
    # EMA of a ramp is monotone; chg over 5 bars must be strictly positive.
    n = 80
    closes = [10.0 + 0.1 * i for i in range(n)]
    feats = compute_rs_direction({"R": _kline(closes)}, _const_bench(n), lookback=5)
    assert feats.loc["R", "rs_ema_chg_5d"] > 0
    assert "rs_ema" in feats.columns and feats.loc["R", "rs_ema"] > 0


def test_params_from_config_includes_lookback_and_tolerance():
    cfg = {"rs_line": {"lookback": 7, "tolerance": 0.003}}
    p = params_from_config(cfg)
    assert p["lookback"] == 7
    assert p["tolerance"] == 0.003


def test_params_from_config_defaults_lookback_tolerance():
    p = params_from_config({})
    assert p["lookback"] == 5
    assert p["tolerance"] == 0.005
