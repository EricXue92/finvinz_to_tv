#!/usr/bin/env python3
"""IBD-style Relative Strength Rating fetcher.

Pulls the daily-published RS percentile table from Fred6725/rs-log
(itself the GitHub-Actions output of Fred6725/relative-strength) and
exposes a filter helper for the EOD pipeline.

The .txt files remain the primary artifact — RS is an extra screen on
top of Finviz output. If the CSV cannot be fetched, every filter call
becomes a logged no-op so the pipeline still produces results.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

RS_CSV_URL = "https://raw.githubusercontent.com/Fred6725/rs-log/main/output/rs_stocks.csv"


def fetch_rs_table(
    output_dir: Path, today: str, timeout: float = 30.0
) -> dict[str, int] | None:
    """Download rs_stocks.csv and return {ticker: percentile (0-99)}.

    Same-day re-runs are offline (cached at
    output/state/rs_rating_<today>.csv). Returns None on any failure
    (network, parse, missing columns) so callers can degrade gracefully.
    """
    cache_path = output_dir / "state" / f"rs_rating_{today}.csv"

    csv_text: str | None = None
    if cache_path.exists():
        try:
            csv_text = cache_path.read_text(encoding="utf-8")
            logger.info(f"[RS Rating] Using cached CSV: {cache_path}")
        except OSError as e:
            logger.warning(f"[RS Rating] Cache read failed ({e}); refetching")

    if csv_text is None:
        try:
            req = Request(RS_CSV_URL, headers={"User-Agent": "finviz-to-tv/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                csv_text = resp.read().decode("utf-8")
        except (URLError, TimeoutError, OSError) as e:
            logger.warning(
                f"[RS Rating] Fetch failed ({e}); RS filter will be skipped"
            )
            return None
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(csv_text, encoding="utf-8")
        except OSError as e:
            logger.warning(f"[RS Rating] Cache write failed: {e}")

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        if (
            not reader.fieldnames
            or "Ticker" not in reader.fieldnames
            or "Percentile" not in reader.fieldnames
        ):
            logger.warning(
                f"[RS Rating] Unexpected CSV columns {reader.fieldnames}; "
                "expected Ticker + Percentile"
            )
            return None
        table: dict[str, int] = {}
        for row in reader:
            t = (row.get("Ticker") or "").strip().upper()
            p = (row.get("Percentile") or "").strip()
            if not t or not p:
                continue
            try:
                table[t] = int(float(p))
            except ValueError:
                continue
        if not table:
            logger.warning("[RS Rating] CSV parsed but no rows extracted")
            return None
        logger.info(
            f"[RS Rating] Loaded {len(table)} tickers (IBD percentile 0-99)"
        )
        return table
    except csv.Error as e:
        logger.warning(f"[RS Rating] CSV parse failed: {e}")
        return None


def filter_by_rs(
    tickers: list[str],
    rs_table: dict[str, int] | None,
    min_percentile: int,
    label: str,
) -> list[str]:
    """Drop tickers with RS percentile below `min_percentile`.

    - rs_table is None      → no-op, return input (one-line warning).
    - min_percentile <= 0   → no-op, return input.
    - Ticker not in table   → KEPT (likely a new IPO without 12mo history;
                              dropping silently would surprise the user).
    """
    if not tickers:
        return tickers
    if rs_table is None:
        logger.warning(
            f"{label} RS table unavailable, skipping RS >= {min_percentile} filter"
        )
        return tickers
    if min_percentile <= 0:
        return tickers

    kept: list[str] = []
    dropped = 0
    missing = 0
    for t in tickers:
        rs = rs_table.get(t.upper())
        if rs is None:
            kept.append(t)
            missing += 1
        elif rs >= min_percentile:
            kept.append(t)
        else:
            dropped += 1
    logger.info(
        f"{label} {len(kept)} after RS >= {min_percentile} "
        f"(dropped {dropped}, kept-as-missing {missing})"
    )
    return kept
