"""Tests for hk_metrics._fetch_cloud_csv and build_hk_metrics_cloud.

The metrics fetcher mirrors hk_rs's HTTP shape but deliberately has NO
stale walk-back: a 3-day-old gap_pct/rvol is wrong, so on a cloud miss the
caller falls back to a local live fetch instead. Only HTTP + disk I/O here.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

import pandas as pd
import pytest

import hk_metrics


# code, then the published (cap-less, bool-less) metrics columns. Three rows:
#  HK.00001 — last 110 > sma50 100 and > sma200 90  → above both True
#  HK.00700 — last 50  < sma50 100                  → above50 False
#  HK.09999 — sma50/sma200 blank (short history)     → above both False
_FIXTURE_CSV = (
    "code,last_price,prev_close,gap_pct,rvol,avg_vol_20d,avg_dollar_vol_20d,"
    "adr_pct,sma50,sma200,perf_4w,perf_13w,perf_26w,perf_ytd,perf_52w,"
    "consecutive_up_days\n"
    "HK.00001,110,108,1.85,3.2,800000,88000000,4.1,100,90,12,30,55,40,150,2\n"
    "HK.00700,50,49,2.04,1.1,600000,30000000,3.0,100,80,5,10,20,15,60,0\n"
    "HK.09999,25,24,4.16,3.5,500000,12500000,3.8,,,8,,,,,1\n"
)


class _FakeResponse:
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
    monkeypatch.setattr(hk_metrics, "urlopen", _fake_urlopen_returning(_FIXTURE_CSV))
    table = hk_metrics._fetch_cloud_csv("https://example.invalid/today.csv")
    assert table is not None
    assert list(table.index) == ["HK.00001", "HK.00700", "HK.09999"]
    assert table.loc["HK.00001", "last_price"] == 110


def test_fetch_cloud_csv_404_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hk_metrics, "urlopen",
        _fake_urlopen_raising(HTTPError("https://example.invalid/x.csv", 404, "Not Found", {}, None)),
    )
    assert hk_metrics._fetch_cloud_csv("https://example.invalid/x.csv") is None


def test_fetch_cloud_csv_network_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hk_metrics, "urlopen", _fake_urlopen_raising(URLError("dns failure")))
    assert hk_metrics._fetch_cloud_csv("https://example.invalid/today.csv") is None


# ── build_hk_metrics_cloud ───────────────────────────────────────────────

def test_build_uses_today_csv_and_mirrors_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    fixture = pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="code")

    def _fake_fetch(url, timeout=30):
        assert "2026-05-22.csv" in url, url
        return fixture

    monkeypatch.setattr(hk_metrics, "_fetch_cloud_csv", _fake_fetch)
    frame = hk_metrics.build_hk_metrics_cloud(tmp_path, today)
    assert frame is not None
    assert list(frame.index) == ["HK.00001", "HK.00700", "HK.09999"]
    # above_sma* recomputed from last_price vs sma50/sma200:
    assert frame.loc["HK.00001", "above_sma50"] is True
    assert frame.loc["HK.00001", "above_sma200"] is True
    assert frame.loc["HK.00700", "above_sma50"] is False   # 50 < 100
    assert frame.loc["HK.09999", "above_sma50"] is False    # NaN sma → False
    assert frame.loc["HK.09999", "above_sma200"] is False
    # Today's pull mirrored to the state cache for same-day rerun short-circuit.
    assert (tmp_path / "state" / "hk_metrics_2026-05-22.csv").exists()


def test_build_no_stale_walkback_returns_none_when_today_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unlike hk_rs: today's CSV missing → None immediately, NO older-date fetch."""
    today = date(2026, 5, 22)
    calls: list[str] = []

    def _fake_fetch(url, timeout=30):
        calls.append(url)
        return None  # today 404

    monkeypatch.setattr(hk_metrics, "_fetch_cloud_csv", _fake_fetch)
    frame = hk_metrics.build_hk_metrics_cloud(tmp_path, today)
    assert frame is None
    assert len(calls) == 1, "must NOT walk back to older dates"
    assert "2026-05-22" in calls[0]
    assert not (tmp_path / "state" / "hk_metrics_2026-05-22.csv").exists()


def test_build_cache_short_circuit_skips_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    (tmp_path / "state").mkdir(parents=True)
    pd.read_csv(io.StringIO(_FIXTURE_CSV), index_col="code").to_csv(
        tmp_path / "state" / "hk_metrics_2026-05-22.csv", index_label="code"
    )

    def _fail(*a, **kw):
        pytest.fail("_fetch_cloud_csv must not be called on a same-day cache hit")

    monkeypatch.setattr(hk_metrics, "_fetch_cloud_csv", _fail)
    frame = hk_metrics.build_hk_metrics_cloud(tmp_path, today)
    assert frame is not None
    # above_sma* still recomputed on the cache path.
    assert frame.loc["HK.00001", "above_sma50"] is True
    assert frame.loc["HK.00700", "above_sma50"] is False


# ── round-trip: cloud-publish → CSV → local-restore → cap-join ───────────

from hk_eod import build_metrics_frame  # noqa: E402


def _kline(start: float, n: int) -> pd.DataFrame:
    """Ascending-time OHLCV with a final up-day, deep enough for SMA200."""
    closes = [start + i * 0.1 for i in range(n)]
    highs = [c * 1.02 for c in closes]
    lows = [c * 0.98 for c in closes]
    vols = [700000 + i for i in range(n)]
    return pd.DataFrame({
        "time_key": pd.date_range(end="2026-05-22", periods=n, freq="B"),
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": vols,
    })


def test_cloud_publish_roundtrip_equals_local_metrics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    today = date(2026, 5, 22)
    klines = {"HK.00001": _kline(100.0, 260), "HK.00700": _kline(50.0, 80)}
    caps = {"HK.00001": 5_000_000_000.0, "HK.00700": 8_000_000_000.0}

    # Local reference: what run_hk_eod's fallback path would compute.
    local = build_metrics_frame(klines, caps)

    # Simulate the cloud publish: empty caps, drop the 3 local-only columns,
    # write the CSV exactly as compute_hk_rs_cloud.main() does.
    published = build_metrics_frame(klines, market_caps={}).drop(
        columns=["market_cap", "above_sma50", "above_sma200"]
    )
    csv_path = tmp_path / "data" / "hk_metrics" / "2026-05-22.csv"
    csv_path.parent.mkdir(parents=True)
    published.to_csv(csv_path, index_label="code")

    # Local fetch reads that CSV; then run_hk_eod joins Futu caps.
    on_disk = pd.read_csv(csv_path, index_col="code")
    monkeypatch.setattr(hk_metrics, "_fetch_cloud_csv", lambda url, timeout=30: on_disk)
    fetched = hk_metrics.build_hk_metrics_cloud(tmp_path, today)
    assert fetched is not None
    fetched["market_cap"] = fetched.index.map(lambda c: caps.get(c, float("nan")))

    # Every column the strategy filters read must match the local reference.
    for col in [
        "last_price", "prev_close", "gap_pct", "rvol", "avg_vol_20d",
        "avg_dollar_vol_20d", "adr_pct", "sma50", "sma200",
        "above_sma50", "above_sma200", "perf_4w", "perf_13w", "perf_26w",
        "perf_ytd", "perf_52w", "consecutive_up_days", "market_cap",
    ]:
        pd.testing.assert_series_equal(
            fetched[col].reindex(local.index), local[col],
            check_names=False, check_dtype=False,
        )
