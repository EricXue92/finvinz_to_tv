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
