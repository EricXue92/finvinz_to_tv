"""Hong Kong Main Board EOD pipeline. Owns the full HK scan: universe fetch,
Futu data, metrics, strategy filters, dedup, write."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from futu_sync import get_market_caps_futu

logger = logging.getLogger(__name__)

HKEX_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/"
    "ListOfSecurities.xlsx"
)


def fetch_hkex_equities() -> list[str]:
    """Download the HKEX securities xlsx, parse with openpyxl, return Main
    Board equity stock codes as 5-digit strings (e.g., '00700')."""
    from openpyxl import load_workbook

    req = Request(HKEX_SECURITIES_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as resp:
        data = resp.read()
    wb = load_workbook(BytesIO(data), data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)

    # Skip 2 leading metadata rows; row 3 has the header.
    next(rows, None)
    next(rows, None)
    header = next(rows, None)
    if header is None:
        return []
    code_idx = next(
        (i for i, h in enumerate(header) if h and "stock code" in str(h).lower()),
        0,
    )
    sub_idx = next(
        (i for i, h in enumerate(header) if h and "sub-category" in str(h).lower()),
        None,
    )

    codes: list[str] = []
    for row in rows:
        if not row or row[code_idx] is None:
            continue
        if sub_idx is not None:
            sub = row[sub_idx]
            if sub != "Equity Securities (Main Board)":
                continue
        raw = str(row[code_idx]).strip()
        if not raw.isdigit():
            continue
        codes.append(raw.zfill(5)[1:])  # 5-digit HKEX code → 4-digit yfinance/TV form ('00700' → '0700')
    return codes


def filter_hk_shorts(
    config: dict, futu_cfg: dict | None = None
) -> tuple[int, list[str]]:
    """Run HK shorts pipeline: fetch HKEX universe, download data via yfinance,
    apply SMA20/volume/cap/dollar-volume/performance/up-days filters.
    Returns (universe_size, filtered_tickers_in_tv_format)."""
    from main import (
        _yf_download_with_retry,
        _get_market_cap,
        _get_closes_volumes,
        _get_ohlc,
        _trim_today,
    )

    logger.info("[HK Shorts] Fetching HKEX equity universe...")
    codes = fetch_hkex_equities()
    logger.info(f"  Found {len(codes)} Main Board equities")

    yf_tickers = [code + ".HK" for code in codes]

    now_hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    market_open = 9 <= now_hk.hour < 16 and now_hk.weekday() < 5
    today = now_hk.date()
    if market_open:
        logger.info("  HK market still open, excluding today's incomplete data")

    min_avg_volume = config.get("min_avg_volume", 1_000_000)

    # Phase 1: Download in batches, apply SMA20 +20%, SMA50, and volume filter.
    # Period bumped to 3mo so SMA50 has >= 50 trading days; downstream filters
    # only use up to 22 days so the wider window is harmless.
    # Store per-ticker data to avoid re-downloading later
    logger.info("[HK Shorts] Downloading price data and filtering (this may take several minutes)...")
    batch_size = 500
    phase1 = []
    ticker_closes: dict[str, object] = {}
    ticker_volumes: dict[str, object] = {}
    ticker_highs: dict[str, object] = {}
    ticker_lows: dict[str, object] = {}
    for start in range(0, len(yf_tickers), batch_size):
        batch = yf_tickers[start : start + batch_size]
        logger.info(f"  Batch {start // batch_size + 1}/{(len(yf_tickers) - 1) // batch_size + 1} ({len(batch)} tickers)...")
        batch_data = _yf_download_with_retry(
            batch, period="3mo", progress=False, group_by="ticker", threads=True,
        )
        if batch_data is None or batch_data.empty:
            logger.warning(f"  Batch failed after retries, skipping")
            continue

        single = len(batch) == 1
        for ticker in batch:
            try:
                closes, volumes = _get_closes_volumes(batch_data, ticker, single)
                closes = _trim_today(closes, market_open, today)
                volumes = _trim_today(volumes, market_open, today)

                if len(closes) < 50 or len(volumes) < 20:
                    continue

                last = closes.iloc[-1]
                sma20 = closes.iloc[-20:].mean()
                sma50 = closes.iloc[-50:].mean()
                if (
                    last > sma20 * 1.2
                    and last > sma50
                    and volumes.iloc[-20:].mean() >= min_avg_volume
                ):
                    phase1.append(ticker)
                    ticker_closes[ticker] = closes
                    ticker_volumes[ticker] = volumes
                    highs, lows, _ = _get_ohlc(batch_data, ticker, single)
                    ticker_highs[ticker] = _trim_today(highs, market_open, today)
                    ticker_lows[ticker] = _trim_today(lows, market_open, today)
            except (KeyError, TypeError):
                continue

        if start + batch_size < len(yf_tickers):
            time.sleep(5)

    logger.info(f"  {len(phase1)} after SMA20 +20%, SMA50, and volume filter")
    if not phase1:
        return len(codes), []

    # Phase 2: Market cap. Prefer Futu snapshot (one batch call for all
    # phase1 tickers, real-time `total_market_val` in HKD) over a per-ticker
    # yfinance loop with 0.5s sleep between calls. Fall back to yfinance
    # when OpenD is unreachable or the snapshot call errors.
    min_market_cap = config.get("min_market_cap", 2_000_000_000)
    market_caps: dict[str, float] = {}
    futu_caps = None
    if futu_cfg and futu_cfg.get("enabled"):
        futu_caps = get_market_caps_futu(
            phase1, market="HK",
            host=futu_cfg.get("host", "127.0.0.1"),
            port=futu_cfg.get("port", 11111),
        )
    if futu_caps is not None:
        market_caps = futu_caps
        phase2 = [t for t in phase1 if market_caps.get(t, 0) >= min_market_cap]
        logger.info(
            f"  {len(phase2)} after market cap filter "
            f"(Futu, >= {min_market_cap:,.0f} HKD)"
        )
    else:
        phase2 = []
        for ticker in phase1:
            cap = _get_market_cap(ticker)
            if cap and cap >= min_market_cap:
                phase2.append(ticker)
                market_caps[ticker] = cap
            time.sleep(0.5)
        logger.info(
            f"  {len(phase2)} after market cap filter "
            f"(yfinance, >= {min_market_cap:,.0f} HKD)"
        )
    if not phase2:
        return len(codes), []

    # Phase 3: Dollar volume (price * 20-day avg volume)
    min_dv = config.get("min_dollar_volume", 100_000_000)
    phase3 = []
    for ticker in phase2:
        try:
            closes = ticker_closes[ticker]
            volumes = ticker_volumes[ticker]
            if closes.iloc[-1] * volumes.iloc[-20:].mean() >= min_dv:
                phase3.append(ticker)
        except (KeyError, TypeError):
            continue

    logger.info(f"  {len(phase3)} after dollar volume filter (>= {min_dv:,.0f} HKD)")
    if not phase3:
        return len(codes), []

    # Phase 4: Cap-conditional performance over 2, 3, 4 week windows
    large_cap_thr = config.get("large_cap_threshold", 80_000_000_000)
    mid_cap_thr = config.get("mid_cap_threshold", 16_000_000_000)
    perf_large = config.get("perf_large_cap", 50)
    perf_mid = config.get("perf_mid_cap", 200)
    perf_small = config.get("perf_small_cap", 300)
    perf_weeks = [2, 3, 4]  # trading days: 10, 15, 22

    phase4: set[str] = set()
    for weeks in perf_weeks:
        trading_days = weeks * 5 + (2 if weeks == 4 else 0)  # 10, 15, 22
        week_hits = 0
        for ticker in phase3:
            if ticker in phase4:
                continue
            try:
                closes = ticker_closes[ticker]
                if len(closes) < trading_days + 1:
                    continue
                perf = (closes.iloc[-1] - closes.iloc[-trading_days]) / closes.iloc[-trading_days] * 100
                cap = market_caps[ticker]
                if cap >= large_cap_thr:
                    threshold = perf_large
                elif cap >= mid_cap_thr:
                    threshold = perf_mid
                else:
                    threshold = perf_small
                if perf >= threshold:
                    phase4.add(ticker)
                    week_hits += 1
            except (KeyError, TypeError, ZeroDivisionError):
                continue
        logger.info(f"  {weeks}-week window: {week_hits} new hits")

    logger.info(f"  {len(phase4)} after performance filter (2/3/4 week combined)")
    if not phase4:
        return len(codes), []

    # Phase 4b: ADR% filter
    min_adr = config.get("min_adr_percent", 0)
    adr_days = config.get("adr_days", 20)
    if min_adr > 0:
        adr_passed: set[str] = set()
        for ticker in phase4:
            try:
                highs = ticker_highs[ticker]
                lows = ticker_lows[ticker]
                closes = ticker_closes[ticker]
                n = min(len(highs), len(lows), len(closes), adr_days)
                if n < adr_days:
                    continue
                h = highs.iloc[-adr_days:].values
                l = lows.iloc[-adr_days:].values
                c = closes.iloc[-adr_days:].values
                adr_pct = float(((h - l) / c).mean()) * 100
                if adr_pct >= min_adr:
                    adr_passed.add(ticker)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
        phase4 = adr_passed
        logger.info(f"  {len(phase4)} after ADR% filter (>= {min_adr}%, {adr_days}d)")
        if not phase4:
            return len(codes), []

    # Phase 5: Consecutive up days
    min_up_days = config.get("min_consecutive_up_days", 3)
    phase5 = []
    for ticker in phase4:
        try:
            closes = ticker_closes[ticker]
            if len(closes) < 2:
                continue
            consecutive = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes.iloc[i] > closes.iloc[i - 1]:
                    consecutive += 1
                else:
                    break
            if consecutive >= min_up_days:
                phase5.append(ticker)
        except (KeyError, TypeError):
            continue

    logger.info(f"  {len(phase5)} after consecutive up days filter (>= {min_up_days})")

    # Convert to TradingView format: 0700.HK → HKEX:0700
    tv_tickers = ["HKEX:" + t.replace(".HK", "") for t in phase5]
    return len(codes), tv_tickers


def fetch_hk_klines(
    codes: list[str],
    days: int = 260,
    host: str = "127.0.0.1",
    port: int = 11111,
) -> dict[str, pd.DataFrame] | None:
    """Pull daily OHLCV k-line for a list of HK Futu codes (e.g., 'HK.00700').
    Returns ``{code: DataFrame[time_key, open, close, high, low, volume]}``.
    Returns ``None`` if OpenD is unreachable or the futu SDK is unavailable.
    Tickers that error out individually are skipped silently.
    """
    if not codes:
        return {}
    try:
        from futu import OpenQuoteContext, RET_OK, KLType
    except ImportError:
        logger.warning("[HK] fetch_hk_klines: futu-api not installed")
        return None

    from futu_sync import _opend_reachable
    if not _opend_reachable(host, port):
        logger.warning(
            f"[HK] fetch_hk_klines: OpenD not reachable at {host}:{port}"
        )
        return None

    end = date.today()
    # 260 trading days ≈ 380 calendar days, with margin
    start = end - timedelta(days=int(days * 1.5) + 30)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    result: dict[str, pd.DataFrame] = {}
    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        for i, code in enumerate(codes):
            if i and i % 50 == 0:
                logger.info(f"[HK] k-line: {i}/{len(codes)}")
            try:
                ret, df, _ = ctx.request_history_kline(
                    code, start=start_s, end=end_s,
                    ktype=KLType.K_DAY, max_count=1000,
                )
                if ret != RET_OK or df is None or df.empty:
                    continue
                # Keep only the columns we need; sort ascending by date.
                cols = ["time_key", "open", "high", "low", "close", "volume"]
                df = df[cols].copy()
                df["time_key"] = pd.to_datetime(df["time_key"])
                df = df.sort_values("time_key").reset_index(drop=True)
                result[code] = df
            except Exception:
                continue
        return result
    except Exception as e:
        logger.warning(f"[HK] fetch_hk_klines: unexpected error — {e}")
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def build_metrics_frame(
    klines: dict[str, pd.DataFrame],
    market_caps: dict[str, float],
) -> pd.DataFrame:
    """Reduce a {code: kline_df} dict + caps to a metrics DataFrame indexed by
    code. Tickers without enough history for a given metric get NaN/False.

    Columns:
      market_cap, last_price, prev_close, gap_pct, rvol, avg_vol_20d,
      avg_dollar_vol_20d, adr_pct, sma50, sma200, above_sma50, above_sma200,
      perf_4w, perf_13w, perf_26w, perf_ytd, perf_52w, consecutive_up_days
    """
    rows: list[dict] = []
    today_year = pd.Timestamp.today().year
    for code, df in klines.items():
        if df is None or df.empty or len(df) < 2:
            continue
        closes = df["close"].astype(float).values
        volumes = df["volume"].astype(float).values
        highs = df["high"].astype(float).values
        lows = df["low"].astype(float).values
        times = df["time_key"]
        n = len(closes)

        last = float(closes[-1])
        prev = float(closes[-2])
        gap = (last - prev) / prev * 100.0 if prev > 0 else float("nan")
        avg_vol_20 = float(volumes[-20:].mean()) if n >= 20 else float("nan")
        rvol = (
            float(volumes[-1] / volumes[-21:-1].mean())
            if n >= 21 and volumes[-21:-1].mean() > 0
            else float("nan")
        )
        avg_dv_20 = last * avg_vol_20 if n >= 20 else float("nan")

        if n >= 20:
            adr = float(((highs[-20:] - lows[-20:]) / closes[-20:]).mean()) * 100
        else:
            adr = float("nan")

        sma50 = float(closes[-50:].mean()) if n >= 50 else float("nan")
        sma200 = float(closes[-200:].mean()) if n >= 200 else float("nan")
        above_sma50 = bool(n >= 50 and last > sma50)
        above_sma200 = bool(n >= 200 and last > sma200)

        def _perf(days: int) -> float:
            if n <= days:
                return float("nan")
            past = closes[-days - 1]
            return (last - past) / past * 100.0 if past > 0 else float("nan")

        perf_4w = _perf(20)
        perf_13w = _perf(65)
        perf_26w = _perf(130)
        perf_52w = _perf(252) if n > 252 else float("nan")

        # YTD: find earliest close in the current year
        ytd_mask = times.dt.year == today_year
        if ytd_mask.any():
            first_ytd = float(closes[ytd_mask.values][0])
            perf_ytd = (last - first_ytd) / first_ytd * 100.0 if first_ytd > 0 else float("nan")
        else:
            perf_ytd = float("nan")

        # Consecutive up days from the tail
        cu = 0
        for i in range(n - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                cu += 1
            else:
                break

        rows.append({
            "code": code,
            "market_cap": market_caps.get(code, float("nan")),
            "last_price": last,
            "prev_close": prev,
            "gap_pct": gap,
            "rvol": rvol,
            "avg_vol_20d": avg_vol_20,
            "avg_dollar_vol_20d": avg_dv_20,
            "adr_pct": adr,
            "sma50": sma50,
            "sma200": sma200,
            "above_sma50": above_sma50,
            "above_sma200": above_sma200,
            "perf_4w": perf_4w,
            "perf_13w": perf_13w,
            "perf_26w": perf_26w,
            "perf_ytd": perf_ytd,
            "perf_52w": perf_52w,
            "consecutive_up_days": cu,
        })
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows).set_index("code")
    # Keep above_* as Python bool singletons (not numpy.bool_) so that
    # ``row["above_sma50"] is False`` works correctly in callers/tests.
    for col in ("above_sma50", "above_sma200"):
        if col in result.columns:
            result[col] = result[col].astype(object)
    return result


HK_STRATEGY_PRIORITY = ["EarningsGap", "HighVolume", "GapUp", "Leaders", "RS"]


def apply_strategy_filters(
    metrics: pd.DataFrame,
    settings: dict,
    longs_cfg: list[dict],
    leaders_cfg: list[dict],
    rs_enabled: bool,
) -> dict[str, list[str]]:
    """Apply every strategy gate against the metrics frame and return a dict
    of {strategy_name: [code, ...]}. Codes are still in Futu format
    (``HK.00700``); caller converts to TradingView ``HKEX:00700`` later.

    Strategies returned: EarningsGap, HighVolume, GapUp, Leaders, RS
    (the last is always returned but empty when rs_enabled is False).
    """
    if metrics.empty:
        return {s: [] for s in HK_STRATEGY_PRIORITY}

    cap = settings.get("min_market_cap", 300_000_000)
    dvol = settings.get("min_dollar_volume", 100_000_000)
    avg_vol = settings.get("min_avg_volume", 500_000)
    adr = settings.get("min_adr_percent", 4.0)
    price = settings.get("min_price", 20.0)

    # Universal long-side baseline
    base = (
        (metrics["market_cap"] >= cap)
        & (metrics["avg_vol_20d"] >= avg_vol)
        & (metrics["avg_dollar_vol_20d"] >= dvol)
        & (metrics["adr_pct"] >= adr)
        & (metrics["last_price"] >= price)
    )

    # Per-strategy parameter lookup
    by_key = {item.get("key"): item for item in longs_cfg}
    eg = by_key.get("earnings_gap", {})
    hv = by_key.get("high_volume", {})
    gu = by_key.get("gap_up", {})

    eg_min_rvol = float(eg.get("min_relative_volume", 3))
    eg_min_gap = float(eg.get("min_gap_percent", 3.0))
    hv_min_rvol = float(hv.get("min_relative_volume", 3))
    gu_min_gap = float(gu.get("min_gap_percent", 5.0))

    earnings_gap_mask = base & (metrics["gap_pct"] >= eg_min_gap) & (metrics["rvol"] >= eg_min_rvol)
    high_volume_mask = base & (metrics["rvol"] >= hv_min_rvol)
    gap_up_mask = base & (metrics["gap_pct"] >= gu_min_gap)

    # Leaders: baseline + above SMA50 & SMA200 + any of the perf windows
    perf_any = (
        (metrics["perf_4w"] >= _leader_threshold(leaders_cfg, "min_perf_4w"))
        | (metrics["perf_13w"] >= _leader_threshold(leaders_cfg, "min_perf_13w"))
        | (metrics["perf_26w"] >= _leader_threshold(leaders_cfg, "min_perf_26w"))
        | (metrics["perf_ytd"] >= _leader_threshold(leaders_cfg, "min_perf_ytd"))
        | (metrics["perf_52w"] >= _leader_threshold(leaders_cfg, "min_perf_52w"))
    ).fillna(False)

    leaders_mask = (
        base
        & metrics["above_sma50"].astype(bool)
        & metrics["above_sma200"].astype(bool)
        & perf_any
    )

    # RS group: baseline + above-SMA50/200, no perf window. Always computed,
    # caller decides whether to actually emit it based on HSI trigger.
    rs_mask = base & metrics["above_sma50"].astype(bool) & metrics["above_sma200"].astype(bool)

    return {
        "EarningsGap": metrics.index[earnings_gap_mask].tolist(),
        "HighVolume": metrics.index[high_volume_mask].tolist(),
        "GapUp": metrics.index[gap_up_mask].tolist(),
        "Leaders": metrics.index[leaders_mask].tolist(),
        "RS": metrics.index[rs_mask].tolist() if rs_enabled else [],
    }


def _leader_threshold(leaders_cfg: list[dict], key: str) -> float:
    """Find the threshold for a given perf window across the [[hk_leaders]]
    list. Returns +inf if no matching entry (so the OR short-circuits)."""
    for item in leaders_cfg:
        if key in item:
            return float(item[key])
    return float("inf")


def dedup_by_priority(
    raw: dict[str, list[str]],
    priority: list[str] | None = None,
) -> dict[str, list[str]]:
    """Given {strategy: [code,...]}, walk in priority order and drop codes
    from later strategies that already appeared in an earlier one. Default
    priority: EarningsGap > HighVolume > GapUp > Leaders > RS."""
    order = priority or HK_STRATEGY_PRIORITY
    seen: set[str] = set()
    out: dict[str, list[str]] = {}
    for name in order:
        codes = [c for c in raw.get(name, []) if c not in seen]
        out[name] = codes
        seen.update(codes)
    return out


def hsi_day_change_pct(
    host: str = "127.0.0.1", port: int = 11111
) -> float | None:
    """Return today's HSI day change in percent, derived from
    ``(last_price - prev_close_price) / prev_close_price * 100``. Uses
    Futu code ``HK.800000`` for HSI. Returns ``None`` on any failure
    (caller should treat None as 'trigger condition not met')."""
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError:
        return None

    from futu_sync import _opend_reachable
    if not _opend_reachable(host, port):
        return None

    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        ret, data = ctx.get_market_snapshot(["HK.800000"])
        if ret != RET_OK or data is None or data.empty:
            return None
        row = data.iloc[0]
        last = float(row.get("last_price") or 0)
        prev = float(row.get("prev_close_price") or 0)
        if prev <= 0:
            return None
        return (last - prev) / prev * 100.0
    except Exception:
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
