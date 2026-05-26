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
    metrics = metrics.copy()
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

    try:
        restored = _restore_above_sma(df)
    except Exception as e:
        logger.warning(
            f"[HK metrics] Cloud CSV schema mismatch ({type(e).__name__}: {e}); "
            "falling back to local fetch"
        )
        return None

    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index_label="code")
    logger.info(f"[HK metrics] Fetched cloud CSV: {len(df)} tickers")
    return restored
