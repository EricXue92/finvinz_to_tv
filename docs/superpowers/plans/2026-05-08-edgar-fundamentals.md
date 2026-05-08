# SEC EDGAR US Fundamentals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace yfinance as the US daily report's fundamentals source with SEC EDGAR's XBRL `companyfacts` API, expanding history from 3y/2q to 5y/4q. Hybrid split: EDGAR owns financial-statement history, yfinance keeps live snapshot fields. Triple fallback (EDGAR → yfinance → None) and 7-day local TTL cache.

**Architecture:** New `report/edgar.py` module with one public function `fetch_edgar_fundamentals(ticker) -> dict | None`. `report/enrich.py` calls EDGAR first for the 8 fundamental fields, falls back per-field to the existing yfinance income-statement code path. Field names rename `_3y → _5y` and `_2q → _4q`; renderer SVG bar count widens. HK report unchanged in source (yfinance) but adopts the same renamed schema (leading slots stay None where yfinance can't fill).

**Tech Stack:** Python 3.12, `httpx` (sync), `pytest`, existing `yfinance` (kept as fallback). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-08-edgar-fundamentals-design.md`

---

## File Structure

**New files:**

- `report/edgar.py` — EDGAR client. ~250 lines. Public surface = one function `fetch_edgar_fundamentals(ticker)`; everything else is private. Owns: HTTP, cache, CIK lookup, XBRL concept matching, period filtering, Q4 derivation, YoY computation.
- `tests/test_report_edgar.py` — pure-logic unit tests, no network. Fixtures live alongside.
- `tests/fixtures/edgar/companyfacts_aapl_minimal.json` — trimmed real `companyfacts` shape (multi-year `Revenues` + `EarningsPerShareDiluted`).
- `tests/fixtures/edgar/companyfacts_v_alt_revenue.json` — uses `RevenueFromContractWithCustomerExcludingAssessedTax` instead of `Revenues`.
- `tests/fixtures/edgar/companyfacts_q2_gap.json` — full annual coverage but missing one Q2 → Q4 derivation should yield None for that year.
- `tests/fixtures/edgar/company_tickers.json` — three-row trimmed copy of the SEC ticker map.

**Modified files:**

- `report/enrich.py` — add EDGAR-first orchestration in `fetch_ticker_data`; rename field keys in `data` template; widen `extract_annual_yoy` default to 5 years; widen quarterly extraction to 4. Existing yfinance fetch code stays as fallback.
- `report/renderer.py` — rename field accesses, update SVG titles ("3-Year" → "5-Year", "Past 2 Quarters" → "Past 4 Quarters"), grow bar arrays, update Markdown table columns FY−5..FY−1.
- `report/__main__.py` — `_EMPTY_DATA_TEMPLATE` field renames.
- `prompts/canslim_system.md` — replace "3-year annual YoY chart" / "3-year YoY arrays" wording with "5-year annual YoY".
- `tests/test_report_enrich.py` — rename `_3y` / `_2q` assertions; add tests for EDGAR-first then yfinance-fallback orchestration.
- `tests/test_report_analyst.py` — rename fixture keys.
- `tests/test_report_renderer.py` — rename fixture keys, expand to 5 / 4 element arrays.
- `tests/test_report_main.py` — update `_EMPTY_DATA_TEMPLATE` expectations.
- `CLAUDE.md` — update "Daily CANSLIM Report" entry to mention EDGAR + new history depth.

**Files explicitly NOT touched:** `report/analyst.py`, `report/llm.py`, `report/ranker.py`, `report/state.py`, `report/search.py`, `hk_eod.py`, `hk_rs.py`, `main.py`, `config.toml`, `pyproject.toml`, `notify.py`, `futu_sync.py`, `rs_rating.py`.

---

## Task 1: Scaffold `report/edgar.py` with cache helpers (TDD)

**Files:**
- Create: `report/edgar.py`
- Create: `tests/test_report_edgar.py`

- [ ] **Step 1: Write failing tests for `_is_fresh` (TTL helper)**

Create `tests/test_report_edgar.py`:

```python
import json
import os
import time
from pathlib import Path

import pytest

from report import edgar


def test_is_fresh_returns_false_when_file_missing(tmp_path: Path):
    assert edgar._is_fresh(tmp_path / "missing.json", ttl_seconds=10) is False


def test_is_fresh_returns_true_for_fresh_file(tmp_path: Path):
    p = tmp_path / "fresh.json"
    p.write_text("{}")
    assert edgar._is_fresh(p, ttl_seconds=86400) is True


def test_is_fresh_returns_false_for_stale_file(tmp_path: Path):
    p = tmp_path / "stale.json"
    p.write_text("{}")
    old = time.time() - 100
    os.utime(p, (old, old))
    assert edgar._is_fresh(p, ttl_seconds=10) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report_edgar.py -v
```

Expected: ImportError or AttributeError (module / `_is_fresh` not yet defined).

- [ ] **Step 3: Create `report/edgar.py` with cache helpers**

```python
"""SEC EDGAR XBRL companyfacts fetcher for US-report fundamentals.

Public API: fetch_edgar_fundamentals(ticker). Returns the 8 EDGAR-owned
fields when available, None on any failure. Caller (report/enrich.py)
falls back to yfinance on None or per-field for partial dicts.

Cache layout under output/state/edgar_cache/:
  - company_tickers.json (TTL: 1 day)
  - CIK{0:010d}.json     (TTL: 7 days)

SEC requires a User-Agent containing an email; we hard-code the
project owner's address per CLAUDE.md / memory.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Constants ---------------------------------------------------------------

CACHE_DIR = Path("output/state/edgar_cache")
COMPANY_TICKERS_TTL = 86_400              # 1 day
COMPANYFACTS_TTL = 7 * 86_400             # 7 days
USER_AGENT = "finviz-to-tv xuelong0208@gmail.com"
HTTP_TIMEOUT = 10.0
HTTP_RETRY_DELAY = 0.5

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


# --- Cache TTL --------------------------------------------------------------

def _is_fresh(path: Path, ttl_seconds: int) -> bool:
    """True if `path` exists and its mtime is within `ttl_seconds` of now."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return False
    return (time.time() - mtime) < ttl_seconds
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report_edgar.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py
git commit -m "feat(edgar): scaffold module with TTL cache helper"
```

---

## Task 2: HTTP fetch with retry + cache write/read

**Files:**
- Modify: `report/edgar.py`
- Modify: `tests/test_report_edgar.py`

- [ ] **Step 1: Write failing tests for `_load_json_cache` and `_save_json_cache`**

Append to `tests/test_report_edgar.py`:

```python
def test_save_and_load_json_cache_roundtrip(tmp_path: Path):
    p = tmp_path / "data.json"
    edgar._save_json_cache(p, {"hello": 1})
    assert edgar._load_json_cache(p) == {"hello": 1}


def test_load_json_cache_returns_none_for_corrupt_file_and_deletes_it(tmp_path: Path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not json")
    assert edgar._load_json_cache(p) is None
    assert not p.exists()


def test_load_json_cache_returns_none_when_missing(tmp_path: Path):
    assert edgar._load_json_cache(tmp_path / "missing.json") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_report_edgar.py -v
```

Expected: 3 new failures with AttributeError.

- [ ] **Step 3: Add cache I/O helpers to `report/edgar.py`**

Append to `report/edgar.py` after `_is_fresh`:

```python
def _save_json_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _load_json_cache(path: Path) -> dict | None:
    """Read and parse JSON from `path`. Returns None on missing file or
    corrupt JSON; on corruption the file is deleted so the next refresh
    overwrites cleanly."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"[edgar] corrupt cache at {path} ({e}); deleting")
        try:
            path.unlink()
        except OSError:
            pass
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report_edgar.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Write failing tests for `_http_get_json` retry behavior**

Append:

```python
def test_http_get_json_returns_payload_on_200(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        def json(self):
            return {"ok": True}
        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(edgar.httpx, "get", fake_get)
    assert edgar._http_get_json("https://x") == {"ok": True}
    assert calls["n"] == 1


def test_http_get_json_retries_once_on_5xx(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, code):
            self.status_code = code
        def json(self):
            return {"ok": True}
        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp(500 if calls["n"] == 1 else 200)

    monkeypatch.setattr(edgar.httpx, "get", fake_get)
    monkeypatch.setattr(edgar.time, "sleep", lambda s: None)
    assert edgar._http_get_json("https://x") == {"ok": True}
    assert calls["n"] == 2


def test_http_get_json_returns_none_after_two_failures(monkeypatch):
    class FakeResp:
        status_code = 503
        def raise_for_status(self):
            pass

    monkeypatch.setattr(edgar.httpx, "get", lambda *a, **kw: FakeResp())
    monkeypatch.setattr(edgar.time, "sleep", lambda s: None)
    assert edgar._http_get_json("https://x") is None


def test_http_get_json_returns_none_on_404_no_retry(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        status_code = 404
        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(edgar.httpx, "get", fake_get)
    assert edgar._http_get_json("https://x") is None
    assert calls["n"] == 1   # 404 = "company not in EDGAR", do not retry
```

- [ ] **Step 6: Run tests to verify they fail**

Expected: 4 new failures with AttributeError on `_http_get_json`.

- [ ] **Step 7: Implement `_http_get_json`**

Add to `report/edgar.py`:

```python
import httpx  # add to top-of-file imports
```

Then append:

```python
def _http_get_json(url: str) -> dict | None:
    """GET `url`, return parsed JSON or None on failure.
    - 5xx / 429 / network error: retry once after HTTP_RETRY_DELAY.
    - 4xx other than 429: no retry, log info, return None.
    Never raises."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for attempt in (1, 2):
        try:
            resp = httpx.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            code = resp.status_code
            if code == 200:
                return resp.json()
            if code in (429,) or code >= 500:
                if attempt == 1:
                    logger.warning(f"[edgar] HTTP {code} from {url}, retry in {HTTP_RETRY_DELAY}s")
                    time.sleep(HTTP_RETRY_DELAY)
                    continue
                logger.warning(f"[edgar] HTTP {code} from {url}, giving up after retry")
                return None
            logger.info(f"[edgar] HTTP {code} from {url}, not retrying")
            return None
        except (httpx.HTTPError, ValueError) as e:
            if attempt == 1:
                logger.warning(f"[edgar] {type(e).__name__} from {url}, retry: {e}")
                time.sleep(HTTP_RETRY_DELAY)
                continue
            logger.warning(f"[edgar] {type(e).__name__} from {url}, giving up: {e}")
            return None
    return None
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
uv run pytest tests/test_report_edgar.py -v
```

Expected: 10 passed.

- [ ] **Step 9: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py
git commit -m "feat(edgar): HTTP fetch with single retry on 5xx/429"
```

---

## Task 3: Ticker→CIK lookup with cache

**Files:**
- Modify: `report/edgar.py`
- Modify: `tests/test_report_edgar.py`
- Create: `tests/fixtures/edgar/company_tickers.json`

- [ ] **Step 1: Create fixture `tests/fixtures/edgar/company_tickers.json`**

Real SEC structure: dict keyed by integer string with `cik_str`, `ticker`, `title`. Trimmed:

```json
{
  "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
  "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
  "2": {"cik_str": 1403161, "ticker": "V", "title": "Visa Inc."}
}
```

- [ ] **Step 2: Write failing tests for `_load_ticker_cik_map` and `_get_cik`**

Append to `tests/test_report_edgar.py`:

```python
FIXTURES = Path(__file__).parent / "fixtures" / "edgar"


def test_parse_ticker_cik_map_zero_pads_cik():
    raw = json.loads((FIXTURES / "company_tickers.json").read_text())
    table = edgar._parse_ticker_cik_map(raw)
    assert table["AAPL"] == "0000320193"
    assert table["V"] == "0001403161"
    assert table["GOOGL"] == "0000001652044"[-10:]   # 10-digit zero-padded


def test_parse_ticker_cik_map_uppercases_ticker():
    raw = {"0": {"cik_str": 1, "ticker": "tsla", "title": "Tesla"}}
    table = edgar._parse_ticker_cik_map(raw)
    assert "TSLA" in table


def test_get_cik_uses_cache_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    cache_path = tmp_path / "company_tickers.json"
    cache_path.write_text(
        (FIXTURES / "company_tickers.json").read_text()
    )
    # _get_cik should not hit the network when cache is fresh
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: pytest.fail("network hit"))
    edgar._cached_ticker_map = None    # reset module-level memo
    assert edgar._get_cik("AAPL") == "0000320193"


def test_get_cik_returns_none_for_unknown_ticker(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "company_tickers.json").write_text(
        (FIXTURES / "company_tickers.json").read_text()
    )
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: None)
    edgar._cached_ticker_map = None
    assert edgar._get_cik("ZZZZ") is None


def test_get_cik_returns_none_when_network_and_cache_both_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: None)
    edgar._cached_ticker_map = None
    assert edgar._get_cik("AAPL") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Expected: 5 new failures (AttributeError on `_parse_ticker_cik_map` / `_get_cik`).

- [ ] **Step 4: Implement parser and getter**

Append to `report/edgar.py`:

```python
# In-memory memo across calls within one process (rebuilt on first miss).
_cached_ticker_map: dict[str, str] | None = None


def _parse_ticker_cik_map(raw: dict) -> dict[str, str]:
    """Convert SEC's int-keyed dict to {TICKER: 10-digit CIK string}."""
    out: dict[str, str] = {}
    for entry in raw.values():
        try:
            ticker = str(entry["ticker"]).upper()
            cik = f"{int(entry['cik_str']):010d}"
        except (KeyError, TypeError, ValueError):
            continue
        out[ticker] = cik
    return out


def _refresh_ticker_cik_map() -> dict[str, str] | None:
    """Read cache → if stale or missing, fetch from SEC → write cache → parse."""
    cache_path = CACHE_DIR / "company_tickers.json"
    if _is_fresh(cache_path, COMPANY_TICKERS_TTL):
        cached = _load_json_cache(cache_path)
        if cached:
            return _parse_ticker_cik_map(cached)
    fresh = _http_get_json(COMPANY_TICKERS_URL)
    if fresh:
        _save_json_cache(cache_path, fresh)
        return _parse_ticker_cik_map(fresh)
    # Fall back to stale cache rather than nothing.
    cached = _load_json_cache(cache_path)
    if cached:
        logger.info("[edgar] using stale company_tickers.json (network failed)")
        return _parse_ticker_cik_map(cached)
    return None


def _get_cik(ticker: str) -> str | None:
    """Resolve `ticker` to a 10-digit CIK string; None if not found."""
    global _cached_ticker_map
    if _cached_ticker_map is None:
        _cached_ticker_map = _refresh_ticker_cik_map()
    if _cached_ticker_map is None:
        return None
    return _cached_ticker_map.get(ticker.upper())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_report_edgar.py -v
```

Expected: 15 passed.

- [ ] **Step 6: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py tests/fixtures/edgar/company_tickers.json
git commit -m "feat(edgar): ticker->CIK lookup with TTL cache"
```

---

## Task 4: companyfacts fetch with cache

**Files:**
- Modify: `report/edgar.py`
- Modify: `tests/test_report_edgar.py`

- [ ] **Step 1: Write failing tests for `_fetch_companyfacts`**

Append to `tests/test_report_edgar.py`:

```python
def test_fetch_companyfacts_uses_cache_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    cik = "0000320193"
    cache_path = tmp_path / f"CIK{cik}.json"
    cache_path.write_text('{"facts": {"us-gaap": {}}}')
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: pytest.fail("network hit"))
    assert edgar._fetch_companyfacts(cik) == {"facts": {"us-gaap": {}}}


def test_fetch_companyfacts_fetches_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    cik = "0000320193"
    payload = {"facts": {"us-gaap": {"Revenues": {}}}}
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: payload)
    got = edgar._fetch_companyfacts(cik)
    assert got == payload
    # Cache should now exist.
    assert (tmp_path / f"CIK{cik}.json").is_file()


def test_fetch_companyfacts_returns_none_when_404(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: None)
    assert edgar._fetch_companyfacts("0000000001") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 3 new failures (AttributeError on `_fetch_companyfacts`).

- [ ] **Step 3: Implement `_fetch_companyfacts`**

Append to `report/edgar.py`:

```python
def _fetch_companyfacts(cik: str) -> dict | None:
    """Return the parsed companyfacts JSON for `cik` (10-digit string).
    Uses the 7-day TTL cache; on cache miss, fetches and writes the cache.
    Returns None if both cache and network fail."""
    cache_path = CACHE_DIR / f"CIK{cik}.json"
    if _is_fresh(cache_path, COMPANYFACTS_TTL):
        cached = _load_json_cache(cache_path)
        if cached:
            return cached
    fresh = _http_get_json(COMPANYFACTS_URL.format(cik=cik))
    if fresh:
        _save_json_cache(cache_path, fresh)
        return fresh
    cached = _load_json_cache(cache_path)
    if cached:
        logger.info(f"[edgar] using stale companyfacts for CIK {cik}")
        return cached
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py
git commit -m "feat(edgar): companyfacts fetch with 7-day TTL cache"
```

---

## Task 5: XBRL concept matching with fallback chain

**Files:**
- Modify: `report/edgar.py`
- Modify: `tests/test_report_edgar.py`
- Create: `tests/fixtures/edgar/companyfacts_aapl_minimal.json`
- Create: `tests/fixtures/edgar/companyfacts_v_alt_revenue.json`

- [ ] **Step 1: Create AAPL fixture**

Trimmed real shape — `companyfacts.facts.us-gaap.<concept>.units.<unit>` is a list of fact dicts. Each fact has `start`, `end`, `val`, `accn`, `fy`, `fp`, `form`, `filed`. Keep 6 fiscal years annual + 8 quarters of `Revenues` and `EarningsPerShareDiluted`. Save to `tests/fixtures/edgar/companyfacts_aapl_minimal.json`:

```json
{
  "cik": 320193,
  "entityName": "Apple Inc.",
  "facts": {
    "us-gaap": {
      "Revenues": {
        "label": "Revenues",
        "units": {
          "USD": [
            {"start": "2019-09-29", "end": "2020-09-26", "val": 274515000000, "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-10-30"},
            {"start": "2020-09-27", "end": "2021-09-25", "val": 365817000000, "fy": 2021, "fp": "FY", "form": "10-K", "filed": "2021-10-29"},
            {"start": "2021-09-26", "end": "2022-09-24", "val": 394328000000, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2022-10-28"},
            {"start": "2022-09-25", "end": "2023-09-30", "val": 383285000000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
            {"start": "2023-10-01", "end": "2024-09-28", "val": 391035000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
            {"start": "2024-09-29", "end": "2025-09-27", "val": 416000000000, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-03"},
            {"start": "2024-09-29", "end": "2024-12-28", "val": 124300000000, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-01-30"},
            {"start": "2024-12-29", "end": "2025-03-29", "val":  95400000000, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-05-01"},
            {"start": "2025-03-30", "end": "2025-06-28", "val":  85700000000, "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-07-31"},
            {"start": "2023-10-01", "end": "2023-12-30", "val": 119575000000, "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": "2024-02-01"},
            {"start": "2023-12-31", "end": "2024-03-30", "val":  90753000000, "fy": 2024, "fp": "Q2", "form": "10-Q", "filed": "2024-05-02"},
            {"start": "2024-03-31", "end": "2024-06-29", "val":  85777000000, "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-01"}
          ]
        }
      },
      "EarningsPerShareDiluted": {
        "label": "Earnings Per Share, Diluted",
        "units": {
          "USD/shares": [
            {"start": "2019-09-29", "end": "2020-09-26", "val": 3.28, "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-10-30"},
            {"start": "2020-09-27", "end": "2021-09-25", "val": 5.61, "fy": 2021, "fp": "FY", "form": "10-K", "filed": "2021-10-29"},
            {"start": "2021-09-26", "end": "2022-09-24", "val": 6.11, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2022-10-28"},
            {"start": "2022-09-25", "end": "2023-09-30", "val": 6.13, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
            {"start": "2023-10-01", "end": "2024-09-28", "val": 6.75, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
            {"start": "2024-09-29", "end": "2025-09-27", "val": 7.40, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-03"},
            {"start": "2024-09-29", "end": "2024-12-28", "val": 2.40, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-01-30"},
            {"start": "2024-12-29", "end": "2025-03-29", "val": 1.65, "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-05-01"},
            {"start": "2025-03-30", "end": "2025-06-28", "val": 1.55, "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-07-31"},
            {"start": "2023-10-01", "end": "2023-12-30", "val": 2.18, "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": "2024-02-01"},
            {"start": "2023-12-31", "end": "2024-03-30", "val": 1.53, "fy": 2024, "fp": "Q2", "form": "10-Q", "filed": "2024-05-02"},
            {"start": "2024-03-31", "end": "2024-06-29", "val": 1.40, "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-01"}
          ]
        }
      }
    }
  }
}
```

- [ ] **Step 2: Create alt-revenue fixture (Visa)**

Save `tests/fixtures/edgar/companyfacts_v_alt_revenue.json` — only `RevenueFromContractWithCustomerExcludingAssessedTax` populated (no `Revenues`):

```json
{
  "cik": 1403161,
  "entityName": "Visa Inc.",
  "facts": {
    "us-gaap": {
      "RevenueFromContractWithCustomerExcludingAssessedTax": {
        "label": "Revenue",
        "units": {
          "USD": [
            {"start": "2019-10-01", "end": "2020-09-30", "val": 21846000000, "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-11-19"},
            {"start": "2020-10-01", "end": "2021-09-30", "val": 24105000000, "fy": 2021, "fp": "FY", "form": "10-K", "filed": "2021-11-18"},
            {"start": "2021-10-01", "end": "2022-09-30", "val": 29310000000, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2022-11-15"},
            {"start": "2022-10-01", "end": "2023-09-30", "val": 32653000000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-15"},
            {"start": "2023-10-01", "end": "2024-09-30", "val": 35926000000, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-13"}
          ]
        }
      },
      "EarningsPerShareDiluted": {
        "label": "Earnings Per Share, Diluted",
        "units": {
          "USD/shares": [
            {"start": "2019-10-01", "end": "2020-09-30", "val": 4.89, "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-11-19"},
            {"start": "2020-10-01", "end": "2021-09-30", "val": 5.91, "fy": 2021, "fp": "FY", "form": "10-K", "filed": "2021-11-18"},
            {"start": "2021-10-01", "end": "2022-09-30", "val": 7.50, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2022-11-15"},
            {"start": "2022-10-01", "end": "2023-09-30", "val": 8.28, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-15"},
            {"start": "2023-10-01", "end": "2024-09-30", "val": 9.73, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-13"}
          ]
        }
      }
    }
  }
}
```

- [ ] **Step 3: Write failing tests for `_match_concept_facts`**

Append to `tests/test_report_edgar.py`:

```python
def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_match_concept_returns_first_match(monkeypatch):
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    out = edgar._match_concept_facts(
        facts, ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"), "USD"
    )
    assert out is not None
    assert any(f["fy"] == 2024 and f["fp"] == "FY" for f in out)


def test_match_concept_falls_back_when_first_missing():
    facts = _load_fixture("companyfacts_v_alt_revenue.json")
    out = edgar._match_concept_facts(
        facts, ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"), "USD"
    )
    assert out is not None
    # Should have used the alt concept.
    assert any(f["fy"] == 2024 and f["fp"] == "FY" for f in out)


def test_match_concept_returns_none_when_no_match():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    assert edgar._match_concept_facts(facts, ("NoSuchConcept",), "USD") is None


def test_match_concept_handles_missing_unit():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    # Revenues exists but only with USD unit; asking for EUR returns None.
    assert edgar._match_concept_facts(facts, ("Revenues",), "EUR") is None
```

- [ ] **Step 4: Run tests to verify they fail**

Expected: 4 new failures.

- [ ] **Step 5: Implement `_match_concept_facts`**

Append to `report/edgar.py`:

```python
REVENUE_CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
EPS_CONCEPTS = (
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
)
USD = "USD"
USD_PER_SHARE = "USD/shares"


def _match_concept_facts(
    companyfacts: dict, candidates: tuple[str, ...], unit: str
) -> list[dict] | None:
    """Walk `candidates` in order; return the first concept's `units[unit]`
    fact list. None if no concept matches or matching concept lacks the unit."""
    try:
        gaap = companyfacts["facts"]["us-gaap"]
    except (KeyError, TypeError):
        return None
    for name in candidates:
        concept = gaap.get(name)
        if not concept:
            continue
        units = concept.get("units") or {}
        facts = units.get(unit)
        if facts:
            return facts
    return None
```

- [ ] **Step 6: Run tests to verify they pass**

Expected: 22 passed.

- [ ] **Step 7: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py tests/fixtures/edgar/companyfacts_aapl_minimal.json tests/fixtures/edgar/companyfacts_v_alt_revenue.json
git commit -m "feat(edgar): XBRL concept matching with fallback chain"
```

---

## Task 6: Annual fact selection + 5y YoY extraction

**Files:**
- Modify: `report/edgar.py`
- Modify: `tests/test_report_edgar.py`

- [ ] **Step 1: Write failing tests for `_select_annual_facts` and `_extract_annual_yoy`**

Append to `tests/test_report_edgar.py`:

```python
def test_select_annual_facts_filters_to_10k_fy_and_sorts():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    annual = edgar._select_annual_facts(raw)
    # 6 fiscal years, oldest first
    assert len(annual) == 6
    assert annual[0]["fy"] == 2020
    assert annual[-1]["fy"] == 2025


def test_select_annual_facts_dedupes_amendments(tmp_path):
    raw = [
        {"end": "2024-09-28", "val": 100, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
        {"end": "2024-09-28", "val": 105, "fy": 2024, "fp": "FY", "form": "10-K/A", "filed": "2025-02-01"},
    ]
    out = edgar._select_annual_facts(raw)
    assert len(out) == 1
    # Latest filed wins (the amendment).
    assert out[0]["val"] == 105


def test_extract_annual_yoy_5y_full_history():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    yoy = edgar._extract_annual_yoy(raw, years_back=5)
    assert len(yoy) == 5
    # Oldest first: FY2021 vs FY2020 = (365817 - 274515) / 274515 = +33.26%
    assert yoy[0] == pytest.approx(33.26, rel=0.01)
    # Newest: FY2025 vs FY2024 = (416000 - 391035) / 391035 = +6.38%
    assert yoy[-1] == pytest.approx(6.38, rel=0.01)


def test_extract_annual_yoy_pads_with_none_when_history_short():
    raw = [
        {"end": "2024-09-28", "val": 100, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
        {"end": "2025-09-27", "val": 110, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
    ]
    yoy = edgar._extract_annual_yoy(raw, years_back=5)
    assert yoy == [None, None, None, None, pytest.approx(10.0, rel=0.01)]


def test_extract_annual_yoy_skips_bad_prior():
    raw = [
        {"end": "2023-09-30", "val": -10, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
        {"end": "2024-09-28", "val":  20, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
        {"end": "2025-09-27", "val":  30, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
    ]
    yoy = edgar._extract_annual_yoy(raw, years_back=5)
    # FY24 vs FY23: prior was -10 (negative) → None
    # FY25 vs FY24: 20 → 30 = +50%
    assert yoy[-2] is None
    assert yoy[-1] == pytest.approx(50.0, rel=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 5 new failures.

- [ ] **Step 3: Implement `_select_annual_facts`, `_compute_yoy`, `_extract_annual_yoy`**

Append to `report/edgar.py`:

```python
def _compute_yoy(current: float | None, prior: float | None) -> float | None:
    """Mirror of enrich.compute_yoy: None if either is None or prior <= 0."""
    if current is None or prior is None:
        return None
    if prior <= 0:
        return None
    return (current - prior) / prior * 100.0


def _select_annual_facts(facts: list[dict]) -> list[dict]:
    """Return only 10-K/10-K/A FY filings, deduped by `end` date (latest filed
    wins so amendments override originals), sorted oldest→newest by `end`."""
    by_end: dict[str, dict] = {}
    for f in facts:
        if f.get("fp") != "FY":
            continue
        if not str(f.get("form", "")).startswith("10-K"):
            continue
        end = f.get("end")
        if not end:
            continue
        existing = by_end.get(end)
        if existing is None or str(f.get("filed", "")) > str(existing.get("filed", "")):
            by_end[end] = f
    return sorted(by_end.values(), key=lambda f: f["end"])


def _extract_annual_yoy(facts: list[dict] | None, years_back: int = 5) -> list[float | None]:
    """Up to `years_back` YoY % datapoints in oldest→newest order. Pads
    leading slots with None when history is short."""
    if not facts:
        return [None] * years_back
    annual = _select_annual_facts(facts)
    values = [f.get("val") for f in annual]
    out: list[float | None] = []
    # We want the most recent `years_back + 1` values to compute `years_back` YoY.
    needed_pairs = years_back
    for i in range(-needed_pairs, 0):
        try:
            current = values[i]
            prior = values[i - 1]
        except IndexError:
            out.append(None)
            continue
        out.append(_compute_yoy(current, prior))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Expected: 27 passed.

- [ ] **Step 5: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py
git commit -m "feat(edgar): annual fact selection + 5-year YoY extraction"
```

---

## Task 7: Quarterly fact selection with Q4 derivation + 4q YoY

**Files:**
- Modify: `report/edgar.py`
- Modify: `tests/test_report_edgar.py`
- Create: `tests/fixtures/edgar/companyfacts_q2_gap.json`

- [ ] **Step 1: Create Q4-gap fixture**

Save `tests/fixtures/edgar/companyfacts_q2_gap.json` — full annual + Q1/Q3 of FY2024 but **missing Q2** so the FY2024 Q4 derivation must yield None:

```json
{
  "cik": 999999,
  "entityName": "GapCo",
  "facts": {
    "us-gaap": {
      "Revenues": {
        "units": {
          "USD": [
            {"start": "2022-10-01", "end": "2023-09-30", "val": 1000, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-15"},
            {"start": "2023-10-01", "end": "2024-09-30", "val": 1200, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-15"},
            {"start": "2023-10-01", "end": "2023-12-31", "val": 250, "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": "2024-02-01"},
            {"start": "2024-04-01", "end": "2024-06-30", "val": 320, "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-01"},
            {"start": "2022-10-01", "end": "2022-12-31", "val": 220, "fy": 2023, "fp": "Q1", "form": "10-Q", "filed": "2023-02-01"},
            {"start": "2023-01-01", "end": "2023-03-31", "val": 240, "fy": 2023, "fp": "Q2", "form": "10-Q", "filed": "2023-05-01"},
            {"start": "2023-04-01", "end": "2023-06-30", "val": 260, "fy": 2023, "fp": "Q3", "form": "10-Q", "filed": "2023-08-01"}
          ]
        }
      }
    }
  }
}
```

- [ ] **Step 2: Write failing tests for quarterly extraction**

Append to `tests/test_report_edgar.py`:

```python
def test_select_quarterly_facts_uses_10q_directly_for_q1_q2_q3():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    quarters = edgar._select_quarterly_facts(raw)
    # Should include Apple's Q1 2024 = 119575000000
    found = [q for q in quarters if q["end"] == "2023-12-30"]
    assert len(found) == 1
    assert found[0]["val"] == 119575000000


def test_select_quarterly_facts_derives_q4_from_fy_minus_q1q2q3():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    quarters = edgar._select_quarterly_facts(raw)
    # FY2024 Q4 = 391035 - (119575 + 90753 + 85777) = 94930 (in millions: 94930000000)
    fy24_q4 = [q for q in quarters if q.get("fy") == 2024 and q.get("fp") == "Q4"]
    assert len(fy24_q4) == 1
    assert fy24_q4[0]["val"] == 391035000000 - (119575000000 + 90753000000 + 85777000000)


def test_select_quarterly_facts_skips_q4_when_a_component_missing():
    facts = _load_fixture("companyfacts_q2_gap.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    quarters = edgar._select_quarterly_facts(raw)
    # FY2024 Q2 missing → no FY2024 Q4 emitted.
    fy24_q4 = [q for q in quarters if q.get("fy") == 2024 and q.get("fp") == "Q4"]
    assert fy24_q4 == []
    # FY2023 Q4 should still be present (220 + 240 + 260 = 720; FY=1000; Q4=280).
    fy23_q4 = [q for q in quarters if q.get("fy") == 2023 and q.get("fp") == "Q4"]
    assert len(fy23_q4) == 1
    assert fy23_q4[0]["val"] == 280


def test_extract_quarterly_yoy_4q_with_full_history():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    yoy, labels = edgar._extract_quarterly_yoy(raw, n_quarters=4)
    assert len(yoy) == 4
    assert len(labels) == 4
    # Newest = Q3 of FY2025 (Jun 2025) = 85.7B vs Q3 FY2024 (Jun 2024) = 85.777B
    # = (85700 - 85777) / 85777 ≈ -0.09%
    assert yoy[-1] == pytest.approx(-0.09, abs=0.5)
    # Each label is "Mon'YY"
    for lbl in labels:
        assert len(lbl) == 6
        assert lbl[3] == "'"


def test_extract_quarterly_yoy_pads_when_short_history():
    raw = [
        {"start": "2024-10-01", "end": "2024-12-31", "val": 100, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-02-01"},
    ]
    yoy, labels = edgar._extract_quarterly_yoy(raw, n_quarters=4)
    assert yoy == [None, None, None, None]
    assert labels[-1] == "Dec'24"
```

- [ ] **Step 3: Run tests to verify they fail**

Expected: 5 new failures.

- [ ] **Step 4: Implement `_select_quarterly_facts` and `_extract_quarterly_yoy`**

Append to `report/edgar.py`:

```python
from datetime import date as _date


def _parse_iso_date(s: str) -> _date | None:
    try:
        return _date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _period_label(end_date_iso: str) -> str:
    """'2024-12-30' -> \"Dec'24\". Empty string on parse failure."""
    d = _parse_iso_date(end_date_iso)
    if d is None:
        return ""
    return d.strftime("%b'%y")


def _select_quarterly_facts(facts: list[dict]) -> list[dict]:
    """Combine 10-Q reported quarters (Q1/Q2/Q3) with derived Q4
    (= FY value − sum of same-FY Q1+Q2+Q3). Q4 derivation skipped
    when any component is missing. Returned list sorted oldest→newest by `end`."""
    # Group by (fy, fp) for Q4 derivation; dedupe with latest-filed-wins.
    by_key: dict[tuple[int, str], dict] = {}
    for f in facts:
        fp = f.get("fp")
        form = str(f.get("form", ""))
        if fp not in ("Q1", "Q2", "Q3", "FY"):
            continue
        if fp in ("Q1", "Q2", "Q3") and not form.startswith("10-Q"):
            continue
        if fp == "FY" and not form.startswith("10-K"):
            continue
        try:
            fy = int(f["fy"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (fy, fp)
        existing = by_key.get(key)
        if existing is None or str(f.get("filed", "")) > str(existing.get("filed", "")):
            by_key[key] = f

    out: list[dict] = []
    # Emit Q1/Q2/Q3 directly.
    for (fy, fp), f in by_key.items():
        if fp in ("Q1", "Q2", "Q3"):
            out.append(f)
    # Derive Q4 per fiscal year.
    fy_set = {fy for (fy, fp) in by_key if fp == "FY"}
    for fy in fy_set:
        fy_fact = by_key.get((fy, "FY"))
        q1 = by_key.get((fy, "Q1"))
        q2 = by_key.get((fy, "Q2"))
        q3 = by_key.get((fy, "Q3"))
        if not (fy_fact and q1 and q2 and q3):
            continue
        try:
            q4_val = float(fy_fact["val"]) - (
                float(q1["val"]) + float(q2["val"]) + float(q3["val"])
            )
        except (KeyError, TypeError, ValueError):
            continue
        # Use FY's `end` for the Q4 period (typically the fiscal year end).
        out.append({
            "end": fy_fact["end"],
            "val": q4_val,
            "fy": fy,
            "fp": "Q4",
            "form": "derived",
            "filed": fy_fact.get("filed", ""),
        })
    out.sort(key=lambda f: f.get("end") or "")
    return out


def _extract_quarterly_yoy(
    facts: list[dict] | None, n_quarters: int = 4
) -> tuple[list[float | None], list[str]]:
    """Return (yoy_pct list, labels list), each length `n_quarters`,
    oldest→newest. Each YoY pairs each quarter with the same calendar
    quarter one year prior (4 quarters back in the chronological list).
    Leading slots are None when history is too short."""
    quarters = _select_quarterly_facts(facts) if facts else []
    values = [q.get("val") for q in quarters]
    ends = [q.get("end") or "" for q in quarters]

    yoy: list[float | None] = []
    labels: list[str] = []
    for i in range(-n_quarters, 0):
        try:
            current = values[i]
            prior = values[i - 4]
        except IndexError:
            yoy.append(None)
        else:
            yoy.append(_compute_yoy(current, prior))
        try:
            labels.append(_period_label(ends[i]))
        except IndexError:
            labels.append("")
    return yoy, labels
```

- [ ] **Step 5: Run tests to verify they pass**

Expected: 32 passed.

- [ ] **Step 6: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py tests/fixtures/edgar/companyfacts_q2_gap.json
git commit -m "feat(edgar): quarterly extraction with derived Q4 and 4-quarter YoY"
```

---

## Task 8: Latest-quarter values + public `fetch_edgar_fundamentals`

**Files:**
- Modify: `report/edgar.py`
- Modify: `tests/test_report_edgar.py`

- [ ] **Step 1: Write failing tests for `_latest_quarter_value` and the public function**

Append:

```python
def test_latest_quarter_value_returns_newest_quarter():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    val, yoy = edgar._latest_quarter_with_yoy(raw)
    # Newest is Q3 FY2025 ending 2025-06-28 = 85.7B
    assert val == 85700000000
    assert yoy == pytest.approx(-0.09, abs=0.5)


def test_latest_quarter_value_returns_none_for_empty():
    val, yoy = edgar._latest_quarter_with_yoy([])
    assert val is None and yoy is None


def test_fetch_edgar_fundamentals_full_path(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._cached_ticker_map = {"AAPL": "0000320193"}
    cf = _load_fixture("companyfacts_aapl_minimal.json")

    def fake_companyfacts(cik):
        assert cik == "0000320193"
        return cf

    monkeypatch.setattr(edgar, "_fetch_companyfacts", fake_companyfacts)
    out = edgar.fetch_edgar_fundamentals("AAPL")
    assert out is not None
    assert out["revenue_latest_q"] == 85700000000
    assert out["eps_latest_q"] == pytest.approx(1.55)
    assert len(out["annual_revenue_yoy_5y"]) == 5
    assert len(out["quarterly_revenue_yoy_4q"]) == 4
    assert len(out["quarterly_revenue_yoy_4q_labels"]) == 4
    assert out["annual_revenue_yoy_5y"][0] == pytest.approx(33.26, rel=0.01)


def test_fetch_edgar_fundamentals_returns_none_when_cik_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._cached_ticker_map = {}   # no tickers known
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: {})  # also empty over network
    assert edgar.fetch_edgar_fundamentals("ZZZZ") is None


def test_fetch_edgar_fundamentals_returns_none_when_companyfacts_404(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._cached_ticker_map = {"AAPL": "0000320193"}
    monkeypatch.setattr(edgar, "_fetch_companyfacts", lambda cik: None)
    assert edgar.fetch_edgar_fundamentals("AAPL") is None


def test_fetch_edgar_fundamentals_alt_revenue_concept(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._cached_ticker_map = {"V": "0001403161"}
    cf = _load_fixture("companyfacts_v_alt_revenue.json")
    monkeypatch.setattr(edgar, "_fetch_companyfacts", lambda cik: cf)
    out = edgar.fetch_edgar_fundamentals("V")
    assert out is not None
    # 5 fiscal years → 4 YoY pairs filled, oldest slot None.
    assert out["annual_revenue_yoy_5y"][0] is None
    assert out["annual_revenue_yoy_5y"][-1] == pytest.approx(10.02, rel=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 6 new failures.

- [ ] **Step 3: Implement `_latest_quarter_with_yoy` and `fetch_edgar_fundamentals`**

Append:

```python
def _latest_quarter_with_yoy(
    facts: list[dict] | None,
) -> tuple[float | None, float | None]:
    """Most recent quarterly value + YoY vs same calendar quarter one year prior."""
    if not facts:
        return (None, None)
    quarters = _select_quarterly_facts(facts)
    if not quarters:
        return (None, None)
    latest = quarters[-1]
    val = latest.get("val")
    prior_val = quarters[-5].get("val") if len(quarters) >= 5 else None
    return (val, _compute_yoy(val, prior_val))


def fetch_edgar_fundamentals(ticker: str) -> dict | None:
    """Return EDGAR-sourced fundamentals dict for `ticker`, or None on failure.
    Caller should fall back to yfinance on None and per-field on partials."""
    cik = _get_cik(ticker)
    if cik is None:
        logger.info(f"[edgar] no CIK for {ticker}; skipping EDGAR")
        return None
    cf = _fetch_companyfacts(cik)
    if cf is None:
        logger.warning(f"[edgar] companyfacts unavailable for {ticker} (CIK {cik})")
        return None

    rev_facts = _match_concept_facts(cf, REVENUE_CONCEPTS, USD)
    eps_facts = _match_concept_facts(cf, EPS_CONCEPTS, USD_PER_SHARE)
    if rev_facts is None and eps_facts is None:
        logger.warning(f"[edgar] no matching revenue or EPS concepts for {ticker}")
        return None

    rev_q_yoy, rev_labels = _extract_quarterly_yoy(rev_facts, 4)
    eps_q_yoy, eps_labels = _extract_quarterly_yoy(eps_facts, 4)
    rev_latest, rev_latest_yoy = _latest_quarter_with_yoy(rev_facts)
    eps_latest, eps_latest_yoy = _latest_quarter_with_yoy(eps_facts)

    return {
        "eps_latest_q": eps_latest,
        "eps_latest_q_yoy_pct": eps_latest_yoy,
        "revenue_latest_q": rev_latest,
        "revenue_latest_q_yoy_pct": rev_latest_yoy,
        "annual_eps_yoy_5y": _extract_annual_yoy(eps_facts, 5),
        "annual_revenue_yoy_5y": _extract_annual_yoy(rev_facts, 5),
        "quarterly_eps_yoy_4q": eps_q_yoy,
        "quarterly_eps_yoy_4q_labels": eps_labels,
        "quarterly_revenue_yoy_4q": rev_q_yoy,
        "quarterly_revenue_yoy_4q_labels": rev_labels,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Expected: 38 passed.

- [ ] **Step 5: Commit**

```bash
git add report/edgar.py tests/test_report_edgar.py
git commit -m "feat(edgar): public fetch_edgar_fundamentals entry point"
```

---

## Task 9: Rename yfinance fallback fields to `_5y` / `_4q`

**Files:**
- Modify: `report/enrich.py`
- Modify: `tests/test_report_enrich.py`

- [ ] **Step 1: Update existing tests in `tests/test_report_enrich.py` to use new names**

Two categories of test in this file:

1. **Tests of the back-compat shim** `extract_annual_yoy_3y` (lines 47-53 `test_extract_annual_yoy_3y_revenue`, lines 56-60 `test_extract_annual_yoy_3y_too_few_rows`). **Keep unchanged.** The shim and these tests stay; they verify the 3y wrapper still works and have nothing to do with the schema rename.

2. **Tests that read fields off the `data` dict returned by `fetch_ticker_data`** (lines ~97-104, 192, 218-219). Rename `data["annual_*_yoy_3y"]` → `data["annual_*_yoy_5y"]` and `data["quarterly_*_yoy_2q*"]` → `data["quarterly_*_yoy_4q*"]`. Update array length expectations:

| Old assertion | New assertion |
|---|---|
| `data["annual_revenue_yoy_3y"][-1] == pytest.approx(10.0, rel=0.01)` | `data["annual_revenue_yoy_5y"][-1] == pytest.approx(10.0, rel=0.01)` (last index, which is the most-recent YoY, stays the same arithmetic) |
| `data["annual_eps_yoy_3y"][-1] == ...` | `data["annual_eps_yoy_5y"][-1] == ...` |
| `data["annual_revenue_yoy_3y"][0] == pytest.approx(16.67, rel=0.01)` | `data["annual_revenue_yoy_5y"][0] == pytest.approx(25.0, rel=0.01)` (oldest YoY in the new 6-year fixture: 2000→2500 = +25%) |
| `data["annual_revenue_yoy_3y"] == [None, None, None]` | `data["annual_revenue_yoy_5y"] == [None, None, None, None, None]` |
| `len(data["annual_revenue_yoy_3y"]) == 3` | `len(data["annual_revenue_yoy_5y"]) == 5` |

Replace `_fake_annual_income_stmt` in `tests/test_report_enrich.py` to provide 6 fiscal years (most recent first) so YoY tests have full history:

```python
def _fake_annual_income_stmt() -> pd.DataFrame:
    """6 fiscal years (most recent first) — enough for full 5-YoY extraction."""
    cols = pd.to_datetime([
        "2025-12-31", "2024-12-31", "2023-12-31",
        "2022-12-31", "2021-12-31", "2020-12-31",
    ])
    data = {
        "TotalRevenue": [4400, 4000, 3500, 3000, 2500, 2000],
        "DilutedEPS":   [4.40, 4.00, 3.50, 3.00, 2.50, 2.00],
    }
    return pd.DataFrame(data, index=cols).T
```

- [ ] **Step 2: Run tests to confirm they fail (red phase)**

```bash
uv run pytest tests/test_report_enrich.py -v
```

Expected: many failures referencing old field names / shapes.

- [ ] **Step 3: Update `report/enrich.py` to emit `_5y` / `_4q` keys**

In `report/enrich.py`:

1. Replace the `data` template lines (the `annual_*_yoy_3y`, `quarterly_*_yoy_2q` and `_labels` entries):

```python
        # Annual earnings increases — past 5 fiscal years of YoY (CANSLIM "A")
        "annual_eps_yoy_5y": [None, None, None, None, None],
        "annual_revenue_yoy_5y": [None, None, None, None, None],
        # Past 4 quarters of YoY % + period labels (CANSLIM "C" trajectory)
        "quarterly_eps_yoy_4q": [None, None, None, None],
        "quarterly_eps_yoy_4q_labels": ["", "", "", ""],
        "quarterly_revenue_yoy_4q": [None, None, None, None],
        "quarterly_revenue_yoy_4q_labels": ["", "", "", ""],
```

2. Replace the quarterly extraction call site (lines ~231-238):

```python
        eps_q, eps_lbl = extract_quarterly_yoy(qdf, DILUTED_EPS_LABELS, 4)
        if all(v is None for v in eps_q):
            eps_q, eps_lbl = extract_quarterly_yoy(qdf, BASIC_EPS_LABELS, 4)
        data["quarterly_eps_yoy_4q"] = eps_q
        data["quarterly_eps_yoy_4q_labels"] = eps_lbl
        rev_q, rev_lbl = extract_quarterly_yoy(qdf, REVENUE_LABELS, 4)
        data["quarterly_revenue_yoy_4q"] = rev_q
        data["quarterly_revenue_yoy_4q_labels"] = rev_lbl
```

3. Replace the annual extraction call site (lines ~242-249):

```python
        adf = t.income_stmt
        data["annual_eps_yoy_5y"] = extract_annual_yoy(adf, DILUTED_EPS_LABELS, years_back=5)
        if all(v is None for v in data["annual_eps_yoy_5y"]):
            data["annual_eps_yoy_5y"] = extract_annual_yoy(adf, BASIC_EPS_LABELS, years_back=5)
        data["annual_revenue_yoy_5y"] = extract_annual_yoy(adf, REVENUE_LABELS, years_back=5)
```

4. Update `extract_annual_yoy` signature default (line 102) and docstring:

```python
def extract_annual_yoy(
    df: pd.DataFrame,
    row_label: str | tuple[str, ...],
    years_back: int = 5,
) -> list[float | None]:
    """Up to `years_back` YoY datapoints in oldest→newest order
    (e.g. `[FY-5, FY-4, FY-3, FY-2, FY-1]`). yfinance annual frames have
    most-recent fiscal year first, so we reverse before pairing. Pads with
    None when the frame doesn't have enough fiscal years.

    Default 5 — enough for IBD CANSLIM "A" coverage. yfinance free tier
    only returns ~4 fiscal years (3 YoY pairs), so the older slots will
    typically be None when EDGAR isn't available; the EDGAR fast path
    fills them in. See report/edgar.py."""
```

5. Update the back-compat shim (line 127):

```python
def extract_annual_yoy_3y(
    df: pd.DataFrame, row_label: str | tuple[str, ...]
) -> list[float | None]:
    return extract_annual_yoy(df, row_label, years_back=3)
```

(Keep the shim — it's tested and harmless. No callers in production after this task.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report_enrich.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add report/enrich.py tests/test_report_enrich.py
git commit -m "refactor(enrich): rename fundamentals fields to _5y/_4q"
```

---

## Task 10: Wire EDGAR-first orchestration into `enrich.fetch_ticker_data`

**Files:**
- Modify: `report/enrich.py`
- Modify: `tests/test_report_enrich.py`

- [ ] **Step 1: Write failing tests for the EDGAR-first hybrid path**

Append to `tests/test_report_enrich.py`:

```python
def test_fetch_ticker_data_uses_edgar_when_available():
    """EDGAR returns a full fundamentals dict → yfinance income_stmt is NOT consulted."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Apple", "currentPrice": 200, "previousClose": 198}
    # If yfinance income statement WERE consulted, the values below would
    # show up; assert they don't.
    fake_ticker.quarterly_income_stmt = pd.DataFrame(
        {"Total Revenue": [999], "Diluted EPS": [9.9]},
        index=pd.to_datetime(["2026-03-31"]),
    ).T
    fake_ticker.income_stmt = fake_ticker.quarterly_income_stmt
    fake_ticker.earnings_dates = None
    edgar_full = {
        "eps_latest_q": 1.55,
        "eps_latest_q_yoy_pct": 5.0,
        "revenue_latest_q": 85_000_000_000,
        "revenue_latest_q_yoy_pct": 6.0,
        "annual_eps_yoy_5y": [10.0, 11.0, 12.0, 13.0, 14.0],
        "annual_revenue_yoy_5y": [20.0, 21.0, 22.0, 23.0, 24.0],
        "quarterly_eps_yoy_4q": [1.0, 2.0, 3.0, 4.0],
        "quarterly_eps_yoy_4q_labels": ["Sep'24", "Dec'24", "Mar'25", "Jun'25"],
        "quarterly_revenue_yoy_4q": [5.0, 6.0, 7.0, 8.0],
        "quarterly_revenue_yoy_4q_labels": ["Sep'24", "Dec'24", "Mar'25", "Jun'25"],
    }
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=edgar_full):
        data = enrich.fetch_ticker_data("AAPL", "Leaders", "NASDAQ", rs_lookup=lambda t: 95)
    assert data["revenue_latest_q"] == 85_000_000_000   # from EDGAR, not yfinance 999
    assert data["eps_latest_q"] == 1.55
    assert data["annual_revenue_yoy_5y"][-1] == 24.0


def test_fetch_ticker_data_falls_back_to_yfinance_when_edgar_returns_none():
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Penny", "currentPrice": 5, "previousClose": 4.5}
    fake_ticker.quarterly_income_stmt = pd.DataFrame(
        {"Total Revenue": [100, 80], "Diluted EPS": [1.0, 0.9]},
        index=pd.to_datetime(["2026-03-31", "2025-12-31"]),
    ).T
    fake_ticker.income_stmt = pd.DataFrame(
        {"Total Revenue": [400, 360], "Diluted EPS": [4.0, 3.6]},
        index=pd.to_datetime(["2025-12-31", "2024-12-31"]),
    ).T
    fake_ticker.earnings_dates = None
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None):
        data = enrich.fetch_ticker_data("PENNY", "EarningsGap", "NASDAQ", rs_lookup=lambda t: None)
    # yfinance fallback ran → latest_q populated.
    assert data["revenue_latest_q"] == 100
    assert data["eps_latest_q"] == 1.0


def test_fetch_ticker_data_per_field_fallback_when_edgar_partial():
    """EDGAR returns a dict with revenue but EPS fields all None → yfinance
    fills the EPS slots without overwriting the EDGAR revenue values."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Mixed", "currentPrice": 10, "previousClose": 9.5}
    fake_ticker.quarterly_income_stmt = pd.DataFrame(
        {"Total Revenue": [500, 450], "Diluted EPS": [2.5, 2.3]},
        index=pd.to_datetime(["2026-03-31", "2025-12-31"]),
    ).T
    fake_ticker.income_stmt = pd.DataFrame(
        {"Total Revenue": [2000, 1800], "Diluted EPS": [10.0, 9.0]},
        index=pd.to_datetime(["2025-12-31", "2024-12-31"]),
    ).T
    fake_ticker.earnings_dates = None
    edgar_partial = {
        "eps_latest_q": None,
        "eps_latest_q_yoy_pct": None,
        "revenue_latest_q": 9_999_999,         # distinctive EDGAR value
        "revenue_latest_q_yoy_pct": 11.0,
        "annual_eps_yoy_5y": [None] * 5,
        "annual_revenue_yoy_5y": [None, None, None, 50.0, 60.0],
        "quarterly_eps_yoy_4q": [None] * 4,
        "quarterly_eps_yoy_4q_labels": [""] * 4,
        "quarterly_revenue_yoy_4q": [None, None, 30.0, 40.0],
        "quarterly_revenue_yoy_4q_labels": ["", "", "Mar'25", "Jun'25"],
    }
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=edgar_partial):
        data = enrich.fetch_ticker_data("MIX", "Leaders", "NASDAQ", rs_lookup=lambda t: 92)
    # EDGAR's revenue values preserved.
    assert data["revenue_latest_q"] == 9_999_999
    assert data["annual_revenue_yoy_5y"][-1] == 60.0
    # EDGAR EPS was None → yfinance fallback filled it.
    assert data["eps_latest_q"] == 2.5


def test_fetch_ticker_data_skips_edgar_for_non_us_exchange():
    """HK ticker (exchange=HKEX) must not call EDGAR — yfinance is the only source."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Tencent", "currentPrice": 350, "previousClose": 345}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    fake_ticker.earnings_dates = None
    edgar_calls = {"n": 0}

    def _track(_):
        edgar_calls["n"] += 1
        return None

    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", side_effect=_track):
        enrich.fetch_ticker_data("0700.HK", "HKLeaders", "HKEX", rs_lookup=lambda t: 95)
    assert edgar_calls["n"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: 4 new failures (`fetch_edgar_fundamentals` not yet imported into enrich namespace).

- [ ] **Step 3: Modify `report/enrich.py`**

Add to imports near the top:

```python
from report.edgar import fetch_edgar_fundamentals
```

Define US exchange whitelist near the existing label constants:

```python
US_EXCHANGES: frozenset[str] = frozenset({"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"})
```

In `fetch_ticker_data`, **after** the existing `info` block (right before the existing `t.quarterly_income_stmt` try/except, around line 218), insert the EDGAR-first branch:

```python
    # --- EDGAR-first fundamentals (US only) -------------------------------
    edgar_data: dict | None = None
    if exchange.upper() in US_EXCHANGES:
        try:
            edgar_data = fetch_edgar_fundamentals(ticker)
        except Exception as e:
            logger.warning(f"[enrich] {ticker}: EDGAR fetch raised: {e}")
            edgar_data = None
    if edgar_data:
        # Populate any non-None field from EDGAR. Leave Nones for yfinance to fill.
        for k, v in edgar_data.items():
            if v is None:
                continue
            if isinstance(v, list) and all(x is None or x == "" for x in v):
                continue
            data[k] = v
```

Then in the existing `try: qdf = t.quarterly_income_stmt` block, **guard each assignment so EDGAR data is not overwritten**. The cleanest pattern: only call `extract_quarterly_yoy` / `latest_quarterly_with_yoy` when the corresponding `data[key]` is still the default (None / all-None / all-""):

```python
    try:
        qdf = t.quarterly_income_stmt
        if data["eps_latest_q"] is None:
            eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, DILUTED_EPS_LABELS)
            if eps_val is None:
                eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, BASIC_EPS_LABELS)
            data["eps_latest_q"] = eps_val
            data["eps_latest_q_yoy_pct"] = eps_yoy
        if data["revenue_latest_q"] is None:
            rev_val, rev_yoy = latest_quarterly_with_yoy(qdf, REVENUE_LABELS)
            data["revenue_latest_q"] = rev_val
            data["revenue_latest_q_yoy_pct"] = rev_yoy
        if all(v is None for v in data["quarterly_eps_yoy_4q"]):
            eps_q, eps_lbl = extract_quarterly_yoy(qdf, DILUTED_EPS_LABELS, 4)
            if all(v is None for v in eps_q):
                eps_q, eps_lbl = extract_quarterly_yoy(qdf, BASIC_EPS_LABELS, 4)
            data["quarterly_eps_yoy_4q"] = eps_q
            data["quarterly_eps_yoy_4q_labels"] = eps_lbl
        if all(v is None for v in data["quarterly_revenue_yoy_4q"]):
            rev_q, rev_lbl = extract_quarterly_yoy(qdf, REVENUE_LABELS, 4)
            data["quarterly_revenue_yoy_4q"] = rev_q
            data["quarterly_revenue_yoy_4q_labels"] = rev_lbl
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: quarterly fetch failed: {e}")

    try:
        adf = t.income_stmt
        if all(v is None for v in data["annual_eps_yoy_5y"]):
            data["annual_eps_yoy_5y"] = extract_annual_yoy(adf, DILUTED_EPS_LABELS, years_back=5)
            if all(v is None for v in data["annual_eps_yoy_5y"]):
                data["annual_eps_yoy_5y"] = extract_annual_yoy(adf, BASIC_EPS_LABELS, years_back=5)
        if all(v is None for v in data["annual_revenue_yoy_5y"]):
            data["annual_revenue_yoy_5y"] = extract_annual_yoy(adf, REVENUE_LABELS, years_back=5)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: annual fetch failed: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report_enrich.py -v
```

Expected: all green (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add report/enrich.py tests/test_report_enrich.py
git commit -m "feat(enrich): EDGAR-first hybrid for US fundamentals"
```

---

## Task 11: Update renderer for 5y / 4q schema

**Files:**
- Modify: `report/renderer.py`
- Modify: `tests/test_report_renderer.py`

- [ ] **Step 1: Update `tests/test_report_renderer.py` fixtures**

Find every occurrence of `annual_*_yoy_3y` (length 3) and `quarterly_*_yoy_2q` (length 2) and rename to `_5y` / `_4q` with longer arrays. Specifically the fixture block at lines 28-29:

```python
        "annual_eps_yoy_5y": [10.0, 14.0, 18.0, 25.0, 30.0],
        "annual_revenue_yoy_5y": [12.0, 18.0, 20.0, 25.0, 28.0],
        "quarterly_eps_yoy_4q": [22.0, 24.0, 28.0, 31.0],
        "quarterly_eps_yoy_4q_labels": ["Jun'24", "Sep'24", "Dec'24", "Mar'25"],
        "quarterly_revenue_yoy_4q": [10.0, 13.0, 16.0, 18.0],
        "quarterly_revenue_yoy_4q_labels": ["Jun'24", "Sep'24", "Dec'24", "Mar'25"],
```

Run grep to find any other occurrences in the test file:

```bash
grep -n "yoy_3y\|yoy_2q" tests/test_report_renderer.py
```

Update each.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_report_renderer.py -v
```

Expected: failures referencing missing fields / wrong lengths.

- [ ] **Step 3: Modify `report/renderer.py`**

In `_render_quarterly_trend` (lines ~637-666), update field reads and the docstring + section title:

```python
def _render_quarterly_trend(data: dict[str, Any]) -> str:
    """Past 4 quarters of YoY growth — shows whether quarterly EPS / Revenue
    YoY is accelerating, decelerating, or rolling over. EDGAR provides up to
    8 quarterly periods (= 4 YoY pairs); yfinance fallback fills less."""
    eps = data.get("quarterly_eps_yoy_4q") or [None] * 4
    rev = data.get("quarterly_revenue_yoy_4q") or [None] * 4
    eps_lbl = data.get("quarterly_eps_yoy_4q_labels") or [""] * 4
    rev_lbl = data.get("quarterly_revenue_yoy_4q_labels") or [""] * 4
    labels = []
    for i, l in enumerate(eps_lbl):
        if l:
            labels.append(l)
        elif rev_lbl[i]:
            labels.append(rev_lbl[i])
        else:
            n = len(eps_lbl) - 1 - i
            labels.append("Latest" if n == 0 else f"−{n}Q")
    return (
        f'<section class="annual">'
        f'<div class="annual-title">Past 4 Quarters — YoY Growth</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">EPS YoY</div>'
        f'{_line_chart_svg(eps, labels)}</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">Rev. YoY</div>'
        f'{_line_chart_svg(rev, labels)}</div>'
        f"</section>"
    )
```

In `_render_annual_yoy` (lines ~669-683):

```python
def _render_annual_yoy(data: dict[str, Any]) -> str:
    eps = data.get("annual_eps_yoy_5y") or [None] * 5
    rev = data.get("annual_revenue_yoy_5y") or [None] * 5
    labels = ["FY-5", "FY-4", "FY-3", "FY-2", "FY-1"]
    return (
        f'<section class="annual">'
        f'<div class="annual-title">5-Year Annual Earnings Increases (YoY)</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">EPS YoY</div>'
        f'{_line_chart_svg(eps, labels)}</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">Rev. YoY</div>'
        f'{_line_chart_svg(rev, labels)}</div>'
        f"</section>"
    )
```

In `_render_md_ticker` (lines ~908-915):

```python
    eps_5y = d.get("annual_eps_yoy_5y") or [None] * 5
    rev_5y = d.get("annual_revenue_yoy_5y") or [None] * 5
    annual = (
        "| Year | FY−5 | FY−4 | FY−3 | FY−2 | FY−1 |\n"
        "|---|---|---|---|---|---|\n"
        f"| EPS YoY | {_fmt_pct(eps_5y[0])} | {_fmt_pct(eps_5y[1])} | {_fmt_pct(eps_5y[2])} | {_fmt_pct(eps_5y[3])} | {_fmt_pct(eps_5y[4])} |\n"
        f"| Rev. YoY | {_fmt_pct(rev_5y[0])} | {_fmt_pct(rev_5y[1])} | {_fmt_pct(rev_5y[2])} | {_fmt_pct(rev_5y[3])} | {_fmt_pct(rev_5y[4])} |\n\n"
    )
```

Update the module docstring (line 6):

```python
Per-ticker block has a numbered header, a snapshot strip, prominent latest-Q
earnings, mini SVG line charts for 5-year EPS / Revenue YoY + 4-quarter
trajectory, and the LLM-written Chinese prose. Self-contained: all CSS +
SVG inline, no external assets.
```

In `_line_chart_svg` (line 426-428), the comment about "3 dots" is now stale — update:

```python
    # Compact horizontal footprint — sized to read 5 dots (annual) cleanly;
    # the 4-quarter trajectory chart shares the same canvas.
    width, height = 320, 96
```

The `_line_chart_svg` function itself iterates the input array and is bar-count agnostic — no logic changes needed.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_report_renderer.py -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add report/renderer.py tests/test_report_renderer.py
git commit -m "refactor(renderer): widen charts to 5-year annual + 4-quarter trajectory"
```

---

## Task 12: Update `report/__main__.py` empty-data template + analyst test

**Files:**
- Modify: `report/__main__.py`
- Modify: `tests/test_report_main.py`
- Modify: `tests/test_report_analyst.py`

- [ ] **Step 1: Inspect `tests/test_report_main.py` for affected assertions**

```bash
grep -n "yoy_3y\|yoy_2q" tests/test_report_main.py
```

For each match, update to the new key + length (5 or 4 element lists, all `None`).

- [ ] **Step 2: Update `_EMPTY_DATA_TEMPLATE` in `report/__main__.py`**

Replace lines 104-120 with:

```python
_EMPTY_DATA_TEMPLATE: dict = {
    "ticker": None, "group": None, "exchange": None, "company_name": None,
    "sector": None, "industry": None,
    "market_cap": None, "last_price": None, "prev_close": None, "gap_pct": None,
    "institutional_holdings_pct": None,
    "eps_latest_q": None, "eps_latest_q_yoy_pct": None,
    "revenue_latest_q": None, "revenue_latest_q_yoy_pct": None,
    "annual_eps_yoy_5y": [None, None, None, None, None],
    "annual_revenue_yoy_5y": [None, None, None, None, None],
    "quarterly_eps_yoy_4q": [None, None, None, None],
    "quarterly_eps_yoy_4q_labels": ["", "", "", ""],
    "quarterly_revenue_yoy_4q": [None, None, None, None],
    "quarterly_revenue_yoy_4q_labels": ["", "", "", ""],
    "latest_earnings_date": None, "rs_percentile": None,
    "yahoo_revenue_growth_yoy_pct": None,
    "yahoo_earnings_growth_yoy_pct": None,
}
```

- [ ] **Step 3: Update `tests/test_report_analyst.py` fixture (lines 28-29)**

```python
        "annual_eps_yoy_5y": [10.0, 12.0, 14.29, 16.67, 16.67],
        "annual_revenue_yoy_5y": [10.0, 12.0, 14.29, 16.67, 16.67],
        "quarterly_eps_yoy_4q": [10.0, 12.0, 14.0, 16.0],
        "quarterly_eps_yoy_4q_labels": ["Jun'24", "Sep'24", "Dec'24", "Mar'25"],
        "quarterly_revenue_yoy_4q": [10.0, 12.0, 14.0, 16.0],
        "quarterly_revenue_yoy_4q_labels": ["Jun'24", "Sep'24", "Dec'24", "Mar'25"],
```

(Also remove any leftover `_3y` / `_2q` keys from that dict — search-replace the file.)

- [ ] **Step 4: Run all report tests**

```bash
uv run pytest tests/test_report_main.py tests/test_report_analyst.py -v
```

Expected: green.

- [ ] **Step 5: Run the full test suite to verify nothing else broke**

```bash
uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add report/__main__.py tests/test_report_main.py tests/test_report_analyst.py
git commit -m "refactor(report): _EMPTY_DATA_TEMPLATE + tests for _5y/_4q"
```

---

## Task 13: Update prompt and CLAUDE.md

**Files:**
- Modify: `prompts/canslim_system.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `prompts/canslim_system.md`**

Replace line 5 (`Revenue, 3-year annual YoY chart`) with:

```
Revenue, 5-year annual YoY chart, 4-quarter trajectory chart) is rendered separately by the report
```

Replace line 29 (`3-year YoY arrays`) with:

```
   Revenue, 5-year annual YoY arrays, 4-quarter trajectory, recommendation_mean if present, etc.) to
```

Replace line 41 (`3 年年度 YoY + 最新季度趋势`) with:

```
5 年年度 YoY + 近 4 季度趋势:加速 / 减速 / 转折?是否盈利?利润率方向?
```

- [ ] **Step 2: Update `CLAUDE.md` "Daily CANSLIM Report" entry**

Find the paragraph starting `**Daily CANSLIM Report** (`--mode report --market {us,hk}`...` and change the closing sentence (currently `Per-ticker structured data ... comes from yfinance; ...`) to:

```
Per-ticker structured data (Market Cap, EPS, Revenue, **5-year annual YoY**, **4-quarter YoY trajectory**, PE, ROE, RS percentile, latest earnings date) comes from SEC EDGAR for US fundamentals (`report/edgar.py`, 7-day TTL local cache, automatic per-ticker fallback to yfinance income statement on EDGAR miss) plus yfinance for snapshot fields (company name / sector / industry / market cap / price / institutional holdings / earnings date) and for HK fundamentals; qualitative analysis (competitive moat, government/policy support, new products, catalysts, risks, bottom-line) comes from the model with up to 2 web_search calls.
```

- [ ] **Step 3: Sanity-check that no `_3y` / `_2q` references remain**

```bash
grep -rn "yoy_3y\|yoy_2q\|3-year YoY\|3年年度\|Past 2 Quarters" \
  report/ prompts/ CLAUDE.md tests/ 2>/dev/null
```

Expected: only `extract_annual_yoy_3y` (the back-compat shim in `enrich.py` and its test) remain. No prose / template references.

- [ ] **Step 4: Commit**

```bash
git add prompts/canslim_system.md CLAUDE.md
git commit -m "docs: update prompt and CLAUDE.md for EDGAR + 5y/4q schema"
```

---

## Task 14: End-to-end smoke run + manual review

**Files:** none modified

- [ ] **Step 1: Confirm SEC user-agent contract**

EDGAR enforces a User-Agent header containing an email. Confirm `report/edgar.py` has the literal:

```bash
grep "USER_AGENT = " report/edgar.py
```

Expected output: `USER_AGENT = "finviz-to-tv xuelong0208@gmail.com"`

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 3: Smoke test EDGAR client directly**

```bash
uv run python -c "
import logging
logging.basicConfig(level=logging.INFO)
from report.edgar import fetch_edgar_fundamentals
import json
print(json.dumps(fetch_edgar_fundamentals('AAPL'), indent=2, default=str))
"
```

Expected: dict with `annual_revenue_yoy_5y` containing 5 floats (not all None), `quarterly_revenue_yoy_4q` containing 4 floats (not all None), `eps_latest_q` non-None.

If anything is None / missing, inspect the cached `output/state/edgar_cache/CIK0000320193.json` and confirm the concept fallback logic is reaching `Revenues`.

- [ ] **Step 4: Smoke test the alt-revenue concept path**

```bash
uv run python -c "
from report.edgar import fetch_edgar_fundamentals
import json
print(json.dumps(fetch_edgar_fundamentals('V'), indent=2, default=str))
"
```

Expected: non-None revenue values (proves `RevenueFromContractWithCustomerExcludingAssessedTax` fallback).

- [ ] **Step 5: Smoke test ADR fallback**

```bash
uv run python -c "
from report.edgar import fetch_edgar_fundamentals
print(fetch_edgar_fundamentals('BABA'))
"
```

Expected: `None` (BABA files 20-F, not 10-K — confirms the EDGAR-miss path triggers cleanly so yfinance fallback can take over in `enrich`).

- [ ] **Step 6: Run the report end-to-end against a recent date**

Pick a date that has US `.txt` files in `output/TV/US/`:

```bash
ls output/TV/US/*.txt | head -5
```

Then run (using one of those dates, here 2026-05-07 from the recent untracked files at repo root):

```bash
uv run python -m report --market us --date 2026-05-07
```

`__main__.py` calls `load_dotenv()` at startup, so a `.env` at the repo root with `ANTHROPIC_API_KEY=...` is picked up automatically.

Expected: writes `output/Reports/2026_05_07_us.md` and `output/Reports/2026_05_07_us.html`. Open the HTML in a browser:

```bash
open output/Reports/2026_05_07_us.html
```

Visually verify:
- Annual chart shows 5 dots (FY-5 through FY-1) for at least one ticker
- 4-quarter trajectory chart shows 4 dots
- "5-Year Annual Earnings Increases (YoY)" title is present
- "Past 4 Quarters — YoY Growth" title is present
- No layout regressions (label collision, missing dots, broken table)

- [ ] **Step 7: Diff cached EDGAR JSON sizes**

```bash
ls -lh output/state/edgar_cache/ | head -20
```

Expected: `company_tickers.json` ~1MB, `CIK*.json` files ~100-500KB each. Confirms the cache is being populated.

- [ ] **Step 8: No commit** — this task is verification only.

---

## Self-Review

**Spec coverage:**

- ✅ Hybrid split (EDGAR for A-class, yfinance for B-class) — Task 10 wires this with the US_EXCHANGES gate.
- ✅ 5y annual + 4q quarterly history — Tasks 6, 7, 9, 11, 12, 13 enforce throughout.
- ✅ Triple fallback EDGAR → yfinance → None — Task 10 (per-field guards in `enrich`); placeholder None handling unchanged in `analyst.py`.
- ✅ 7-day TTL cache + 1-day company_tickers — Tasks 1, 3, 4.
- ✅ Always-on, no config toggle — no config changes anywhere in the plan.
- ✅ XBRL concept fallback chains — Task 5 (Revenue + EPS).
- ✅ Q4 derivation with gap handling — Task 7.
- ✅ HTTP retry + User-Agent compliance — Task 2.
- ✅ Cache I/O safety (corrupt-file delete) — Task 2.
- ✅ Renderer skips `None` cleanly — verified in code (already iterates `values`); Task 11 only changes counts/titles.
- ✅ HK schema co-renamed — Tasks 9, 12 update the shared `enrich` template + tests.
- ✅ Tests as fixtures (no live network) — Tasks 1-8 all use `monkeypatch` + JSON fixtures.
- ✅ CLAUDE.md + prompt updates — Task 13.
- ✅ Smoke verification — Task 14.

**Placeholder scan:** No `TBD`, `TODO`, `implement later`, `add appropriate error handling`, or unspecified test bodies. Every step shows the exact code to write or command to run.

**Type / name consistency:**
- `_get_cik`, `_fetch_companyfacts`, `_match_concept_facts`, `_select_annual_facts`, `_select_quarterly_facts`, `_extract_annual_yoy`, `_extract_quarterly_yoy`, `_latest_quarter_with_yoy`, `_compute_yoy`, `_period_label`, `_is_fresh`, `_load_json_cache`, `_save_json_cache`, `_http_get_json`, `_parse_ticker_cik_map`, `_refresh_ticker_cik_map`, `_cached_ticker_map`, `fetch_edgar_fundamentals` — used identically in tests and implementation across Tasks 1-8.
- New field names `annual_eps_yoy_5y`, `annual_revenue_yoy_5y`, `quarterly_eps_yoy_4q[_labels]`, `quarterly_revenue_yoy_4q[_labels]` — appear identically in Tasks 8 (EDGAR output), 9 (enrich template + yfinance fallback), 10 (orchestration), 11 (renderer), 12 (template + tests), 13 (prompt + CLAUDE.md).
- `US_EXCHANGES` constant — defined in Task 10, gated against in `fetch_ticker_data`. HK exchange string is `HKEX` (matches the test in Task 10 step 1).
