"""Local IBD-style Relative Strength percentile for the US universe (3-month).

Complements `rs_rating.py` (Fred6725-CSV-based 12-month RS) with a locally
computed short-window layer benchmarked against SPY. Universe is the same
~6100 tickers as `rs_rating.py` (Fred6725 CSV); SPY k-line comes from yfinance.

The 3M table is consumed twice:
  1. Long-side 3M gate on Leaders / conditional RS / Shorts
  2. US IPO ladder's 3M RS filter (via raw_score column for out-of-universe
     percentile lookup)
"""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

WEIGHTS_3M: list[tuple[int, float]] = [(1, 0.5), (2, 0.3), (3, 0.2)]


def _score_from_kline(
    df: pd.DataFrame,
    weights: list[tuple[int, float]] = WEIGHTS_3M,
) -> tuple[float | None, str]:
    """Compute Σ wᵢ·Rᵢ from a k-line DataFrame sorted ascending by ``time_key``.

    Returns (score, reason); score is None on rejection. Reason ∈
    {"ok", "no_data", "short_history", "zero_last", "zero_past"}.

    Minimum rows = max(months) * 21 + 1 (64 for WEIGHTS_3M).
    """
    if df is None or df.empty:
        return None, "no_data"
    max_months = max(m for m, _ in weights)
    min_rows = max_months * 21 + 1
    if len(df) < min_rows:
        return None, "short_history"
    closes = df["close"].astype(float).values
    last = float(closes[-1])
    if last <= 0:
        return None, "zero_last"

    score = 0.0
    for months, w in weights:
        idx = -1 - months * 21
        if abs(idx) > len(closes):
            return None, "short_history"
        past = float(closes[idx])
        if past <= 0:
            return None, "zero_past"
        score += w * ((last / past) - 1.0)
    return score, "ok"


def compute_us_rs_3m_table(
    klines: dict[str, pd.DataFrame],
    spy_kline: pd.DataFrame,
) -> pd.DataFrame:
    """Return DataFrame indexed by ticker with columns ``raw_score`` and
    ``rs_percentile`` (0-99).

    Tickers without enough history (< 64 rows) are excluded. ``raw_score``
    is retained alongside the percentile so the US IPO ladder can score
    out-of-universe candidates against the same Fred6725 distribution.
    """
    spy_score, spy_reason = _score_from_kline(spy_kline)
    if spy_score is None:
        logger.warning(
            f"[US RS 3M] SPY score rejected ({spy_reason}) — falling back to "
            f"absolute scores (effectively un-relativised)."
        )
        spy_score = 0.0

    scores: dict[str, float] = {}
    reasons: dict[str, int] = {}
    for ticker, df in klines.items():
        s, reason = _score_from_kline(df)
        reasons[reason] = reasons.get(reason, 0) + 1
        if s is None:
            continue
        scores[ticker] = s - spy_score

    logger.info(
        f"[US RS 3M] computed: {len(scores)}/{len(klines)} klines scored. "
        f"Reason breakdown: {dict(sorted(reasons.items(), key=lambda x: -x[1]))}"
    )

    if not scores:
        return pd.DataFrame(columns=["raw_score", "rs_percentile"])

    series = pd.Series(scores)  # column name comes from the dict key below
    pct = series.rank(method="average", pct=True) * 99
    return pd.DataFrame({
        "raw_score": series,
        "rs_percentile": pct.round().astype(int),
    })


def filter_by_rs(
    tickers: list[str],
    table: pd.DataFrame | None,
    threshold: int,
) -> list[str]:
    """Keep tickers with rs_percentile >= threshold.

    Missing-from-table → KEPT (US passthrough policy, mirrors rs_rating.py
    and hk_rs.filter_by_rs). Threshold ≤ 0 → passthrough.
    """
    if table is None or table.empty or threshold <= 0:
        return list(tickers)
    out: list[str] = []
    for t in tickers:
        if t not in table.index:
            out.append(t)
            continue
        if int(table.loc[t, "rs_percentile"]) >= threshold:
            out.append(t)
    return out


def cache_path(today: date, output_dir: Path) -> Path:
    return output_dir / "state" / f"rs_rating_3m_{today.isoformat()}.csv"


def save_cache(table: pd.DataFrame, today: date, output_dir: Path) -> None:
    p = cache_path(today, output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(p, index_label="ticker")


def load_cache(today: date, output_dir: Path) -> pd.DataFrame | None:
    p = cache_path(today, output_dir)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, index_col="ticker")
    except Exception:
        return None


def _yf_download_with_retry(tickers, **kwargs):
    """Indirection layer so tests can monkeypatch this attribute.

    At runtime, lazily import the helper from main.py (avoids circular import:
    main.py imports from us_rs_3m).
    """
    from main import _yf_download_with_retry as _impl
    return _impl(tickers, **kwargs)


def _retry_sparse_in_batch(data, tickers: list[str], period: str, min_rows: int) -> bool:
    """Indirection layer so tests can monkeypatch this attribute.

    At runtime, lazily import the helper from main.py (avoids circular import:
    main.py imports from us_rs_3m). Returns True when the batch looked
    rate-limited (caller should back off longer).
    """
    from main import _retry_sparse_in_batch as _impl
    return _impl(data, tickers, period=period, min_rows=min_rows)


def fetch_us_klines_yf(
    tickers: list[str],
    period: str = "6mo",
    batch_size: int = 100,
    include_ohlcv: bool = False,
) -> dict[str, pd.DataFrame]:
    """Batch-download daily closes (or full OHLCV) for US tickers via yfinance.

    Returns ``{ticker: DataFrame[time_key, close]}`` when ``include_ohlcv``
    is False (default), or ``{ticker: DataFrame[time_key, open, high, low,
    close, volume]}`` when True.

    Tickers that fail the batch retry or come back as all-NaN are silently
    dropped (callers treat them as "not in 3M table" via the kept-as-missing
    policy).

    100-ticker batches with threads=True and a 5s inter-batch pause.
    Batch size matches Fred6725/relative-strength's reliability sweet spot
    (their README: "100 is a sweet spot — speed vs reliability"); we
    previously used 500 like hk_eod, but at US scale (~6000 tickers across
    60 batches with 100, vs 12 with 500) the smaller batches reduce the
    blast radius when Yahoo throttles a single response and let curl_cffi's
    browser-impersonation breathe between calls.

    Defensive layers (kept as belt-and-braces even with curl_cffi):
    - When a batch returns fully empty after retries → 30s throttle cooldown
    - When a batch returns ≥50% sparse → also 30s cooldown (signal from
      _retry_sparse_in_batch); 5s spacing is not enough for Yahoo's window
    """
    if not tickers:
        return {}

    result: dict[str, pd.DataFrame] = {}
    n_batches = (len(tickers) - 1) // batch_size + 1
    for bidx, start in enumerate(range(0, len(tickers), batch_size), start=1):
        batch = tickers[start:start + batch_size]
        logger.info(f"[US RS 3M] yfinance batch {bidx}/{n_batches} ({len(batch)} tickers)...")
        batch_data = _yf_download_with_retry(
            batch, period=period, progress=False, group_by="ticker", threads=True,
        )
        if batch_data is None or batch_data.empty:
            logger.warning(f"[US RS 3M]   batch failed; skipping {len(batch)} tickers")
            if bidx < n_batches:
                logger.info("[US RS 3M]   throttle cooldown 30s before next batch...")
                time.sleep(30)
            continue
        # min_rows=20 mirrors hk_eod.fetch_hk_klines_yf — this is a "batch
        # quality floor" (retry tickers whose batch result is essentially
        # empty, < 20 of ~126 trading days for period="6mo"), NOT the 3M
        # algorithm's 64-row scoring minimum. min_rows=64 here was a 2026-
        # 05-21 mis-tune that flagged ~90% of tickers (including AAPL/MSFT)
        # as sparse on routine yfinance NaN noise, sending the retry phase
        # into a 5+ hour spiral. _score_from_kline enforces the real 64-row
        # cut at scoring time.
        throttled = _retry_sparse_in_batch(batch_data, batch, period=period, min_rows=20)
        for t in batch:
            try:
                closes = batch_data[t]["Close"].dropna()
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if closes.empty:
                continue
            if include_ohlcv:
                try:
                    row = {
                        "time_key": closes.index,
                        "open": batch_data[t]["Open"].reindex(closes.index).values,
                        "high": batch_data[t]["High"].reindex(closes.index).values,
                        "low": batch_data[t]["Low"].reindex(closes.index).values,
                        "close": closes.values,
                        "volume": batch_data[t]["Volume"].reindex(closes.index).values,
                    }
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
                result[t] = pd.DataFrame(row)
            else:
                result[t] = pd.DataFrame({
                    "time_key": closes.index,
                    "close": closes.values,
                })
        # Cooldown selection: if the batch came back ≥50% sparse (rate-limit
        # signal from _retry_sparse_in_batch), back off the full 30s instead
        # of the normal 5s — Yahoo's throttle window is ≥30s and 5s spacing
        # just cascades sparse-batches one after another (2026-05-21 20:55:
        # batches 3-10 all 371-499 sparse with only 5s between).
        if bidx < n_batches:
            if throttled:
                logger.info("[US RS 3M]   throttle cooldown 30s (high-sparse batch)...")
                time.sleep(30)
            else:
                time.sleep(5)
    return result


def fetch_universe_from_rs_csv(rs_table_12m: dict[str, int] | None) -> list[str]:
    """Return the ticker list from the Fred6725 12M table.

    We piggyback on the existing 12M fetch (already done at top of EOD)
    instead of re-downloading the CSV. ``rs_table_12m`` is the dict
    returned by ``rs_rating.fetch_rs_table``.
    """
    if not rs_table_12m:
        return []
    return sorted(rs_table_12m.keys())


def _fetch_spy_kline(period: str = "6mo") -> pd.DataFrame | None:
    """Fetch SPY closes via the same retrying yfinance helper as the universe.
    Returns a DataFrame with time_key + close, or None on failure.
    """
    klines = fetch_us_klines_yf(["SPY"], period=period, batch_size=1)
    return klines.get("SPY")


def build_3m_table(
    output_dir: Path,
    today: date,
    rs_table_12m: dict[str, int] | None = None,
) -> pd.DataFrame | None:
    """Orchestrator: load cache or (universe → fetch → compute → save).

    Returns None when both cache and fetch fail. Universe is derived from
    the Fred6725 12M table (must be passed in by the EOD pipeline).
    """
    cached = load_cache(today, output_dir)
    if cached is not None and not cached.empty:
        logger.info(f"[US RS 3M] Using cached table: {len(cached)} tickers")
        return cached

    tickers = fetch_universe_from_rs_csv(rs_table_12m)
    if not tickers:
        logger.warning("[US RS 3M] No universe (Fred6725 12M table empty/None); skipping 3M build")
        return None

    # Fetch SPY FIRST, before the universe loop consumes any of Yahoo's
    # rate-limit budget. If SPY falls into the throttle tail (as on
    # 2026-05-21), the whole table degrades to "absolute scores
    # (un-relativised)" — every ticker's percentile gets warped, not just
    # the ones we couldn't fetch.
    spy = _fetch_spy_kline(period="6mo")
    if spy is None:
        logger.warning("[US RS 3M] SPY fetch failed; computing absolute scores (un-relativised)")
        spy = pd.DataFrame({"time_key": [], "close": []})

    klines = fetch_us_klines_yf(tickers, period="6mo")
    if not klines:
        logger.warning("[US RS 3M] yfinance batch returned no data; 3M layer will passthrough")
        return None

    table = compute_us_rs_3m_table(klines, spy)
    if table.empty:
        logger.warning("[US RS 3M] No tickers scored; 3M layer will passthrough")
        return None

    # Refuse to cache a severely partial table — percentile ranks in a 1300
    # ticker subset vs. the 6000-ticker universe are not interchangeable
    # (a 90th-percentile name in the partial may be 70th in the full set).
    # Returning None forces the next rerun to retry rather than freezing
    # the day at a warped distribution.
    coverage = len(table) / len(tickers) if tickers else 0
    if coverage < 0.5:
        logger.warning(
            f"[US RS 3M] Coverage {len(table)}/{len(tickers)} ({coverage:.1%}) "
            f"below 50% — refusing to cache warped table; 3M layer will "
            f"passthrough until a healthier rerun succeeds"
        )
        return None

    save_cache(table, today, output_dir)
    logger.info(f"[US RS 3M] Built {len(table)} ticker table → cache")
    return table
