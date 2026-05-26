# HK Metrics Frame Cloud-Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the HK long-side metrics k-line fetch off-host (reuse the RS workflow's existing universe fetch) so candidate discovery covers the full ~2,400-ticker universe instead of the throttled ~1,600.

**Architecture:** The `update_hk_rs.yml` cloud workflow already fetches every HK k-line to compute RS. We add a second published artifact — `data/hk_metrics/<date>.csv` — derived from those same in-memory k-lines via the existing `build_metrics_frame`. The local 20:00 run pulls that CSV (new thin module `hk_metrics.py`), joins Futu market caps locally, and skips its own ~2,400-ticker download. On any cloud miss it falls straight through to the existing local fetch (no stale walk-back — `gap_pct`/`rvol` are point-in-time signals).

**Tech Stack:** Python 3.13, pandas, `urllib` (stdlib HTTP, mirrors `hk_rs.py`), pytest, GitHub Actions, uv.

**Spec:** `docs/superpowers/specs/2026-05-26-hk-metrics-cloud-fetch-design.md`

**Note on test scope:** the spec's Part 6 listed a `run_hk_eod` integration test. `run_hk_eod` has no existing test harness (it takes 9 injected callables and hits Futu/HKEX/yfinance; `test_hk_eod.py` only covers pure helpers). This plan substitutes a stronger **round-trip equivalence test** (Task 4) that proves the cloud-published frame, after local restore + cap-join, is byte-for-byte the same metrics `build_metrics_frame` would produce locally. The trivial cloud-or-fallback branch (Task 5) is verified by the manual Verification steps.

---

### Task 1: Scaffold the `data/hk_metrics/` published-artifact directory

**Files:**
- Create: `data/hk_metrics/.gitkeep`
- Create: `data/hk_metrics/README.md`

- [ ] **Step 1: Create the directory and keep-marker**

```bash
mkdir -p data/hk_metrics
touch data/hk_metrics/.gitkeep
```

- [ ] **Step 2: Write the README** (mirrors `data/hk_rs/README.md`'s purpose)

Create `data/hk_metrics/README.md`:

```markdown
# HK Long-side Metrics Frame (cloud-published)

Daily `<YYYY-MM-DD>.csv` written by `.github/workflows/update_hk_rs.yml`
(`scripts/compute_hk_rs_cloud.py`), reusing the same k-line batch fetched for
the HK RS tables. Indexed by Futu `code` (e.g. `HK.00700`).

Columns are the k-line-derived outputs of `hk_eod.build_metrics_frame`, minus:
- `market_cap` — needs Futu (not available in CI); filled locally from a Futu
  snapshot.
- `above_sma50` / `above_sma200` — recomputed locally from
  `last_price`/`sma50`/`sma200` (avoids bool↔CSV serialization fragility).

The local 20:00 HKT run fetches this via `hk_metrics.build_hk_metrics_cloud`
to skip its own throttled ~2,400-ticker yfinance download. Files older than
14 days are pruned each run.
```

- [ ] **Step 3: Commit**

```bash
git add data/hk_metrics/.gitkeep data/hk_metrics/README.md
git commit -m "chore(hk_metrics): scaffold cloud-published metrics dir"
```

---

### Task 2: New module `hk_metrics.py` — the local cloud-CSV fetcher

**Files:**
- Create: `hk_metrics.py`
- Test: `tests/test_hk_metrics_fetcher.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hk_metrics_fetcher.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_hk_metrics_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hk_metrics'`

- [ ] **Step 3: Write `hk_metrics.py`**

Create `hk_metrics.py`:

```python
"""Local fetcher for the cloud-published HK long-side metrics frame.

Mirrors hk_rs's HTTP/cache shape, with one deliberate difference: NO stale
walk-back. RS percentiles drift slowly (a 3-day-old table is "more honest
than no gate"); a metrics frame is the opposite — gap_pct/rvol/consecutive
up-days are point-in-time signals, so a stale frame is simply wrong. On a
cloud miss the caller (run_hk_eod) falls back to a local live yfinance fetch,
which gives correct today's prices at partial (throttled) coverage — strictly
better than complete-but-stale.

Compute lives in .github/workflows/update_hk_rs.yml (it already fetches every
HK k-line for RS); this module only does HTTP + disk I/O.
"""

from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

logger = logging.getLogger(__name__)

_CLOUD_CSV_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/EricXue92/finvinz_to_tv/main/"
    "data/hk_metrics/{date}.csv"
)


def _cache_path(today: date, output_dir: Path) -> Path:
    return output_dir / "state" / f"hk_metrics_{today.isoformat()}.csv"


def _fetch_cloud_csv(url: str, timeout: int = 30) -> pd.DataFrame | None:
    """Fetch the metrics CSV from the cloud-published artifact. Returns the
    DataFrame indexed by ``code`` on success, or None on 404 (not yet
    published), network error, or parse failure. Never raises."""
    try:
        req = Request(url, headers={"User-Agent": "finviz-to-tv/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return pd.read_csv(io.BytesIO(body), index_col="code")
    except HTTPError as e:
        if e.code == 404:
            return None
        logger.warning(f"[HK metrics] HTTP {e.code} fetching {url}")
        return None
    except (URLError, TimeoutError) as e:
        logger.warning(f"[HK metrics] Network error fetching {url}: {e}")
        return None
    except Exception as e:
        logger.warning(f"[HK metrics] Failed to parse {url}: {type(e).__name__}: {e}")
        return None


def _restore_above_sma(metrics: pd.DataFrame) -> pd.DataFrame:
    """Recompute the two bool columns build_metrics_frame normally emits
    (dropped before publish to dodge bool↔CSV fragility). Stored as Python
    bool (object dtype) to match build_metrics_frame's contract: callers and
    tests rely on ``row["above_sma50"] is False``."""
    last = metrics["last_price"]
    for col, sma_col in (("above_sma50", "sma50"), ("above_sma200", "sma200")):
        sma = metrics[sma_col]
        metrics[col] = [
            (bool(lp > sv) if pd.notna(sv) else False)
            for lp, sv in zip(last, sma)
        ]
        metrics[col] = metrics[col].astype(object)
    return metrics


def build_hk_metrics_cloud(output_dir: Path, today: date) -> pd.DataFrame | None:
    """Load today's cloud-published HK metrics frame (cap-less). Returns a
    DataFrame indexed by Futu code with the build_metrics_frame columns minus
    ``market_cap`` (caller joins Futu caps), or None when today's CSV is
    unavailable (caller falls back to a local live fetch).

    Resolution: same-day state cache → today's cloud CSV (mirror to cache) →
    None. No stale walk-back (see module docstring)."""
    cache = _cache_path(today, output_dir)
    if cache.exists():
        try:
            df = pd.read_csv(cache, index_col="code")
            logger.info(f"[HK metrics] Using cached frame: {len(df)} tickers")
            return _restore_above_sma(df)
        except Exception:
            pass  # unreadable cache → re-fetch

    url = _CLOUD_CSV_URL_TEMPLATE.format(date=today.isoformat())
    df = _fetch_cloud_csv(url)
    if df is None or df.empty:
        logger.warning(
            f"[HK metrics] Cloud CSV for {today.isoformat()} unavailable; "
            "caller will fall back to local yfinance fetch "
            "(check https://github.com/EricXue92/finvinz_to_tv/actions)"
        )
        return None

    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index_label="code")
    logger.info(f"[HK metrics] Fetched cloud CSV: {len(df)} tickers")
    return _restore_above_sma(df)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_hk_metrics_fetcher.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add hk_metrics.py tests/test_hk_metrics_fetcher.py
git commit -m "feat(hk_metrics): local fetcher for cloud-published metrics frame"
```

---

### Task 3: Cloud script publishes the metrics CSV

**Files:**
- Modify: `scripts/compute_hk_rs_cloud.py` (imports, `_METRICS_DIR`, `_prune_old_files` signature, metrics publish in `main()`)
- Test: `tests/test_compute_hk_rs_cloud.py` (extend)

- [ ] **Step 1: Write the failing test (extend the cloud test)**

Add to `tests/test_compute_hk_rs_cloud.py`. First, every existing test that calls `cloud.main()` and reaches the publish step must also point `_METRICS_DIR` at tmp_path. Add this line right after each existing `monkeypatch.setattr(cloud, "_DATA_DIR", ...)` line in **all four** existing tests:

```python
    monkeypatch.setattr(cloud, "_METRICS_DIR", tmp_path / "data" / "hk_metrics")
```

Then add a new test at the end of the file:

```python
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
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `uv run pytest tests/test_compute_hk_rs_cloud.py::test_main_also_writes_metrics_csv_without_cap_or_bool_columns -v`
Expected: FAIL — `AttributeError: ... has no attribute '_METRICS_DIR'` (or no metrics CSV written)

- [ ] **Step 3: Modify `scripts/compute_hk_rs_cloud.py`**

Add `build_metrics_frame` to the `hk_eod` import (around line 34):

```python
from hk_eod import (  # noqa: E402
    build_metrics_frame,
    fetch_hk_klines_yf,
    fetch_hkex_equities,
    fetch_hsi_kline_yf,
)
```

Add the metrics dir constant next to `_DATA_DIR` (around line 48):

```python
_DATA_DIR = _REPO_ROOT / "data" / "hk_rs"
_METRICS_DIR = _REPO_ROOT / "data" / "hk_metrics"
```

Change `_prune_old_files` to take the directory as a parameter (it currently hardcodes `_DATA_DIR`). Replace its signature and the `_DATA_DIR` reference inside:

```python
def _prune_old_files(data_dir: Path, today: date) -> int:
    """Delete <YYYY-MM-DD>.csv files in data_dir older than _RETENTION_DAYS."""
    if not data_dir.exists():
        return 0
    cutoff = today - timedelta(days=_RETENTION_DAYS)
    pruned = 0
    for p in data_dir.glob("*.csv"):
        try:
            file_date = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            p.unlink()
            pruned += 1
    return pruned
```

In `main()`, between the RS write (step 7, ends ~line 134) and the prune (step 8), insert the metrics publish:

```python
    # 7b. Publish the k-line-derived metrics frame off the SAME klines.
    #     market_cap needs Futu (absent in CI) → dropped, filled locally.
    #     above_sma50/200 dropped (bool↔CSV fragility) → recomputed locally.
    metrics = build_metrics_frame(klines, market_caps={}).drop(
        columns=["market_cap", "above_sma50", "above_sma200"]
    )
    _METRICS_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path = _METRICS_DIR / f"{today.isoformat()}.csv"
    metrics.to_csv(metrics_path, index_label="code")
    logger.info(f"[Cloud HK RS] Wrote {len(metrics)} metrics rows → {metrics_path}")
```

And update step 8 to prune both directories:

```python
    # 8. Prune old files in both published dirs.
    pruned = _prune_old_files(_DATA_DIR, today) + _prune_old_files(_METRICS_DIR, today)
    logger.info(f"[Cloud HK RS] Pruned {pruned} files older than {_RETENTION_DAYS} days")
```

- [ ] **Step 4: Run the full cloud test file to verify all pass**

Run: `uv run pytest tests/test_compute_hk_rs_cloud.py -v`
Expected: PASS (5 tests — the 4 existing, now with `_METRICS_DIR` monkeypatched, plus the new one)

- [ ] **Step 5: Commit**

```bash
git add scripts/compute_hk_rs_cloud.py tests/test_compute_hk_rs_cloud.py
git commit -m "feat(compute_hk_rs_cloud): publish metrics frame alongside RS tables"
```

---

### Task 4: Round-trip equivalence test (cloud-publish ↔ local-restore)

**Files:**
- Test: `tests/test_hk_metrics_fetcher.py` (append)

This is the spec's key correctness guarantee: the cloud-published frame, after local restore + cap-join, equals the metrics `build_metrics_frame` produces locally with real caps. No production code changes — it validates the Task 2 + Task 3 contract end to end.

- [ ] **Step 1: Write the round-trip test**

Append to `tests/test_hk_metrics_fetcher.py`:

```python
from hk_eod import build_metrics_frame  # noqa: E402  (top-of-file import is fine too)


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
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/test_hk_metrics_fetcher.py::test_cloud_publish_roundtrip_equals_local_metrics -v`
Expected: PASS

(If it fails on `above_sma*`, that signals a real restore mismatch — fix `_restore_above_sma`, not the test.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_hk_metrics_fetcher.py
git commit -m "test(hk_metrics): cloud-publish round-trips to local metrics contract"
```

---

### Task 5: Wire the cloud-first branch into `run_hk_eod`

**Files:**
- Modify: `hk_eod.py` (the HK Long-side block, ~lines 864–934)

No new automated test (see plan header note). Verified manually in the Verification section.

- [ ] **Step 1: Add the import**

At the top of `hk_eod.py`, near the other local-module imports, add:

```python
from hk_metrics import build_hk_metrics_cloud
```

- [ ] **Step 2: Replace the unconditional fetch with a cloud-first branch**

The current block (`hk_eod.py:869–934`) runs: log universe → `fetch_hk_klines_yf` → depth log → pre-20:00 trim → Futu caps → `build_metrics_frame`. Wrap it so the cloud path skips the local k-line fetch. Replace lines 869–934 with:

```python
    # Cloud-first: the RS workflow already fetched every HK k-line; it also
    # publishes a metrics frame (data/hk_metrics/<date>.csv). Pull that to
    # skip the local ~2,400-ticker yfinance download (which throttles to
    # ~66% coverage). On any miss, fall back to the local live fetch — a
    # stale frame would carry wrong gap_pct/rvol, so there is no walk-back.
    today_d = date.today()
    metrics = build_hk_metrics_cloud(output_dir, today_d)

    if metrics is not None:
        logger.info(
            f"[HK Longs] Using cloud metrics: {len(metrics)} tickers "
            "(local k-line fetch skipped)"
        )
        logger.info("[HK Longs] Fetching market caps via Futu snapshot...")
        tv_codes = [_to_tv(c) for c in metrics.index]
        futu_caps_by_tv = (
            get_market_caps_futu(tv_codes, market="HK", host=host, port=port) or {}
        )
        caps = {
            f"HK.{tv.replace('HKEX:', '').zfill(5)}": v
            for tv, v in futu_caps_by_tv.items()
        }
        metrics["market_cap"] = metrics.index.map(lambda c: caps.get(c, float("nan")))
    else:
        logger.warning(
            "[HK Longs] Cloud metrics unavailable; falling back to local "
            "yfinance fetch (throttle-prone, partial coverage)"
        )
        logger.info("[HK Longs] Fetching universe...")
        codes_4d = fetch_hkex_equities()
        logger.info(f"  Universe: {len(codes_4d)} codes")

        logger.info("[HK Longs] Fetching daily OHLCV via yfinance (~5-10 min)...")
        klines = fetch_hk_klines_yf(codes_4d, period="2y")

        if klines:
            lens = sorted(len(df) for df in klines.values())
            n = len(lens)
            ge253 = sum(1 for l in lens if l >= 253)
            ge200 = sum(1 for l in lens if l >= 200)
            ge100 = sum(1 for l in lens if l >= 100)
            ge20 = sum(1 for l in lens if l >= 20)
            median = lens[n // 2] if n else 0
            logger.info(
                f"[HK Longs] k-line depth: n={n}, median={median} rows; "
                f">=253: {ge253} ({100*ge253//max(n,1)}%), >=200: {ge200}, "
                f">=100: {ge100}, >=20: {ge20}"
            )

        # Trim incomplete today's bar if running before 20:00 HKT (only the
        # local-fetch path needs this — cloud metrics are always settled-close).
        hkt_now = datetime.now(ZoneInfo("Asia/Hong_Kong"))
        use_yesterday = hkt_now.hour < 20
        if use_yesterday and klines:
            today_d_local = hkt_now.date()
            trimmed = 0
            for code, df in list(klines.items()):
                mask = df["time_key"].dt.date < today_d_local
                if (~mask).any():
                    trimmed += 1
                klines[code] = df[mask].reset_index(drop=True)
            logger.info(
                f"[HK Longs] Pre-20:00 HKT run (now {hkt_now.strftime('%H:%M')}); "
                f"trimmed today's incomplete bar from {trimmed} tickers — "
                f"using previous-day close as 'latest'."
            )

        logger.info("[HK Longs] Fetching market caps via Futu snapshot...")
        tv_codes = [_to_tv(c) for c in klines.keys()]
        futu_caps_by_tv = (
            get_market_caps_futu(tv_codes, market="HK", host=host, port=port) or {}
        )
        caps = {
            f"HK.{tv.replace('HKEX:', '').zfill(5)}": v
            for tv, v in futu_caps_by_tv.items()
        }

        logger.info("[HK Longs] Building metrics frame...")
        metrics = build_metrics_frame(klines, caps)

    logger.info(f"  Metrics: {len(metrics)} tickers with usable history")
```

- [ ] **Step 3: Remove the now-duplicate `today_d` assignment**

The RS-table line below this block currently reads `today_d = date.today()` then `build_hk_rs_tables(output_dir, today_d)` (~lines 945–946). `today_d` is now defined above; delete the redundant re-assignment so only the `build_hk_rs_tables(output_dir, today_d)` call remains.

- [ ] **Step 4: Run the full suite to confirm no regression**

Run: `uv run pytest tests/ -v`
Expected: PASS (all existing tests + the new fetcher/cloud/round-trip tests). `build_metrics_frame` / `apply_strategy_filters` / IPO tests are unaffected.

- [ ] **Step 5: Commit**

```bash
git add hk_eod.py
git commit -m "feat(hk_eod): cloud-first metrics frame with local-fetch fallback"
```

---

### Task 6: Cleanup rule for the metrics state cache

**Files:**
- Modify: `cleanup.py` (add a `_Rule` for `hk_metrics_<date>.csv`)
- Test: `tests/test_cleanup.py` (add a case)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cleanup.py` (follow the existing test style in that file — it constructs `output/state/` fixtures and calls `cleanup_old_outputs`):

```python
def test_cleanup_removes_old_hk_metrics_state_cache(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "hk_metrics_2026-05-26.csv").write_text("code,last_price\n")   # today
    (state / "hk_metrics_2026-05-25.csv").write_text("code,last_price\n")   # yesterday
    (state / "hk_metrics_2026-05-20.csv").write_text("code,last_price\n")   # 6 days old

    from cleanup import cleanup_old_outputs
    cleanup_old_outputs(tmp_path, date(2026, 5, 26))

    assert (state / "hk_metrics_2026-05-26.csv").exists()      # today kept
    assert (state / "hk_metrics_2026-05-25.csv").exists()      # yesterday kept (2-day window)
    assert not (state / "hk_metrics_2026-05-20.csv").exists()  # 6-day pruned
```

(Match the actual import/date-construction idiom already used at the top of `tests/test_cleanup.py`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cleanup.py::test_cleanup_removes_old_hk_metrics_state_cache -v`
Expected: FAIL — the 6-day-old file still exists (no rule matches `hk_metrics_*`).

- [ ] **Step 3: Add the rule**

In `cleanup.py`, in the `_RETENTION_RULES` tuple, add next to the `hk_rs_rating` rules:

```python
    # hk_metrics_*.csv: HK long-side metrics state cache (hk_metrics.py, ISO
    # dashes). 2-day window — only today's cache is ever read (no walk-back).
    _Rule("state", re.compile(rf"^hk_metrics_{_DATE_D}\.csv$"),
          "%Y-%m-%d", 2),
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_cleanup.py -v`
Expected: PASS (existing cleanup tests + the new one)

- [ ] **Step 5: Commit**

```bash
git add cleanup.py tests/test_cleanup.py
git commit -m "feat(cleanup): retain hk_metrics state cache on the 2-day window"
```

---

### Task 7: Workflow commit step + CLAUDE.md refresh

**Files:**
- Modify: `.github/workflows/update_hk_rs.yml` (git-add the new dir)
- Modify: `CLAUDE.md` (refresh the "metrics frame still locally throttled" note)

- [ ] **Step 1: Add `data/hk_metrics/` to the workflow's git-add**

In `.github/workflows/update_hk_rs.yml`, the "Commit and push" step currently runs `git add data/hk_rs/`. Change it to stage both dirs:

```yaml
          git add data/hk_rs/ data/hk_metrics/
```

(The `git diff --staged --quiet` guard and the rebase-retry push below it are unchanged — they already handle "a new file was added".)

- [ ] **Step 2: Refresh the CLAUDE.md note**

In `CLAUDE.md`, the RS-gating section's blockquote currently ends:

> ... The local HK 20:00 run still fetches ~2,400 k-lines for the *metrics* frame, so discovery is still locally throttled.

Replace that final sentence with:

> The HK metrics frame is now also cloud-published (`data/hk_metrics/`,
> same workflow) and fetched locally via `hk_metrics.build_hk_metrics_cloud`,
> so discovery runs on the full universe on the happy path; a cloud miss
> falls back to the local (throttle-prone) k-line fetch.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/update_hk_rs.yml CLAUDE.md
git commit -m "ci(hk_rs): publish data/hk_metrics; refresh CLAUDE.md note"
```

---

## Verification (after all tasks)

1. **Full suite:** `uv run pytest tests/ -v` — all pass.
2. **Cloud script locally:** run `uv run python scripts/compute_hk_rs_cloud.py`
   (or trigger the workflow via `workflow_dispatch`) and confirm both
   `data/hk_rs/<date>.csv` and `data/hk_metrics/<date>.csv` are written with
   matching row counts; the metrics CSV has `last_price`/`gap_pct`/… and **no**
   `market_cap`/`above_sma50`/`above_sma200` columns.
3. **Local happy path:** with today's `data/hk_metrics/<date>.csv` present
   (pull latest `main`), run `uv run main.py --mode hk-eod` and confirm the log
   shows `[HK Longs] Using cloud metrics: N tickers (local k-line fetch
   skipped)` with N ≈ full universe (~2,400) and the baseline funnel `n=` is no
   longer ~1,600.
4. **Fallback path:** temporarily point `hk_metrics._CLOUD_CSV_URL_TEMPLATE` at
   an invalid path (or run before today's cloud CSV exists) → confirm the log
   shows `Cloud metrics unavailable; falling back to local yfinance fetch` and
   the run still completes and writes the dated `.txt` files.
5. **No behavior regression:** HK output `.txt` files remain first-sighting-only
   (cross-day dedup untouched); the only observable change is a larger discovery
   universe (the funnel's `n=` count).
```
