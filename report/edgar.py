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
