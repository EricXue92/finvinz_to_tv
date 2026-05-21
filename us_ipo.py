"""US IPO candidate filter — mirrors HK's filter_hk_ipo_candidates.

IPO candidates = tickers that passed a Finviz long-side screener but were
dropped by yfinance for missing/insufficient daily history. They go through
a conditional ladder based on data depth so a 30-day-old IPO can still surface
while a 200-day-old one is held to nearly the full long-side baseline.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _build_ipo_metrics(
    klines: dict[str, pd.DataFrame],
    finviz_caps: dict[str, float],
) -> pd.DataFrame:
    """Compute IPO-ladder metrics frame from k-lines.

    Columns: market_cap, last_price, avg_vol_20d, avg_dollar_vol_20d,
    adr_pct, sma50, sma200, above_sma50, above_sma200, n_rows.

    Tickers shorter than the relevant window get NaN for that window's
    metrics (matching hk_eod.build_metrics_frame semantics). Caller checks
    NaN via pd.notna in the ladder.
    """
    rows: list[dict] = []
    for t, df in klines.items():
        if df is None or df.empty or len(df) < 2:
            continue
        closes = df["close"].astype(float).values
        highs = df["high"].astype(float).values if "high" in df.columns else closes
        lows = df["low"].astype(float).values if "low" in df.columns else closes
        volumes = (
            df["volume"].astype(float).values if "volume" in df.columns
            else np.full(len(df), float("nan"))
        )
        n = len(closes)
        last = float(closes[-1])

        avg_vol_20 = float(volumes[-20:].mean()) if n >= 20 else float("nan")
        avg_dv_20 = last * avg_vol_20 if n >= 20 else float("nan")
        if n >= 20:
            adr = float(((highs[-20:] - lows[-20:]) / closes[-20:]).mean()) * 100
        else:
            adr = float("nan")
        sma50 = float(closes[-50:].mean()) if n >= 50 else float("nan")
        sma200 = float(closes[-200:].mean()) if n >= 200 else float("nan")
        above_sma50 = bool(n >= 50 and last > sma50)
        above_sma200 = bool(n >= 200 and last > sma200)

        rows.append({
            "ticker": t,
            "market_cap": finviz_caps.get(t, float("nan")),
            "last_price": last,
            "avg_vol_20d": avg_vol_20,
            "avg_dollar_vol_20d": avg_dv_20,
            "adr_pct": adr,
            "sma50": sma50,
            "sma200": sma200,
            "above_sma50": above_sma50,
            "above_sma200": above_sma200,
            "n_rows": n,
        })

    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).set_index("ticker")
    # Use object dtype to preserve Python bool identity (is False / is True tests).
    for col in ("above_sma50", "above_sma200"):
        if col in result.columns:
            result[col] = result[col].map(bool).astype(object)
    return result
