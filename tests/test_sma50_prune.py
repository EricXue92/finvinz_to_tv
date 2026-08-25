"""Tests for sma50_prune — daily prune of eod_seen_US.txt tickers whose close
has been below SMA50 for N consecutive completed days.

yfinance is never hit: the fetch layer is monkeypatched.
"""

from pathlib import Path

import pandas as pd
import pytest

import sma50_prune


def _series(closes: list[float]) -> pd.Series:
    idx = pd.bdate_range(end="2026-08-21", periods=len(closes))
    return pd.Series(closes, index=idx, dtype=float)


# --- find_sma50_drops (pure logic) ---


def test_two_consecutive_closes_below_sma50_is_dropped():
    closes = [100.0] * 58 + [90.0, 90.0]  # SMA50 ~99.6/99.8, both closes below
    drops = sma50_prune.find_sma50_drops({"AAA": _series(closes)})
    assert drops == ["AAA"]


def test_only_latest_close_below_sma50_is_kept():
    closes = [100.0] * 58 + [110.0, 90.0]  # day-2 above, only latest below
    drops = sma50_prune.find_sma50_drops({"AAA": _series(closes)})
    assert drops == []


def test_dipped_then_recovered_is_kept():
    closes = [100.0] * 58 + [90.0, 110.0]  # latest close back above SMA50
    drops = sma50_prune.find_sma50_drops({"AAA": _series(closes)})
    assert drops == []


def test_insufficient_history_is_kept():
    closes = [1.0] * 30  # < sma_period bars: SMA50 undefined -> keep
    drops = sma50_prune.find_sma50_drops({"AAA": _series(closes)})
    assert drops == []


def test_consecutive_days_knob():
    # below for exactly 2 days: dropped at consecutive_days=2, kept at 3
    closes = [100.0] * 58 + [90.0, 90.0]
    assert sma50_prune.find_sma50_drops(
        {"AAA": _series(closes)}, consecutive_days=3
    ) == []


# --- prune_us_master (I/O wrapper) ---


def _write_master(tmp_path: Path, tickers: list[str]) -> Path:
    p = tmp_path / "eod_seen_US.txt"
    p.write_text("\n".join(tickers) + "\n")
    return p


def test_prune_removes_drops_and_backs_up(tmp_path, monkeypatch):
    seen = _write_master(tmp_path, ["AAA", "BBB", "CCC"])
    weak = [100.0] * 58 + [90.0, 90.0]
    strong = [100.0] * 60
    monkeypatch.setattr(
        sma50_prune,
        "_fetch_daily_closes",
        lambda tickers: {
            "AAA": _series(weak),
            "BBB": _series(strong),
            "CCC": _series(strong),
        },
    )
    drops = sma50_prune.prune_us_master(seen, {"enabled": True})
    assert drops == ["AAA"]
    assert seen.read_text().split() == ["BBB", "CCC"]
    backups = list(tmp_path.glob("eod_seen_US.txt.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text().split() == ["AAA", "BBB", "CCC"]


def test_fetch_failure_skips_prune(tmp_path, monkeypatch):
    seen = _write_master(tmp_path, ["AAA", "BBB"])
    monkeypatch.setattr(sma50_prune, "_fetch_daily_closes", lambda tickers: None)
    drops = sma50_prune.prune_us_master(seen, {"enabled": True})
    assert drops == []
    assert seen.read_text().split() == ["AAA", "BBB"]
    assert list(tmp_path.glob("*.bak.*")) == []


def test_ticker_missing_from_fetch_is_kept(tmp_path, monkeypatch):
    seen = _write_master(tmp_path, ["AAA", "BBB"])
    weak = [100.0] * 58 + [90.0, 90.0]
    monkeypatch.setattr(
        sma50_prune,
        "_fetch_daily_closes",
        lambda tickers: {"AAA": _series(weak)},  # BBB absent -> keep
    )
    drops = sma50_prune.prune_us_master(seen, {"enabled": True})
    assert drops == ["AAA"]
    assert seen.read_text().split() == ["BBB"]


def test_disabled_config_is_noop(tmp_path, monkeypatch):
    seen = _write_master(tmp_path, ["AAA"])

    def _boom(tickers):  # pragma: no cover - must not be called
        raise AssertionError("fetch should not run when disabled")

    monkeypatch.setattr(sma50_prune, "_fetch_daily_closes", _boom)
    drops = sma50_prune.prune_us_master(seen, {"enabled": False})
    assert drops == []
    assert seen.read_text().split() == ["AAA"]


def test_missing_master_is_noop(tmp_path, monkeypatch):
    seen = tmp_path / "eod_seen_US.txt"  # never written
    monkeypatch.setattr(
        sma50_prune, "_fetch_daily_closes", lambda tickers: {}
    )
    drops = sma50_prune.prune_us_master(seen, {"enabled": True})
    assert drops == []
    assert not seen.exists()
