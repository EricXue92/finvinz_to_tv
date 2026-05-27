"""RS-line-vs-MA trend features (TraderLion-style RS line position).

The RS line is the price-to-benchmark ratio (close / benchmark_close). This
module reports whether that line sits below its own moving average and how
persistently — the inverse of TraderLion's "RS line overtakes MA" signal.

Pure compute: no network, no I/O. Computed cloud-side (the cloud RS scripts
already fetch the klines) and published as extra CSV columns; the local
pipeline only reads the columns. Only the line's position relative to its OWN
MA is used, so the result is scale-invariant — the benchmark's absolute level
(SPX vs SPY) is irrelevant.
"""

from __future__ import annotations

import pandas as pd

DEFAULT_MA_LENGTH = 21
DEFAULT_MA_TYPE = "ema"
DEFAULT_PERSISTENCE_WINDOW = 20
DEFAULT_MIN_HISTORY = 42

_COLUMNS = ["rs_below_ma", "rs_days_below_ma", "rs_frac_below_ma"]


def _moving_average(s: pd.Series, length: int, ma_type: str) -> pd.Series:
    if ma_type == "sma":
        return s.rolling(length).mean()
    return s.ewm(span=length, adjust=False).mean()


def _trailing_streak(flags: list[bool]) -> int:
    """Count of consecutive True values at the end of the list."""
    n = 0
    for v in reversed(flags):
        if v:
            n += 1
        else:
            break
    return n


def compute_rs_line_features(
    klines: dict[str, pd.DataFrame],
    benchmark_kline: pd.DataFrame | None,
    ma_length: int = DEFAULT_MA_LENGTH,
    ma_type: str = DEFAULT_MA_TYPE,
    persistence_window: int = DEFAULT_PERSISTENCE_WINDOW,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> pd.DataFrame:
    """Per-id RS-line-vs-MA features, indexed by the ``klines`` dict key.

    Each value DataFrame must have ``time_key`` (datetime) + ``close`` columns
    (the shape returned by fetch_us_klines_yf / fetch_hk_klines_yf). Columns:
      rs_below_ma       int   1 if line < MA on the latest aligned bar else 0
      rs_days_below_ma  int   trailing consecutive bars below MA (0 if above)
      rs_frac_below_ma  float fraction of last ``persistence_window`` bars below
    Ids with < ``min_history`` aligned bars are EXCLUDED (can't seed the MA);
    consumers treat missing-from-frame as "unknown". Never raises.
    ``min_history`` is counted against **MA-valid** bars (bars where the MA is
    non-NaN), so SMA callers effectively need ``min_history + (ma_length - 1)``
    aligned bars to retain a ticker.
    """
    if benchmark_kline is None or getattr(benchmark_kline, "empty", True):
        return pd.DataFrame(columns=_COLUMNS)
    bench = (
        benchmark_kline[["time_key", "close"]]
        .rename(columns={"close": "_bench"})
        .dropna()
    )

    rows: dict[str, tuple[int, int, float]] = {}
    for tid, df in klines.items():
        if df is None or df.empty or "close" not in df or "time_key" not in df:
            continue
        m = (
            df[["time_key", "close"]]
            .dropna()
            .merge(bench, on="time_key", how="inner")
            .sort_values("time_key")
        )
        if len(m) < min_history:
            continue
        rs = m["close"].astype(float) / m["_bench"].astype(float)
        ma = _moving_average(rs, ma_length, ma_type)
        below = (rs < ma)[ma.notna()]
        # SMA warm-up leaves the first (ma_length-1) bars NaN; require min_history MA-valid bars.
        if len(below) < min_history:
            continue
        flags = [bool(v) for v in below.tolist()]
        window = flags[-persistence_window:]
        rows[tid] = (
            int(flags[-1]),
            _trailing_streak(flags),
            round(sum(window) / len(window), 3),
        )

    if not rows:
        return pd.DataFrame(columns=_COLUMNS)
    return pd.DataFrame.from_dict(rows, orient="index", columns=_COLUMNS)
