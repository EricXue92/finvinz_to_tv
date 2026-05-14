"""Tests for the rs_rating local-cache fallback path.

The happy path (fresh download) and the same-day cache short-circuit
have no tests; they require live network or extensive plumbing to mock.
What this file covers is the failure path: after `urlopen` errors on
every retry, the fetcher should fall back to the most recent
`rs_rating_*.csv` within `_FALLBACK_MAX_AGE_DAYS`. Beyond that window
it should still return None.
"""

from __future__ import annotations

from pathlib import Path
from urllib.error import URLError

import pytest

import rs_rating

_FIXTURE_CSV = "Ticker,Percentile\nAAPL,95\nMSFT,88\n"


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real retry schedule sleeps 10s+30s between attempts. Skip
    the waits so the test doesn't take 40s when urlopen fails 3x."""
    monkeypatch.setattr(rs_rating.time, "sleep", lambda *_: None)


def _write_cache(state_dir: Path, date_stem: str, csv_text: str = _FIXTURE_CSV) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    p = state_dir / f"rs_rating_{date_stem}.csv"
    p.write_text(csv_text, encoding="utf-8")
    return p


def test_fallback_picks_recent_within_3_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    # Today = 2026_05_14. 2 days old = 2026_05_12 (valid, should win).
    # 5 days old = 2026_05_09 (too old). Bad-stem file should be skipped.
    _write_cache(state_dir, "2026_05_12")
    _write_cache(state_dir, "2026_05_09", "Ticker,Percentile\nGOOG,50\n")
    (state_dir / "rs_rating_not_a_date.csv").write_text("junk", encoding="utf-8")

    def _always_fail(*_a, **_kw):
        raise URLError("simulated DNS failure")

    monkeypatch.setattr(rs_rating, "urlopen", _always_fail)

    table = rs_rating.fetch_rs_table(tmp_path, "2026_05_14")

    assert table == {"AAPL": 95, "MSFT": 88}, (
        "expected fallback to read the 2-day-old fixture, not the 5-day-old one"
    )
    # And we should NOT have written a same-day cache (so a later rerun retries net).
    assert not (state_dir / "rs_rating_2026_05_14.csv").exists()


def test_no_fallback_when_only_stale_caches_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    _write_cache(state_dir, "2026_05_09")  # 5 days old, beyond the 3-day cap

    def _always_fail(*_a, **_kw):
        raise URLError("simulated DNS failure")

    monkeypatch.setattr(rs_rating, "urlopen", _always_fail)

    table = rs_rating.fetch_rs_table(tmp_path, "2026_05_14")
    assert table is None


def test_no_fallback_when_state_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # state/ doesn't exist at all → fetch fails → no fallback candidates.
    def _always_fail(*_a, **_kw):
        raise URLError("simulated DNS failure")

    monkeypatch.setattr(rs_rating, "urlopen", _always_fail)

    table = rs_rating.fetch_rs_table(tmp_path, "2026_05_14")
    assert table is None


def test_fallback_skips_future_dated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clock skew defense: a future-dated cache file must not be chosen
    over an older valid one."""
    state_dir = tmp_path / "state"
    _write_cache(state_dir, "2026_05_15", "Ticker,Percentile\nXXX,1\n")  # future
    _write_cache(state_dir, "2026_05_13")  # 1 day old, valid

    def _always_fail(*_a, **_kw):
        raise URLError("simulated DNS failure")

    monkeypatch.setattr(rs_rating, "urlopen", _always_fail)

    table = rs_rating.fetch_rs_table(tmp_path, "2026_05_14")
    assert table == {"AAPL": 95, "MSFT": 88}, (
        "expected the 1-day-old past file, not the future-dated file"
    )
