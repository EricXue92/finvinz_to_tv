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
from datetime import date as _date
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


# --- Quarterly fact selection + YoY extraction --------------------------------

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
