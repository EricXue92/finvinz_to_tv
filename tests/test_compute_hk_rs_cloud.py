"""Tests for scripts/compute_hk_rs_cloud.py — the GH Actions entrypoint.

External dependencies (HKEX universe fetch, yfinance batch download, HSI
fetch) are fully monkeypatched so the unit tests are fast and deterministic.
The script's real integration test is the workflow's first workflow_dispatch
run.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

# scripts/ is not a package; tests need a path hack to import.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import compute_hk_rs_cloud as cloud  # noqa: E402


def _flat_then_jump(start: float, jump_pct: float, n: int = 260) -> pd.DataFrame:
    """260 rows so both 12M (≥253) and 3M (≥64) windows score.

    Includes open/high/low/volume columns so the DataFrame also satisfies
    build_metrics_frame (which needs all OHLCV fields).
    """
    closes = [start] * (n - 1) + [start * (1 + jump_pct / 100)]
    volume = 1_000_000.0
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-21", periods=n, freq="B"),
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [volume] * n,
    })


def test_main_writes_combined_csv_and_exits_zero_when_coverage_above_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path: 3/3 universe fetched → combined CSV written → exit 0."""
    universe = ["0001", "0700", "9999"]
    monkeypatch.setattr(cloud, "fetch_hkex_equities", lambda: universe)
    monkeypatch.setattr(cloud, "fetch_hsi_kline_yf", lambda **kw: _flat_then_jump(20000.0, 0))
    monkeypatch.setattr(
        cloud, "fetch_hk_klines_yf",
        lambda codes, **kw: {
            f"HK.0{c}": _flat_then_jump(100.0, jump_pct=5 + i * 5)
            for i, c in enumerate(codes)
        },
    )
    monkeypatch.setattr(cloud, "_DATA_DIR", tmp_path / "data" / "hk_rs")
    monkeypatch.setattr(cloud, "_METRICS_DIR", tmp_path / "data" / "hk_metrics")
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 0
    csv = tmp_path / "data" / "hk_rs" / "2026-05-22.csv"
    assert csv.exists()
    df = pd.read_csv(csv, index_col="code")
    assert set(df.index) == {"HK.00001", "HK.00700", "HK.09999"}
    assert "rs_percentile_12m" in df.columns
    assert "rs_percentile_3m" in df.columns


def test_main_exits_nonzero_when_coverage_below_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """1 of 5 universe codes fetched = 20% coverage → exit 1, no CSV."""
    universe = ["0001", "0700", "9999", "0005", "0011"]
    monkeypatch.setattr(cloud, "fetch_hkex_equities", lambda: universe)
    monkeypatch.setattr(cloud, "fetch_hsi_kline_yf", lambda **kw: _flat_then_jump(20000.0, 0))
    monkeypatch.setattr(
        cloud, "fetch_hk_klines_yf",
        lambda codes, **kw: {"HK.00001": _flat_then_jump(100.0, 5)},  # 1/5 only
    )
    monkeypatch.setattr(cloud, "_DATA_DIR", tmp_path / "data" / "hk_rs")
    monkeypatch.setattr(cloud, "_METRICS_DIR", tmp_path / "data" / "hk_metrics")
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 1
    assert not (tmp_path / "data" / "hk_rs" / "2026-05-22.csv").exists()


def test_main_exits_nonzero_when_universe_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty HKEX universe → exit 1, no compute attempted."""
    monkeypatch.setattr(cloud, "fetch_hkex_equities", lambda: [])
    monkeypatch.setattr(cloud, "_DATA_DIR", tmp_path / "data" / "hk_rs")
    monkeypatch.setattr(cloud, "_METRICS_DIR", tmp_path / "data" / "hk_metrics")
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 1
    assert not (tmp_path / "data" / "hk_rs" / "2026-05-22.csv").exists()


def test_main_prunes_files_older_than_retention_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After a successful write, files older than 14 days are deleted."""
    data_dir = tmp_path / "data" / "hk_rs"
    data_dir.mkdir(parents=True)
    (data_dir / "2026-05-09.csv").write_text("code,rs_percentile_12m,rs_percentile_3m\n")  # 13 days
    (data_dir / "2026-05-07.csv").write_text("code,rs_percentile_12m,rs_percentile_3m\n")  # 15 days
    (data_dir / "README.md").write_text("docs")  # non-date, keep

    metrics_dir = tmp_path / "data" / "hk_metrics"
    metrics_dir.mkdir(parents=True)
    (metrics_dir / "2026-05-09.csv").write_text("code,last_price\n")  # 13 days, keep
    (metrics_dir / "2026-05-07.csv").write_text("code,last_price\n")  # 15 days, prune

    universe = ["0001", "0700", "9999"]
    monkeypatch.setattr(cloud, "fetch_hkex_equities", lambda: universe)
    monkeypatch.setattr(cloud, "fetch_hsi_kline_yf", lambda **kw: _flat_then_jump(20000.0, 0))
    monkeypatch.setattr(
        cloud, "fetch_hk_klines_yf",
        lambda codes, **kw: {
            f"HK.0{c}": _flat_then_jump(100.0, 5 + i * 5) for i, c in enumerate(codes)
        },
    )
    monkeypatch.setattr(cloud, "_DATA_DIR", data_dir)
    monkeypatch.setattr(cloud, "_METRICS_DIR", metrics_dir)
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 0
    assert (data_dir / "2026-05-22.csv").exists(), "today's file written"
    assert (data_dir / "2026-05-09.csv").exists(), "13-day-old file kept"
    assert not (data_dir / "2026-05-07.csv").exists(), "15-day-old file pruned"
    assert (data_dir / "README.md").exists(), "non-date file untouched"
    assert (metrics_dir / "2026-05-09.csv").exists(), "13-day-old metrics file kept"
    assert not (metrics_dir / "2026-05-07.csv").exists(), "15-day-old metrics file pruned"


def test_main_also_writes_metrics_csv_without_cap_or_bool_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Happy path also publishes data/hk_metrics/<date>.csv, minus the
    cap/bool columns that are local-only."""
    universe = ["0001", "0700", "9999"]
    monkeypatch.setattr(cloud, "fetch_hkex_equities", lambda: universe)
    monkeypatch.setattr(cloud, "fetch_hsi_kline_yf", lambda **kw: _flat_then_jump(20000.0, 0))
    monkeypatch.setattr(
        cloud, "fetch_hk_klines_yf",
        lambda codes, **kw: {
            f"HK.0{c}": _flat_then_jump(100.0, jump_pct=5 + i * 5)
            for i, c in enumerate(codes)
        },
    )
    monkeypatch.setattr(cloud, "_DATA_DIR", tmp_path / "data" / "hk_rs")
    monkeypatch.setattr(cloud, "_METRICS_DIR", tmp_path / "data" / "hk_metrics")
    monkeypatch.setattr(cloud, "_today", lambda: date(2026, 5, 22))

    exit_code = cloud.main()

    assert exit_code == 0
    metrics_csv = tmp_path / "data" / "hk_metrics" / "2026-05-22.csv"
    assert metrics_csv.exists()
    df = pd.read_csv(metrics_csv, index_col="code")
    assert set(df.index) == {"HK.00001", "HK.00700", "HK.09999"}
    # k-line-derived columns present; cap + bool columns dropped.
    assert "last_price" in df.columns
    assert "gap_pct" in df.columns
    assert "market_cap" not in df.columns
    assert "above_sma50" not in df.columns
    assert "above_sma200" not in df.columns
