"""Tests for hk_rs._fetch_cloud_csv and build_hk_rs_tables.

Cover the HTTP-fetcher path, the combined-CSV split (12M/3M, with NaN-drop
per column), the 4-day stale-fallback walk (today → 1d → 2d → 3d), the
local-cache short-circuit on same-day reruns, and the (None, None)
passthrough when all fetches fail.

build_hk_rs_tables has no yfinance/HSI dependency after the cloud move —
only HTTP and disk I/O remain.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

import hk_rs


# Combined cloud CSV: HK.09999 is 3M-only (blank 12M → 3M frame only).
_FIXTURE_CSV = (
    "code,rs_percentile_12m,rs_percentile_3m\n"
    "HK.00001,95,90\n"
    "HK.00700,80,85\n"
    "HK.09999,,70\n"
)


class _FakeResponse:
    """Minimal stand-in for urlopen's return value: context manager + read()."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _fake_urlopen_returning(body: str):
    def _fn(req, timeout=30):
        return _FakeResponse(body.encode("utf-8"))
    return _fn


def _fake_urlopen_raising(exc: Exception):
    def _fn(req, timeout=30):
        raise exc
    return _fn


# ── _fetch_cloud_csv ─────────────────────────────────────────────────────

def test_fetch_cloud_csv_success_returns_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hk_rs, "urlopen", _fake_urlopen_returning(_FIXTURE_CSV))
    table = hk_rs._fetch_cloud_csv("https://example.invalid/today.csv")
    assert table is not None
    assert list(table.index) == ["HK.00001", "HK.00700", "HK.09999"]
    assert table.loc["HK.00001", "rs_percentile_12m"] == 95


def test_fetch_cloud_csv_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hk_rs, "urlopen",
        _fake_urlopen_raising(HTTPError("https://example.invalid/missing.csv", 404, "Not Found", {}, None)),
    )
    assert hk_rs._fetch_cloud_csv("https://example.invalid/missing.csv") is None


def test_fetch_cloud_csv_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hk_rs, "urlopen", _fake_urlopen_raising(URLError("dns failure")))
    assert hk_rs._fetch_cloud_csv("https://example.invalid/today.csv") is None


def test_fetch_cloud_csv_malformed_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed body should not raise — caller falls back day-by-day."""
    monkeypatch.setattr(hk_rs, "urlopen", _fake_urlopen_returning("not,a,valid\ncsv\n\"unterminated"))
    assert hk_rs._fetch_cloud_csv("https://example.invalid/bad.csv") is None


# ── _split_combined ──────────────────────────────────────────────────────

def test_split_combined_drops_nan_per_column() -> None:
    combined = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="code")
    table_12m, table_3m = hk_rs._split_combined(combined)
    # HK.09999 has blank 12M → present in 3M only.
    assert list(table_12m.index) == ["HK.00001", "HK.00700"]
    assert list(table_3m.index) == ["HK.00001", "HK.00700", "HK.09999"]
    # Single-column "rs_percentile" int frames (the gate's expected shape).
    assert list(table_12m.columns) == ["rs_percentile"]
    assert int(table_12m.loc["HK.00001", "rs_percentile"]) == 95
    assert int(table_3m.loc["HK.09999", "rs_percentile"]) == 70


# ── build_hk_rs_tables ───────────────────────────────────────────────────

def test_build_uses_today_cloud_csv_and_mirrors_to_local_caches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    fixture = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="code")

    def _fake_fetch(url, timeout=30):
        assert "2026-05-22.csv" in url, url
        return fixture

    monkeypatch.setattr(hk_rs, "_fetch_cloud_csv", _fake_fetch)
    table_12m, table_3m, _rs_line = hk_rs.build_hk_rs_tables(tmp_path, today)
    assert table_12m is not None and table_3m is not None
    assert list(table_12m.index) == ["HK.00001", "HK.00700"]
    assert list(table_3m.index) == ["HK.00001", "HK.00700", "HK.09999"]
    # Both local caches mirrored for same-day rerun short-circuit.
    assert (tmp_path / "state" / "hk_rs_rating_2026-05-22.csv").exists()
    assert (tmp_path / "state" / "hk_rs_rating_3m_2026-05-22.csv").exists()


def test_build_walks_back_to_stale_fallback_and_does_not_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    fixture = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="code")
    calls: list[str] = []

    def _fake_fetch(url, timeout=30):
        calls.append(url)
        if "2026-05-19" in url:  # 3d-old succeeds; today/-1/-2 all 404
            return fixture
        return None

    monkeypatch.setattr(hk_rs, "_fetch_cloud_csv", _fake_fetch)
    table_12m, table_3m, _rs_line = hk_rs.build_hk_rs_tables(tmp_path, today)
    assert table_12m is not None and table_3m is not None
    assert len(calls) == 4  # today, -1, -2, -3
    assert "2026-05-22" in calls[0]
    assert "2026-05-19" in calls[3]
    # Stale fallbacks must NOT write the local caches.
    assert not (tmp_path / "state" / "hk_rs_rating_2026-05-22.csv").exists()
    assert not (tmp_path / "state" / "hk_rs_rating_3m_2026-05-22.csv").exists()


def test_build_returns_none_pair_when_all_fetches_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    monkeypatch.setattr(hk_rs, "_fetch_cloud_csv", lambda *a, **kw: None)
    table_12m, table_3m, _rs_line = hk_rs.build_hk_rs_tables(tmp_path, today)
    assert table_12m is None and table_3m is None


def test_build_local_cache_short_circuit_skips_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If BOTH local caches exist for today, no HTTP at all."""
    today = date(2026, 5, 22)
    cache_12m = pd.DataFrame({"rs_percentile": [95, 80]}, index=["HK.00001", "HK.00700"])
    cache_3m = pd.DataFrame({"rs_percentile": [90, 85, 70]}, index=["HK.00001", "HK.00700", "HK.09999"])
    cache_12m.index.name = "code"
    cache_3m.index.name = "code"
    hk_rs.save_cache(cache_12m, today, tmp_path)
    hk_rs.save_cache(cache_3m, today, tmp_path, suffix="3m")

    def _fail_on_fetch(*a, **kw):
        pytest.fail("_fetch_cloud_csv should not be called when both caches hit")

    monkeypatch.setattr(hk_rs, "_fetch_cloud_csv", _fail_on_fetch)
    table_12m, table_3m, _rs_line = hk_rs.build_hk_rs_tables(tmp_path, today)
    assert table_12m is not None and table_3m is not None
    assert list(table_12m.index) == ["HK.00001", "HK.00700"]


def test_build_partial_cache_does_not_short_circuit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the 12M cache present → still fetch (both must be cached to skip)."""
    today = date(2026, 5, 22)
    cache_12m = pd.DataFrame({"rs_percentile": [95]}, index=["HK.00001"])
    cache_12m.index.name = "code"
    hk_rs.save_cache(cache_12m, today, tmp_path)
    fixture = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="code")
    monkeypatch.setattr(hk_rs, "_fetch_cloud_csv", lambda *a, **kw: fixture)
    table_12m, table_3m, _rs_line = hk_rs.build_hk_rs_tables(tmp_path, today)
    # Fetched fixture wins (3 rows in 3M), not the 1-row partial cache.
    assert list(table_3m.index) == ["HK.00001", "HK.00700", "HK.09999"]


def test_build_returns_rs_line_frame(tmp_path, monkeypatch):
    import pandas as pd
    import hk_rs

    combined = pd.DataFrame({
        "code": ["HK.00001", "HK.00002"],
        "rs_percentile_12m": [95, 80],
        "rs_percentile_3m": [92, 70],
        "rs_below_ma": [0, 1],
        "rs_days_below_ma": [0, 14],
        "rs_frac_below_ma": [0.1, 0.9],
    }).set_index("code")
    monkeypatch.setattr(hk_rs, "_fetch_cloud_csv", lambda url, **k: combined)

    t12, t3, tline = hk_rs.build_hk_rs_tables(tmp_path, __import__("datetime").date(2026, 5, 27))
    assert tline is not None
    assert int(tline.loc["HK.00002", "rs_below_ma"]) == 1
    assert "rs_frac_below_ma" in tline.columns
