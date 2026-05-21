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


# Weight tuples: (months, weight). months 决定回看偏移 (months * 21 个交易日)；
# weight 按 IBD 风格压在最短窗口上，重叠窗口产生隐式时间衰减。
WEIGHTS_12M: list[tuple[int, float]] = [(3, 0.4), (6, 0.2), (9, 0.2), (12, 0.2)]
WEIGHTS_3M:  list[tuple[int, float]] = [(1, 0.5), (2, 0.3), (3, 0.2)]


def _score_from_kline(
    df: pd.DataFrame,
    weights: list[tuple[int, float]] = WEIGHTS_12M,
) -> tuple[float | None, str]:
    """Compute Σ wᵢ·Rᵢ from a k-line DataFrame sorted ascending by ``time_key``.
    Returns ``(score, reason)`` — score is None on rejection. Reason is one of:
      ``ok``, ``no_data``, ``short_history`` (insufficient rows for max lookback),
      ``zero_last``, ``zero_past``.

    Minimum rows = ``max(months for months, _ in weights) * 21 + 1``.
    """
    if df is None or df.empty:
        return None, "no_data"
    max_months = max(m for m, _ in weights)
    min_rows = max_months * 21 + 1
    if len(df) < min_rows:
        return None, "short_history"
    closes = df["close"].astype(float).values
    last = closes[-1]
    if last <= 0:
        return None, "zero_last"

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
    weights: list[tuple[int, float]] = WEIGHTS_12M,
    label: str = "12M",
) -> pd.DataFrame:
    """Return DataFrame indexed by Futu code with column ``rs_percentile``
    (0-99). Tickers without enough history are excluded.

    ``weights`` selects the weight tuple (WEIGHTS_12M or WEIGHTS_3M). ``label``
    is purely cosmetic — it's spliced into the rejection-breakdown log line so
    a single run computing both 12M and 3M tables produces distinguishable
    output.

    Logs a per-reason rejection breakdown so the operator can tell whether
    a small RS table is due to (a) Futu/yfinance not returning enough k-line
    history for less liquid HK names (``short_history``) versus (b) data
    hygiene issues (``zero_last`` / ``zero_past``).
    """
    hsi_score, hsi_reason = _score_from_kline(hsi_kline, weights=weights)
    if hsi_score is None:
        logger.warning(
            f"[HK RS {label}] HSI score rejected ({hsi_reason}) — falling back to "
            f"absolute scores (effectively un-relativised)."
        )
        hsi_score = 0.0

    scores: dict[str, float] = {}
    reasons: dict[str, int] = {}
    for code, df in klines.items():
        s, reason = _score_from_kline(df, weights=weights)
        reasons[reason] = reasons.get(reason, 0) + 1
        if s is None:
            continue
        scores[code] = s - hsi_score

    logger.info(
        f"[HK RS {label}] computed: {len(scores)}/{len(klines)} klines scored. "
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


def cache_path(today: date, output_dir: Path, suffix: str = "") -> Path:
    """Default suffix '' → hk_rs_rating_<date>.csv (12M, legacy path).
    suffix='3m' → hk_rs_rating_3m_<date>.csv."""
    prefix = f"{suffix}_" if suffix else ""
    return output_dir / "state" / f"hk_rs_rating_{prefix}{today.isoformat()}.csv"


def save_cache(
    table: pd.DataFrame, today: date, output_dir: Path, suffix: str = ""
) -> None:
    p = cache_path(today, output_dir, suffix=suffix)
    p.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(p, index_label="code")


def load_cache(
    today: date, output_dir: Path, suffix: str = ""
) -> pd.DataFrame | None:
    p = cache_path(today, output_dir, suffix=suffix)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, index_col="code")
    except Exception:
        return None
