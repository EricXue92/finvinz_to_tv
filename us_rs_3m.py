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


def fetch_us_klines_yf(
    tickers: list[str],
    period: str = "6mo",
    batch_size: int = 500,
) -> dict[str, pd.DataFrame]:
    """Batch-download daily closes for US tickers via yfinance.

    Returns ``{ticker: DataFrame[time_key, close]}``. Tickers that fail the
    batch retry or come back as all-NaN are silently dropped (callers treat
    them as "not in 3M table" via the kept-as-missing policy).

    Mirrors hk_eod.fetch_hk_klines_yf structure: 500-ticker batches with
    threads=True, no inter-batch sleep (yfinance handles rate-limits via
    its own backoff).
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
            continue
        for t in batch:
            try:
                if len(batch) == 1:
                    closes = batch_data["Close"].dropna()
                else:
                    closes = batch_data[t]["Close"].dropna()
            except (KeyError, AttributeError):
                continue
            if closes.empty:
                continue
            result[t] = pd.DataFrame({
                "time_key": closes.index,
                "close": closes.values,
            })
    return result
