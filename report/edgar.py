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

import httpx

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


# --- Ticker → CIK lookup ----------------------------------------------------

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


# --- companyfacts fetch -----------------------------------------------------

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


# --- XBRL concept constants -------------------------------------------------

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


# --- Annual fact selection + YoY extraction ----------------------------------

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
