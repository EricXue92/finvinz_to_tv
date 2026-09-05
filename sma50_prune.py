"""Daily SMA50 prune of the US cross-day master (``eod_seen_US.txt``).

Runs at the top of the us-eod pipeline (before the master is loaded): any
ticker whose close has been below its SMA50 for ``consecutive_days`` completed
trading days in a row AND whose latest close is below the prior day's close
(still declining, not rebounding under the line) is removed from the master,
so it can re-qualify on a future EOD run once it recovers. The master is
backed up first (``eod_seen_US.txt.bak.<stamp>``, same scheme as rs-line-audit).

Soft-fail by design: a total yfinance failure skips the prune with a warning;
tickers with missing data or fewer than ``sma_period`` bars are KEPT.
US only — the HK master is untouched.
"""

import logging

from pathlib import Path

import pandas as pd

from rs_line_audit import _prune_master

logger = logging.getLogger("momentum_scanner")

_LABEL = "sma50-prune"


def _fetch_daily_closes(tickers: list[str]) -> dict[str, pd.Series] | None:
    """Batch-download daily closes for the master's tickers.

    Returns {ticker: close series} (absent/empty tickers omitted), or None when
    the batch download failed entirely. Monkeypatched in tests.
    """
    from main import _yf_download_with_retry  # local import: main imports us

    data = _yf_download_with_retry(
        tickers, period="6mo", progress=False, group_by="ticker", threads=False
    )
    if data is None or data.empty:
        return None
    single = len(tickers) == 1
    closes: dict[str, pd.Series] = {}
    for t in tickers:
        try:
            series = (data["Close"] if single else data[t]["Close"]).dropna()
        except (KeyError, IndexError):
            continue
        if not series.empty:
            closes[t] = series
    return closes


def find_sma50_drops(
    closes_by_ticker: dict[str, pd.Series],
    consecutive_days: int = 2,
    sma_period: int = 50,
) -> list[str]:
    """Tickers whose close sat below SMA50 on each of the last N completed days
    AND whose latest close is below the prior day's close (still declining — a
    ticker rebounding under the line is kept).

    The SMA is evaluated per-day (rolling, includes that day's close). Tickers
    with fewer than ``sma_period + consecutive_days - 1`` bars are kept — the
    oldest checked day must have a defined SMA.
    """
    drops = []
    for ticker, closes in closes_by_ticker.items():
        sma = closes.rolling(sma_period).mean()
        tail_close = closes.iloc[-consecutive_days:]
        tail_sma = sma.iloc[-consecutive_days:]
        if len(tail_close) < consecutive_days or tail_sma.isna().any():
            continue  # insufficient history -> keep
        if not (tail_close.values < tail_sma.values).all():
            continue
        if closes.iloc[-1] >= closes.iloc[-2]:
            continue  # flat/rebounding under the line -> keep
        drops.append(ticker)
    return sorted(drops)


def prune_us_master(seen_path: Path, cfg: dict) -> list[str]:
    """Prune SMA50 breakdowns from the US master. Returns the dropped tickers.

    No-op when disabled, when the master is missing/empty, or when the batch
    download fails (warned, master untouched).
    """
    if not cfg.get("enabled", False):
        return []
    if not seen_path.exists():
        logger.info(f"[{_LABEL}] master missing, nothing to prune: {seen_path}")
        return []
    tickers = [ln.strip() for ln in seen_path.read_text().splitlines() if ln.strip()]
    if not tickers:
        logger.info(f"[{_LABEL}] master empty, nothing to prune")
        return []

    consecutive_days = int(cfg.get("consecutive_days", 2))
    closes = _fetch_daily_closes(tickers)
    if closes is None:
        logger.warning(
            f"[{_LABEL}] yfinance batch download failed — skipping prune, "
            "master untouched"
        )
        return []

    drops = find_sma50_drops(closes, consecutive_days=consecutive_days)
    missing = len(tickers) - len(closes)
    logger.info(
        f"[{_LABEL}] checked {len(closes)}/{len(tickers)} tickers "
        f"(missing data: {missing}, kept) | close < SMA50 for "
        f"{consecutive_days} consecutive day(s) + still declining: {len(drops)}"
        + (f" -> {','.join(drops)}" if drops else "")
    )
    _prune_master(seen_path, drops, label=_LABEL)
    return drops
