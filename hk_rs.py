"""Local IBD-style Relative Strength percentile for the HK universe.

Mirrors rs_rating.py's contract but computes percentiles in-process from
Futu k-line data rather than reading the Fred6725 CSV (which only covers
US tickers). HSI is the benchmark.
"""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)


def _score_from_kline(df: pd.DataFrame) -> tuple[float | None, str]:
    """Compute 0.4*R3 + 0.2*R6 + 0.2*R9 + 0.2*R12 from a k-line DataFrame
    sorted ascending by ``time_key``. Returns ``(score, reason)`` — score is
    None on rejection. Reason is one of:
      ``ok``, ``no_data``, ``short_history`` (< 253 rows = < 12 months),
      ``zero_last``, ``zero_past``.
    """
    if df is None or df.empty:
        return None, "no_data"
    if len(df) < 253:
        return None, "short_history"
    closes = df["close"].astype(float).values
    last = closes[-1]
    if last <= 0:
        return None, "zero_last"

    weights = [(3, 0.4), (6, 0.2), (9, 0.2), (12, 0.2)]
    score = 0.0
    for months, w in weights:
        idx = -1 - months * 21
        if abs(idx) > len(closes):
            return None, "short_history"
        past = closes[idx]
        if past <= 0:
            return None, "zero_past"
        score += w * ((last / past) - 1.0)
    return score, "ok"


def compute_rs_table(
    klines: dict[str, pd.DataFrame],
    hsi_kline: pd.DataFrame,
) -> pd.DataFrame:
    """Return DataFrame indexed by Futu code with column ``rs_percentile``
    (0-99). Tickers without enough history are excluded.

    Logs a per-reason rejection breakdown so the operator can tell whether
    a small RS table is due to (a) Futu not returning enough k-line history
    for less liquid HK names (``short_history``) versus (b) data hygiene
    issues (``zero_last`` / ``zero_past``). The 282-out-of-2400 outcome
    we saw on 2026-05-06 turned out to be ``short_history`` dominated;
    this logging makes the diagnosis cheap on every run.
    """
    hsi_score, hsi_reason = _score_from_kline(hsi_kline)
    if hsi_score is None:
        logger.warning(
            f"[HK RS] HSI score rejected ({hsi_reason}) — falling back to "
            f"absolute scores (effectively un-relativised)."
        )
        hsi_score = 0.0

    scores: dict[str, float] = {}
    reasons: dict[str, int] = {}
    for code, df in klines.items():
        s, reason = _score_from_kline(df)
        reasons[reason] = reasons.get(reason, 0) + 1
        if s is None:
            continue
        scores[code] = s - hsi_score

    logger.info(
        f"[HK RS] computed: {len(scores)}/{len(klines)} klines scored. "
        f"Reason breakdown: {dict(sorted(reasons.items(), key=lambda x: -x[1]))}"
    )

    if not scores:
        return pd.DataFrame(columns=["rs_percentile"])

    series = pd.Series(scores, name="relative_score")
    pct = series.rank(method="average", pct=True) * 99
    return pd.DataFrame({"rs_percentile": pct.round().astype(int)})


def filter_by_rs(
    tickers: list[str],
    table: pd.DataFrame | None,
    threshold: int,
) -> list[str]:
    """Keep tickers with rs_percentile >= threshold. Tickers missing from
    ``table`` are KEPT (passthrough — same policy as rs_rating.py for IPOs
    and recent listings without 12mo history). If ``table`` is None or
    empty, all tickers are returned unchanged (failure passthrough)."""
    if table is None or table.empty or threshold <= 0:
        return list(tickers)
    out = []
    for t in tickers:
        if t not in table.index:
            out.append(t)
            continue
        if int(table.loc[t, "rs_percentile"]) >= threshold:
            out.append(t)
    return out


def cache_path(today: date, output_dir: Path) -> Path:
    return output_dir / "state" / f"hk_rs_rating_{today.isoformat()}.csv"


def save_cache(table: pd.DataFrame, today: date, output_dir: Path) -> None:
    p = cache_path(today, output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(p, index_label="code")


def load_cache(today: date, output_dir: Path) -> pd.DataFrame | None:
    p = cache_path(today, output_dir)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, index_col="code")
    except Exception:
        return None
