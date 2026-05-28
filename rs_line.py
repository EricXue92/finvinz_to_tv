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
DEFAULT_LOOKBACK = 5
DEFAULT_TOLERANCE = 0.005
DEFAULT_ADR_MULT = 1.5
DEFAULT_ADR_DAYS = 20

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


def compute_rs_direction(
    klines: dict[str, pd.DataFrame],
    benchmark_kline: pd.DataFrame | None,
    ma_length: int = DEFAULT_MA_LENGTH,
    ma_type: str = DEFAULT_MA_TYPE,
    lookback: int = DEFAULT_LOOKBACK,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> pd.DataFrame:
    """Per-id RS-line 21EMA direction, indexed by the ``klines`` dict key.

    rs_line = close / benchmark_close (date-aligned inner join); ma = EMA/SMA.
    Columns:
      rs_ema         float  latest MA value of the RS line (descriptive only;
                            scale-dependent — NOT comparable to a TV chart level)
      rs_ema_chg_5d  float  (ma[-1] - ma[-1-lookback]) / ma[-1-lookback]
    Ids with < ``min_history`` MA-valid bars are EXCLUDED (unknown). Scale-
    invariant: scaling the benchmark by a constant leaves rs_ema_chg_5d
    unchanged. Never raises (ids with too few bars to index back are skipped).
    """
    cols = ["rs_ema", "rs_ema_chg_5d"]
    if benchmark_kline is None or getattr(benchmark_kline, "empty", True):
        return pd.DataFrame(columns=cols)
    bench = (
        benchmark_kline[["time_key", "close"]]
        .rename(columns={"close": "_bench"})
        .dropna()
    )

    rows: dict[str, tuple[float, float]] = {}
    for tid, df in klines.items():
        if df is None or df.empty or "close" not in df or "time_key" not in df:
            continue
        m = (
            df[["time_key", "close"]]
            .dropna()
            .merge(bench, on="time_key", how="inner")
            .sort_values("time_key")
        )
        rs = m["close"].astype(float) / m["_bench"].astype(float)
        ma = _moving_average(rs, ma_length, ma_type)
        ma = ma[ma.notna()]
        if len(ma) < max(min_history, lookback + 1):  # need lookback+1 bars to index back
            continue
        ema_now = float(ma.iloc[-1])
        ema_prior = float(ma.iloc[-1 - lookback])
        rows[tid] = (round(ema_now, 6), round((ema_now - ema_prior) / ema_prior, 6))

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame.from_dict(rows, orient="index", columns=cols)


def compute_rs_reversal(
    klines: dict[str, pd.DataFrame],
    lookback: int = DEFAULT_LOOKBACK,
    adr_days: int = DEFAULT_ADR_DAYS,
) -> pd.DataFrame:
    """Per-id recent-strength metric for the reversal exemption, indexed by the
    klines dict key. Needs OHLC (high/low/close). Columns:
      adr_pct      float  ADR% = mean(last adr_days of (high-low)/close) * 100
      ret_lb       float  lookback-bar price return, as a fraction
      ret_per_adr  float  how many ADRs the price moved over the window
                          (= ret_lb*100 / adr_pct)
    Ids lacking high/low/close columns, with < max(adr_days, lookback+1) bars,
    or with non-positive ADR are EXCLUDED. Never raises.
    """
    cols = ["adr_pct", "ret_lb", "ret_per_adr"]
    rows: dict[str, tuple[float, float, float]] = {}
    for tid, df in klines.items():
        if df is None or getattr(df, "empty", True):
            continue
        if not {"time_key", "high", "low", "close"}.issubset(df.columns):
            continue
        d = df.dropna(subset=["high", "low", "close"]).sort_values("time_key")
        if len(d) < max(adr_days, lookback + 1):
            continue
        close = d["close"].astype(float)
        adr_pct = float(((d["high"].astype(float) - d["low"].astype(float)) / close)
                        .tail(adr_days).mean() * 100)
        if adr_pct <= 0:
            continue
        ret_lb = float((close.iloc[-1] - close.iloc[-1 - lookback]) / close.iloc[-1 - lookback])
        rows[tid] = (round(adr_pct, 4), round(ret_lb, 6), round(ret_lb * 100 / adr_pct, 4))
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame.from_dict(rows, orient="index", columns=cols)


def adr_mult_from_config(config: dict) -> float:
    """Reversal-exemption multiplier (in ADR units) from [rs_line]. Tunable:
    lower in a bull regime (more reversals real), higher in a correction."""
    cfg = config.get("rs_line", {}) or {}
    return float(cfg.get("adr_mult", DEFAULT_ADR_MULT))


def params_from_config(config: dict) -> dict:
    """``compute_rs_line_features`` kwargs from a parsed config's ``[rs_line]``
    section (v1 position signal). Missing keys fall back to module defaults.
    Keys here MUST match that function's signature — it is splatted directly."""
    cfg = config.get("rs_line", {}) or {}
    return {
        "ma_length": int(cfg.get("ma_length", DEFAULT_MA_LENGTH)),
        "ma_type": str(cfg.get("ma_type", DEFAULT_MA_TYPE)),
        "persistence_window": int(cfg.get("persistence_window", DEFAULT_PERSISTENCE_WINDOW)),
        "min_history": int(cfg.get("min_history", DEFAULT_MIN_HISTORY)),
    }


def direction_params_from_config(config: dict) -> dict:
    """``compute_rs_direction`` kwargs from ``[rs_line]`` (direction signal).
    Separate from ``params_from_config`` because the two compute functions take
    different kwargs — splatting a superset into either would raise TypeError."""
    cfg = config.get("rs_line", {}) or {}
    return {
        "ma_length": int(cfg.get("ma_length", DEFAULT_MA_LENGTH)),
        "ma_type": str(cfg.get("ma_type", DEFAULT_MA_TYPE)),
        "lookback": int(cfg.get("lookback", DEFAULT_LOOKBACK)),
        "min_history": int(cfg.get("min_history", DEFAULT_MIN_HISTORY)),
    }


def tolerance_from_config(config: dict) -> float:
    """The RS-direction cut band (fraction, e.g. 0.005 = 0.5%) from ``[rs_line]``.
    Not a compute kwarg — used by the audit/gate to flag weak direction."""
    cfg = config.get("rs_line", {}) or {}
    return float(cfg.get("tolerance", DEFAULT_TOLERANCE))


def is_enabled(config: dict) -> bool:
    return bool((config.get("rs_line", {}) or {}).get("enabled", True))


def summarize_rs_line(ids: list[str], features: pd.DataFrame | None) -> str | None:
    """One-line RS-line summary for a list of ids against a features frame.
    Returns None when no usable feature data exists (annotation skipped).
    Ids missing from the frame are treated as 'unknown' and omitted from counts.
    """
    if features is None or features.empty or "rs_below_ma" not in features.columns:
        return None
    above = 0
    below: list[tuple[str, int, float]] = []
    for i in ids:
        if i not in features.index:
            continue
        val = features.loc[i, "rs_below_ma"]
        if pd.isna(val):
            continue
        if int(val) == 1:
            d = features.loc[i, "rs_days_below_ma"]
            f = features.loc[i, "rs_frac_below_ma"]
            below.append((i, int(d) if pd.notna(d) else 0, float(f) if pd.notna(f) else 0.0))
        else:
            above += 1
    if above == 0 and not below:
        return None
    detail = ", ".join(f"{i}({d}d,{f:.0%})" for i, d, f in sorted(below, key=lambda x: -x[1]))
    tail = f" — below: {detail}" if below else ""
    return f"RS-line: {above} above MA, {len(below)} below{tail}"
