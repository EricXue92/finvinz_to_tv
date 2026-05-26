#!/usr/bin/env python3
"""Cloud-side HK RS table compute — runs in GitHub Actions weekday cron.

Fetches the HKEX Main Board universe, downloads 2y of yfinance OHLCV for
each ticker (curl_cffi auto-active), computes IBD-style 12M + 3M RS vs HSI
via hk_rs.compute_rs_table, writes a combined data/hk_rs/<today>.csv, and
prunes files older than 14 days.

Why off-host: the home-IP yfinance fetch of the ~2,400 HK universe gets
throttled (only ~50% coverage on 2026-05-25), so the percentile distribution
was computed over half the universe. GH Actions runners get fresh Azure-pool
IPs per run — same pattern as update_us_rs_3m.yml.

Exits 1 on:
  - Empty HKEX universe (nothing to score)
  - k-line coverage < 50% of universe (Yahoo throttle on the runner — better
    to fail loudly than commit a warped distribution)
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Make repo root importable so we can use `hk_eod` and `hk_rs` as top-level
# modules (mirrors how main.py imports them).
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from hk_eod import (  # noqa: E402
    build_metrics_frame,
    fetch_hk_klines_yf,
    fetch_hkex_equities,
    fetch_hsi_kline_yf,
)
from hk_rs import (  # noqa: E402
    WEIGHTS_3M,
    WEIGHTS_12M,
    compute_rs_table,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("compute_hk_rs_cloud")

_DATA_DIR = _REPO_ROOT / "data" / "hk_rs"
_METRICS_DIR = _REPO_ROOT / "data" / "hk_metrics"
_RETENTION_DAYS = 14
_COVERAGE_THRESHOLD = 0.5


def _today() -> date:
    """Indirection for tests to monkeypatch today's date."""
    return date.today()


def _prune_old_files(data_dir: Path, today: date) -> int:
    """Delete <YYYY-MM-DD>.csv files in data_dir older than _RETENTION_DAYS.

    Returns the count of pruned files. Non-date filenames (README.md,
    .gitkeep) are left untouched.
    """
    if not data_dir.exists():
        return 0
    cutoff = today - timedelta(days=_RETENTION_DAYS)
    pruned = 0
    for p in data_dir.glob("*.csv"):
        try:
            file_date = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue  # non-date filename, skip
        if file_date < cutoff:
            p.unlink()
            pruned += 1
    return pruned


def main() -> int:
    today = _today()
    logger.info(f"[Cloud HK RS] Starting compute for {today.isoformat()}")

    # 1. HKEX Main Board universe (HTTP fetch of the securities list — no Futu).
    universe = fetch_hkex_equities()
    if not universe:
        logger.error("[Cloud HK RS] HKEX universe empty — nothing to score")
        return 1
    logger.info(f"[Cloud HK RS] Universe: {len(universe)} Main Board codes")

    # 2. HSI benchmark.
    hsi_kline = fetch_hsi_kline_yf(period="2y")
    if hsi_kline is None or hsi_kline.empty:
        logger.warning("[Cloud HK RS] HSI fetch failed; scores will be un-relativised")

    # 3. Fetch the full universe (curl_cffi auto-active in CI environment).
    klines = fetch_hk_klines_yf(universe, period="2y")
    if not klines:
        logger.error("[Cloud HK RS] yfinance returned no klines")
        return 1

    # 4. Coverage guard on the k-line fetch — this is the throttle signal.
    #    (We guard on fetch coverage rather than scored-rows like the US script,
    #    because the HK universe legitimately includes many short-history names
    #    that can't be 12M-scored; fetch coverage isolates the throttle.)
    coverage = len(klines) / len(universe)
    if coverage < _COVERAGE_THRESHOLD:
        logger.error(
            f"[Cloud HK RS] k-line coverage {len(klines)}/{len(universe)} "
            f"({coverage:.1%}) below {_COVERAGE_THRESHOLD:.0%} threshold — failing"
        )
        return 1

    # 5. Compute both tables off the same k-line batch + HSI fetch.
    hsi_for_compute = hsi_kline if hsi_kline is not None else pd.DataFrame({"time_key": [], "close": []})
    table_12m = compute_rs_table(klines, hsi_for_compute, weights=WEIGHTS_12M, label="12M")
    table_3m = compute_rs_table(klines, hsi_for_compute, weights=WEIGHTS_3M, label="3M")

    # 6. Combine into one CSV (union of indexes; NaN where a column doesn't
    #    score a given ticker — e.g. 3M-only names without 12mo history).
    combined = pd.DataFrame({
        "rs_percentile_12m": table_12m["rs_percentile"] if not table_12m.empty else pd.Series(dtype="float64"),
        "rs_percentile_3m": table_3m["rs_percentile"] if not table_3m.empty else pd.Series(dtype="float64"),
    })
    combined.index.name = "code"

    # 7. Write today's CSV.
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _DATA_DIR / f"{today.isoformat()}.csv"
    combined.to_csv(out_path, index_label="code")
    logger.info(
        f"[Cloud HK RS] Wrote {len(combined)} rows "
        f"(12M {len(table_12m)}, 3M {len(table_3m)}, "
        f"k-line coverage {coverage:.1%}) → {out_path}"
    )

    # 7b. Publish the k-line-derived metrics frame off the SAME klines.
    #     market_cap needs Futu (absent in CI) → dropped, filled locally.
    #     above_sma50/200 dropped (bool↔CSV fragility) → recomputed locally.
    metrics = build_metrics_frame(klines, market_caps={}).drop(
        columns=["market_cap", "above_sma50", "above_sma200"],
        errors="ignore",
    )
    _METRICS_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path = _METRICS_DIR / f"{today.isoformat()}.csv"
    metrics.to_csv(metrics_path, index_label="code")
    logger.info(f"[Cloud HK RS] Wrote {len(metrics)} metrics rows → {metrics_path}")

    # 8. Prune old files in both published dirs.
    pruned = _prune_old_files(_DATA_DIR, today) + _prune_old_files(_METRICS_DIR, today)
    logger.info(f"[Cloud HK RS] Pruned {pruned} files older than {_RETENTION_DAYS} days")

    return 0


if __name__ == "__main__":
    sys.exit(main())
