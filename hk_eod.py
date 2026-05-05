"""Hong Kong Main Board EOD pipeline. Owns the full HK scan: universe fetch,
Futu data, metrics, strategy filters, dedup, write."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from io import BytesIO
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

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
