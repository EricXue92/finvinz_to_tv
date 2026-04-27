#!/usr/bin/env python3
"""Finviz screener to TradingView watchlist generator."""

import argparse
import logging
import sys
import time
import tomllib
from datetime import date, datetime, timedelta
import io
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import yfinance as yf
from finviz import get_stock
from finviz.screener import Screener
import openpyxl

from futu_sync import sync_to_futu

logger = logging.getLogger(__name__)


def _futu_sync(config: dict, key: str, tickers: list[str], market: str) -> None:
    """Sync to Futu if [futu] is enabled in config — silent no-op otherwise."""
    futu_cfg = config.get("futu") or {}
    if not futu_cfg.get("enabled", False):
        return
    group_name = (futu_cfg.get("groups") or {}).get(key)
    if not group_name:
        return
    sync_to_futu(
        tickers,
        group_name,
        market,  # type: ignore[arg-type]
        host=futu_cfg.get("host", "127.0.0.1"),
        port=futu_cfg.get("port", 11111),
    )

HKEX_SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
)


def fetch_hkex_equities() -> list[str]:
    """Fetch Main Board equity stock codes from the HKEX securities list.
    Returns 4-digit codes like ['0001', '0002', '0700']."""
    req = Request(HKEX_SECURITIES_URL, headers={"User-Agent": "Mozilla/5.0"})
    resp = urlopen(req)
    wb = openpyxl.load_workbook(io.BytesIO(resp.read()))
    ws = wb.active

    codes = []
    for i, row in enumerate(ws.iter_rows(min_row=4, values_only=True)):
        if row[2] == "Equity" and row[3] == "Equity Securities (Main Board)":
            codes.append(row[0][1:])  # "00700" → "0700"

    wb.close()
    return codes


def _yf_download_with_retry(tickers, max_retries=3, **kwargs):
    """Download yfinance data with retries on failure."""
    for attempt in range(max_retries):
        data = yf.download(tickers, **kwargs)
        if data is not None and not data.empty:
            return data
        if attempt < max_retries - 1:
            time.sleep(3)
            logger.warning(f"  yfinance download returned empty, retrying ({attempt + 2}/{max_retries})...")
    return data


def _get_market_cap(ticker: str, max_retries: int = 3) -> float | None:
    """Get market cap with retries."""
    for attempt in range(max_retries):
        try:
            cap = yf.Ticker(ticker).fast_info.market_cap
            if cap:
                return cap
        except Exception:
            pass
        if attempt < max_retries - 1:
            time.sleep(1)
    return None


def _get_closes_volumes(data, ticker: str, single: bool):
    """Extract closes and volumes series for a ticker from downloaded data."""
    if single:
        return data["Close"].dropna(), data["Volume"].dropna()
    return data[ticker]["Close"].dropna(), data[ticker]["Volume"].dropna()


def _trim_today(series, market_open: bool, today_date):
    """Remove today's incomplete data if market is open."""
    if market_open and len(series) > 0 and series.index[-1].date() == today_date:
        return series.iloc[:-1]
    return series


def filter_hk_shorts(config: dict) -> tuple[int, list[str]]:
    """Run HK shorts pipeline: fetch HKEX universe, download data via yfinance,
    apply SMA20/volume/cap/dollar-volume/performance/up-days filters.
    Returns (universe_size, filtered_tickers_in_tv_format)."""
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

    # Phase 1: Download in batches, apply SMA20 +20% and volume filter
    # Store per-ticker data to avoid re-downloading later
    logger.info("[HK Shorts] Downloading price data and filtering (this may take several minutes)...")
    batch_size = 500
    phase1 = []
    ticker_closes: dict[str, object] = {}
    ticker_volumes: dict[str, object] = {}
    for start in range(0, len(yf_tickers), batch_size):
        batch = yf_tickers[start : start + batch_size]
        logger.info(f"  Batch {start // batch_size + 1}/{(len(yf_tickers) - 1) // batch_size + 1} ({len(batch)} tickers)...")
        batch_data = _yf_download_with_retry(
            batch, period="2mo", progress=False, group_by="ticker", threads=True,
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

                if len(closes) < 20 or len(volumes) < 20:
                    continue

                sma20 = closes.iloc[-20:].mean()
                if closes.iloc[-1] > sma20 * 1.2 and volumes.iloc[-20:].mean() >= min_avg_volume:
                    phase1.append(ticker)
                    ticker_closes[ticker] = closes
                    ticker_volumes[ticker] = volumes
            except (KeyError, TypeError):
                continue

        if start + batch_size < len(yf_tickers):
            time.sleep(5)

    logger.info(f"  {len(phase1)} after SMA20 +20% and volume filter")
    if not phase1:
        return len(codes), []

    # Phase 2: Market cap (with retries)
    min_market_cap = config.get("min_market_cap", 2_000_000_000)
    phase2 = []
    market_caps: dict[str, float] = {}
    for ticker in phase1:
        cap = _get_market_cap(ticker)
        if cap and cap >= min_market_cap:
            phase2.append(ticker)
            market_caps[ticker] = cap
        time.sleep(0.5)

    logger.info(f"  {len(phase2)} after market cap filter (>= {min_market_cap:,.0f} HKD)")
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


def load_config(config_path: Path) -> dict:
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def run_screener(filters: list[str], signal: str | None = None) -> list[str]:
    """Run a Finviz screener and return list of tickers."""
    kwargs = {"filters": filters}
    if signal:
        kwargs["signal"] = signal
    stock_list = Screener(**kwargs)
    return [stock["Ticker"] for stock in stock_list.data]


def parse_number(value: str) -> float:
    """Parse a finviz number string like '6.96M', '1.23B', '500K', or '5,366,687'."""
    value = value.strip().replace(",", "")
    suffixes = {"K": 1e3, "M": 1e6, "B": 1e9}
    if value and value[-1] in suffixes:
        return float(value[:-1]) * suffixes[value[-1]]
    return float(value)


def filter_shorts(
    filters: list[str],
    signal: str | None,
    perf_large_cap: float,
    perf_mid_cap: float,
    perf_small_cap: float,
    min_dollar_volume: float,
    min_consecutive_up_days: int,
) -> tuple[int, list[str]]:
    """Run shorts pipeline: finviz Ownership → single yfinance download →
    performance / dollar-volume / consecutive-up-days filters.
    Returns (total_found, filtered_tickers)."""
    kwargs_own = {"filters": filters, "table": "Ownership"}
    if signal:
        kwargs_own["signal"] = signal
    ownership = Screener(**kwargs_own)
    total = len(ownership.data)

    tickers = []
    market_caps: dict[str, float] = {}
    for stock in ownership.data:
        ticker = stock["Ticker"]
        try:
            cap = parse_number(stock["Market Cap"])
            tickers.append(ticker)
            market_caps[ticker] = cap
        except (KeyError, ValueError):
            continue

    if not tickers:
        return total, []

    # Single yfinance download — shared by all three filters
    data = _yf_download_with_retry(
        tickers, period="2mo", progress=False, group_by="ticker", threads=False
    )

    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = 9 <= now_et.hour < 16 and now_et.weekday() < 5
    today_et = now_et.date()
    if market_open:
        logger.info("  US market still open, excluding today's incomplete data")

    single = len(tickers) == 1

    # 1. Performance filter (cap-conditional, 2/3/4-week windows)
    perf_weeks = [2, 3, 4]
    passed: set[str] = set()
    for weeks in perf_weeks:
        trading_days = weeks * 5 + (2 if weeks == 4 else 0)  # 10, 15, 22
        week_hits = 0
        for ticker in tickers:
            if ticker in passed:
                continue
            try:
                if single:
                    closes = data["Close"].dropna()
                else:
                    closes = data[ticker]["Close"].dropna()
                closes = _trim_today(closes, market_open, today_et)

                if len(closes) < trading_days + 1:
                    continue

                perf = (closes.iloc[-1] - closes.iloc[-trading_days]) / closes.iloc[-trading_days] * 100
                cap = market_caps[ticker]

                if cap >= 10e9:
                    threshold = perf_large_cap
                elif cap >= 2e9:
                    threshold = perf_mid_cap
                else:
                    threshold = perf_small_cap

                if perf >= threshold:
                    passed.add(ticker)
                    week_hits += 1
            except (KeyError, ValueError, ZeroDivisionError):
                continue
        logger.info(f"  {weeks}-week window: {week_hits} new hits")

    perf_passed = list(passed)
    logger.info(f"  {len(perf_passed)} after performance filter (2/3/4 week combined)")

    # 2. Dollar volume filter (uses same data)
    if min_dollar_volume > 0 and perf_passed:
        dv_passed = _filter_dollar_volume_from_data(
            perf_passed, data, min_dollar_volume, market_open, today_et, single
        )
        logger.info(f"  {len(dv_passed)} after dollar volume filter (20-day avg)")
    else:
        dv_passed = perf_passed

    # 3. Consecutive up days filter (uses same data)
    if min_consecutive_up_days > 0 and dv_passed:
        final = _filter_consecutive_up_days_from_data(
            dv_passed, data, min_consecutive_up_days, market_open, today_et, single
        )
        logger.info(f"  {len(final)} after consecutive up days filter (>= {min_consecutive_up_days})")
    else:
        final = dv_passed

    return total, final


def run_morning_gap(config: dict) -> tuple[int, list[str]]:
    """Run the intraday morning-gap scan. Determines current scan offset
    from ET time, runs Finviz screener, applies dollar-volume and
    intraday cumulative volume filters. Returns (offset, tickers).
    Returns (-1, []) if outside scan window."""
    scan_offsets = config.get("scan_offsets", [10, 15, 20, 25, 30])
    tolerance = config.get("offset_tolerance_minutes", 2)
    offset = _get_et_scan_offset(scan_offsets, tolerance)
    if offset is None:
        logger.info("[Morning Gap] Not in scan window, exiting")
        return -1, []

    logger.info(f"[Morning Gap] Running for offset +{offset}min")

    # Phase 1: Finviz screener
    tickers = run_screener(config["filters"], config.get("signal"))
    logger.info(f"  Found {len(tickers)} tickers from Finviz screener")
    if not tickers:
        return offset, []

    # Phase 2: 20-day daily data — used by both dollar volume and avg volume
    daily_data = _yf_download_with_retry(
        tickers, period="2mo", interval="1d", progress=False,
        group_by="ticker", threads=False,
    )
    if daily_data is None or daily_data.empty:
        logger.warning("  Daily yfinance download failed, exiting")
        return offset, []

    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = True  # we only run during market hours
    today_et = now_et.date()

    # Phase 3: Dollar volume filter
    min_dv = config.get("min_dollar_volume", 0)
    if min_dv > 0:
        tickers = _filter_dollar_volume_from_data(
            tickers, daily_data, min_dv, market_open, today_et, len(tickers) == 1
        )
        logger.info(f"  {len(tickers)} after dollar volume filter (>= ${min_dv:,.0f})")
    if not tickers:
        return offset, []

    # Phase 4: Compute 20-day avg daily volume per ticker
    avg_days = config.get("avg_volume_days", 20)
    single = len(tickers) == 1
    avg_daily_volumes: dict[str, float] = {}
    for ticker in tickers:
        try:
            if single:
                volumes = daily_data["Volume"].dropna()
            else:
                volumes = daily_data[ticker]["Volume"].dropna()
            volumes = _trim_today(volumes, market_open, today_et)
            if len(volumes) < avg_days:
                continue
            avg_daily_volumes[ticker] = float(volumes.iloc[-avg_days:].mean())
        except (KeyError, TypeError):
            continue

    tickers = [t for t in tickers if t in avg_daily_volumes]
    logger.info(f"  {len(tickers)} have sufficient 20-day daily volume data")
    if not tickers:
        return offset, []

    # Phase 5: Pull intraday 1m data and apply cumulative volume filter
    intraday_data = _yf_download_with_retry(
        tickers, period="1d", interval="1m", progress=False,
        group_by="ticker", threads=False,
    )
    if intraday_data is None or intraday_data.empty:
        logger.warning("  Intraday yfinance download failed, exiting")
        return offset, []

    final = _filter_intraday_cumulative_volume(
        tickers, intraday_data, avg_daily_volumes, offset
    )
    logger.info(f"  {len(final)} after intraday cumulative volume filter (offset={offset}m)")

    return offset, final


def filter_consecutive_up_days(tickers: list[str], min_days: int) -> list[str]:
    """Filter tickers to those with >= min_days consecutive up days.
    Uses yfinance to fetch recent daily close prices."""
    if not tickers:
        return []

    data = yf.download(tickers, period="1mo", progress=False, group_by="ticker")
    result = []

    # If US market is still open, today's data is incomplete — exclude it
    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = 9 <= now_et.hour < 16 and now_et.weekday() < 5
    if market_open:
        logger.info("  US market still open, excluding today's incomplete data")

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                closes = data["Close"].dropna()
            else:
                closes = data[ticker]["Close"].dropna()

            if market_open and len(closes) > 0 and closes.index[-1].date() == now_et.date():
                closes = closes.iloc[:-1]

            if len(closes) < 2:
                logger.warning(f"  yfinance: no data for {ticker}, keeping it")
                result.append(ticker)
                continue

            consecutive = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes.iloc[i] > closes.iloc[i - 1]:
                    consecutive += 1
                else:
                    break

            if consecutive >= min_days:
                result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, keeping it")
            result.append(ticker)

    return result


def _filter_consecutive_up_days_from_data(
    tickers: list[str],
    data,
    min_days: int,
    market_open: bool,
    today_date,
    single: bool,
) -> list[str]:
    """Filter tickers to those with >= min_days consecutive up days,
    using a pre-downloaded yfinance DataFrame.
    Strict: tickers with no data are dropped."""
    if not tickers:
        return []

    result = []
    for ticker in tickers:
        try:
            if single:
                closes = data["Close"].dropna()
            else:
                closes = data[ticker]["Close"].dropna()
            closes = _trim_today(closes, market_open, today_date)

            if len(closes) < 2:
                logger.warning(f"  yfinance: no data for {ticker}, dropping")
                continue

            consecutive = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes.iloc[i] > closes.iloc[i - 1]:
                    consecutive += 1
                else:
                    break

            if consecutive >= min_days:
                result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, dropping")

    return result


def filter_dollar_volume_yf(tickers: list[str], min_dollar_volume: float, days: int = 20) -> list[str]:
    """Filter tickers by dollar volume using yfinance N-day average volume.
    Dollar volume = latest close price * N-day average volume.
    Strict: tickers with missing/insufficient data are dropped — safe_write_watchlist
    guards against catastrophic yfinance failures."""
    if not tickers:
        return []

    data = yf.download(tickers, period="2mo", progress=False, group_by="ticker", threads=False)
    result = []

    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = 9 <= now_et.hour < 16 and now_et.weekday() < 5

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                closes = data["Close"].dropna()
                volumes = data["Volume"].dropna()
            else:
                closes = data[ticker]["Close"].dropna()
                volumes = data[ticker]["Volume"].dropna()

            if market_open:
                if len(closes) > 0 and closes.index[-1].date() == now_et.date():
                    closes = closes.iloc[:-1]
                if len(volumes) > 0 and volumes.index[-1].date() == now_et.date():
                    volumes = volumes.iloc[:-1]

            if len(volumes) < days or len(closes) < 1:
                logger.warning(f"  yfinance: insufficient data for {ticker}, dropping")
                continue

            price = closes.iloc[-1]
            avg_vol = volumes.iloc[-days:].mean()

            if price * avg_vol >= min_dollar_volume:
                result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, dropping")

    return result


def _filter_dollar_volume_from_data(
    tickers: list[str],
    data,
    min_dollar_volume: float,
    market_open: bool,
    today_date,
    single: bool,
    days: int = 20,
) -> list[str]:
    """Filter tickers by dollar volume using a pre-downloaded yfinance DataFrame.
    Dollar volume = latest close price * N-day average volume.
    Strict: tickers with insufficient data are dropped."""
    if not tickers:
        return []

    result = []
    for ticker in tickers:
        try:
            closes, volumes = _get_closes_volumes(data, ticker, single)
            closes = _trim_today(closes, market_open, today_date)
            volumes = _trim_today(volumes, market_open, today_date)

            if len(volumes) < days or len(closes) < 1:
                logger.warning(f"  yfinance: insufficient data for {ticker}, dropping")
                continue

            price = closes.iloc[-1]
            avg_vol = volumes.iloc[-days:].mean()

            if price * avg_vol >= min_dollar_volume:
                result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, dropping")

    return result


def _filter_intraday_cumulative_volume(
    tickers: list[str],
    intraday_data,
    avg_daily_volumes: dict[str, float],
    offset_minutes: int,
) -> list[str]:
    """For each ticker, sum the 1-minute volume bars from 9:30 ET up to
    9:30 + offset_minutes ET (exclusive of the +offset bar) and keep
    tickers whose cumulative volume >= their 20-day average daily volume.
    Strict: tickers with missing data are dropped."""
    if not tickers:
        return []

    et = ZoneInfo("America/New_York")
    today_et = datetime.now(et).date()
    open_ts = datetime.combine(today_et, datetime.min.time(), tzinfo=et).replace(hour=9, minute=30)
    end_ts = open_ts + timedelta(minutes=offset_minutes)

    single = len(tickers) == 1
    result = []
    for ticker in tickers:
        try:
            if single:
                volumes = intraday_data["Volume"].dropna()
            else:
                volumes = intraday_data[ticker]["Volume"].dropna()

            if len(volumes) == 0:
                logger.warning(f"  yfinance 1m: no data for {ticker}, dropping")
                continue

            # yfinance 1m index is tz-aware in ET (or UTC depending on version).
            # Normalize to ET for comparison.
            idx = volumes.index
            if idx.tz is None:
                idx = idx.tz_localize("America/New_York")
            else:
                idx = idx.tz_convert("America/New_York")
            mask = (idx >= open_ts) & (idx < end_ts)
            cumulative = volumes[mask.values].sum()

            avg_daily = avg_daily_volumes.get(ticker)
            if avg_daily is None or avg_daily <= 0:
                continue

            if cumulative >= avg_daily:
                result.append(ticker)
        except (KeyError, TypeError, AttributeError):
            logger.warning(f"  yfinance 1m: failed to process {ticker}, dropping")

    return result


def filter_relative_volume(tickers: list[str], min_rvol: float, days: int = 20) -> list[str]:
    """Filter tickers by relative volume: latest day's volume / N-day average volume >= min_rvol.
    Uses yfinance to fetch daily volume data.
    Strict: tickers with missing/insufficient data are dropped."""
    if not tickers:
        return []

    data = yf.download(tickers, period="2mo", progress=False, group_by="ticker", threads=False)
    result = []

    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = 9 <= now_et.hour < 16 and now_et.weekday() < 5

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                volumes = data["Volume"].dropna()
            else:
                volumes = data[ticker]["Volume"].dropna()

            if market_open and len(volumes) > 0 and volumes.index[-1].date() == now_et.date():
                volumes = volumes.iloc[:-1]

            if len(volumes) < days + 1:
                logger.warning(f"  yfinance: insufficient volume data for {ticker}, dropping")
                continue

            current_vol = volumes.iloc[-1]
            avg_vol = volumes.iloc[-(days + 1):-1].mean()

            if avg_vol > 0:
                rvol = current_vol / avg_vol
                if rvol >= min_rvol:
                    result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, dropping")

    return result


def check_market_down(threshold: float = -1.5) -> bool:
    """Check if both SPY and QQQ are down more than threshold%."""
    spy = get_stock("SPY")
    qqq = get_stock("QQQ")
    spy_change = float(spy["Change"].strip("%"))
    qqq_change = float(qqq["Change"].strip("%"))
    logger.info(f"  SPY: {spy_change:+.2f}%  QQQ: {qqq_change:+.2f}%")
    return spy_change < threshold and qqq_change < threshold


def _get_et_scan_offset(
    scan_offsets: list[int], tolerance_minutes: int
) -> int | None:
    """Determine which scan offset (in minutes after 9:30 ET) the current
    ET time matches, within tolerance. Returns None if outside any window
    or outside trading hours / weekend."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return None
    market_open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes_since_open = (now_et - market_open_et).total_seconds() / 60
    if minutes_since_open < 0 or minutes_since_open > max(scan_offsets) + tolerance_minutes:
        return None
    best = min(scan_offsets, key=lambda o: abs(o - minutes_since_open))
    if abs(best - minutes_since_open) <= tolerance_minutes:
        return best
    return None


def _previous_dated_file(directory: Path, today_prefix: str, suffix: str) -> Path | None:
    """Find the most recent dated file matching `*{suffix}` in `directory`,
    excluding today's. Used as the baseline for safe_write_watchlist's drop guard."""
    today_name = f"{today_prefix}{suffix}"
    candidates = sorted(
        p for p in directory.glob(f"*{suffix}") if p.name != today_name
    )
    return candidates[-1] if candidates else None


def safe_write_watchlist(
    tickers: list[str],
    output_path: Path,
    fmt: str = "comma",
    drop_threshold: float = 0.5,
    baseline_path: Path | None = None,
) -> bool:
    """Write tickers to file. If `baseline_path` (or `output_path` when not given)
    exists and new count drops by more than drop_threshold (e.g. 0.5 = 50%),
    keep the existing file and warn. Returns True if the file was written."""
    compare = baseline_path if baseline_path is not None else output_path
    if compare.exists():
        old_content = compare.read_text().strip()
        old_count = len(old_content.split(",")) if "," in old_content else len(old_content.splitlines())
        if old_count > 0 and len(tickers) < old_count * (1 - drop_threshold):
            logger.warning(
                f"  SKIPPED writing {output_path.name}: new count ({len(tickers)}) "
                f"is {(1 - len(tickers) / old_count) * 100:.0f}% less than baseline "
                f"{compare.name} ({old_count}). Possible rate limiting. Previous file kept."
            )
            return False

    if fmt == "comma":
        content = ",".join(tickers)
    else:
        content = "\n".join(tickers)
    output_path.write_text(content + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["eod", "morning-gap"],
        default="eod",
        help="eod: run end-of-day scanners (Longs/Shorts/RS/HK Shorts). "
             "morning-gap: run intraday gap-up scanner.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    project_root = Path(__file__).parent
    config_path = project_root / "config.toml"
    if not config_path.exists():
        logger.error(f"Config not found: {config_path}")
        return 1

    config = load_config(config_path)
    settings = config.get("settings", {})
    output_dir = project_root / settings.get("output_dir", "output")
    output_dir.mkdir(exist_ok=True)
    delay = settings.get("delay_between_requests", 3.0)
    fmt = settings.get("output_format", "comma")

    # Global dollar volume threshold ($100M) — shared by Longs and RS
    min_dollar_volume = settings.get("min_dollar_volume", 0)

    today = date.today().strftime("%Y_%m_%d")

    us_output_dir = output_dir / "US"
    us_output_dir.mkdir(exist_ok=True)
    hk_output_dir = output_dir / "HK"
    hk_output_dir.mkdir(exist_ok=True)

    if args.mode == "eod":
        # --- Longs (collect only — write deferred until after cross-group dedup) ---
        longs_tickers: set[str] = set()
        for i, screener_cfg in enumerate(config.get("longs", [])):
            name = screener_cfg["name"]
            logger.info(f"[Longs] Running: {name}")
            try:
                tickers = run_screener(screener_cfg["filters"], screener_cfg.get("signal"))
                logger.info(f"  Found {len(tickers)} tickers")
                if min_dollar_volume > 0 and tickers:
                    tickers = filter_dollar_volume_yf(tickers, min_dollar_volume)
                    logger.info(f"  {len(tickers)} after dollar volume filter (20-day avg)")
                min_rvol = screener_cfg.get("min_relative_volume")
                if min_rvol and tickers:
                    rvol_days = screener_cfg.get("relative_volume_days", 20)
                    tickers = filter_relative_volume(tickers, min_rvol, rvol_days)
                    logger.info(f"  {len(tickers)} after relative volume filter (>= {min_rvol}x {rvol_days}-day avg)")
                longs_tickers.update(tickers)
            except Exception as e:
                logger.warning(f"  Failed: {e}")
            if i < len(config.get("longs", [])) - 1:
                time.sleep(delay)

        time.sleep(delay)

        # --- Leaders (collect only) ---
        leaders_tickers: set[str] = set()
        for i, screener_cfg in enumerate(config.get("leaders", [])):
            name = screener_cfg["name"]
            logger.info(f"[Leaders] Running: {name}")
            try:
                tickers = run_screener(screener_cfg["filters"], screener_cfg.get("signal"))
                logger.info(f"  Found {len(tickers)} tickers")
                if min_dollar_volume > 0 and tickers:
                    tickers = filter_dollar_volume_yf(tickers, min_dollar_volume)
                    logger.info(f"  {len(tickers)} after dollar volume filter (20-day avg)")
                leaders_tickers.update(tickers)
            except Exception as e:
                logger.warning(f"  Failed: {e}")
            if i < len(config.get("leaders", [])) - 1:
                time.sleep(delay)

        if config.get("leaders"):
            time.sleep(delay)

        # --- Shorts (independent — write directly) ---
        shorts_cfg = config.get("shorts")
        if shorts_cfg:
            logger.info(f"[Shorts] Running: {shorts_cfg['name']}")
            try:
                total, shorts_tickers = filter_shorts(
                    shorts_cfg["filters"],
                    shorts_cfg.get("signal"),
                    perf_large_cap=shorts_cfg.get("perf_large_cap", 50),
                    perf_mid_cap=shorts_cfg.get("perf_mid_cap", 200),
                    perf_small_cap=shorts_cfg.get("perf_small_cap", 300),
                    min_dollar_volume=shorts_cfg.get("min_dollar_volume", 100_000_000),
                    min_consecutive_up_days=shorts_cfg.get("min_consecutive_up_days", 3),
                )
                logger.info(f"  Found {total} tickers from finviz Ownership screener")

                if shorts_tickers:
                    sorted_shorts = sorted(set(shorts_tickers))
                    dated = us_output_dir / f"{today}_Shorts.txt"
                    prev = _previous_dated_file(us_output_dir, today, "_Shorts.txt")
                    if safe_write_watchlist(sorted_shorts, dated, fmt, baseline_path=prev):
                        logger.info(f"[Shorts] Final: {len(sorted_shorts)} tickers -> {dated}")
                        _futu_sync(config, "shorts", sorted_shorts, "US")
                else:
                    logger.warning("[Shorts] No tickers found after all filters")
            except Exception as e:
                logger.warning(f"[Shorts] Failed: {e}")

        time.sleep(delay)

        # --- RS (conditional, collect only) ---
        rs_tickers: set[str] = set()
        rs_ran = False
        rs_cfg = config.get("rs")
        if rs_cfg:
            logger.info("[RS] Checking market condition...")
            try:
                if check_market_down():
                    logger.info("[RS] Condition met, running screener...")
                    time.sleep(delay)
                    found = run_screener(rs_cfg["filters"], rs_cfg.get("signal"))
                    logger.info(f"  Found {len(found)} tickers")
                    if min_dollar_volume > 0 and found:
                        found = filter_dollar_volume_yf(found, min_dollar_volume)
                        logger.info(f"  {len(found)} after dollar volume filter (20-day avg)")
                    rs_tickers.update(found)
                    rs_ran = True
                else:
                    logger.info("[RS] Condition not met (SPY/QQQ not both down >1.5%), skipping")
            except Exception as e:
                logger.warning(f"[RS] Failed: {e}")

        # --- Cross-group dedup: priority Longs > Leaders > RS ---
        # A ticker firing in multiple long-side groups is kept only in the
        # highest-priority one. Since each .txt and Futu group is rewritten
        # every run, this also prevents day-over-day cross-group duplication.
        before = (len(leaders_tickers), len(rs_tickers))
        leaders_tickers -= longs_tickers
        rs_tickers -= longs_tickers | leaders_tickers
        removed_le = before[0] - len(leaders_tickers)
        removed_rs = before[1] - len(rs_tickers)
        if removed_le or removed_rs:
            logger.info(
                f"[Dedup] Priority Longs > Leaders > RS: "
                f"removed {removed_le} from Leaders, {removed_rs} from RS"
            )

        # --- Write Longs ---
        if longs_tickers:
            sorted_longs = sorted(longs_tickers)
            dated = us_output_dir / f"{today}_Longs.txt"
            prev = _previous_dated_file(us_output_dir, today, "_Longs.txt")
            if safe_write_watchlist(sorted_longs, dated, fmt, baseline_path=prev):
                logger.info(f"[Longs] Total unique: {len(sorted_longs)} -> {dated}")
                _futu_sync(config, "longs", sorted_longs, "US")
        else:
            logger.warning("[Longs] No tickers found")

        # --- Write Leaders ---
        if leaders_tickers:
            sorted_leaders = sorted(leaders_tickers)
            dated = us_output_dir / f"{today}_Leaders.txt"
            prev = _previous_dated_file(us_output_dir, today, "_Leaders.txt")
            if safe_write_watchlist(sorted_leaders, dated, fmt, baseline_path=prev):
                logger.info(f"[Leaders] Total unique: {len(sorted_leaders)} -> {dated}")
                _futu_sync(config, "leaders", sorted_leaders, "US")
        elif config.get("leaders"):
            logger.warning("[Leaders] No tickers found")

        # --- Write RS (only if it actually ran) ---
        if rs_ran:
            if rs_tickers:
                sorted_rs = sorted(rs_tickers)
                dated = us_output_dir / f"{today}_RS.txt"
                prev = _previous_dated_file(us_output_dir, today, "_RS.txt")
                if safe_write_watchlist(sorted_rs, dated, fmt, baseline_path=prev):
                    logger.info(f"[RS] Found {len(sorted_rs)} tickers -> {dated}")
                    _futu_sync(config, "rs", sorted_rs, "US")
            else:
                logger.warning("[RS] No tickers found")

        # --- HK Shorts ---
        hk_shorts_cfg = config.get("hk_shorts")
        if hk_shorts_cfg:
            logger.info(f"[HK Shorts] Running: {hk_shorts_cfg['name']}")
            try:
                total, hk_shorts_tickers = filter_hk_shorts(hk_shorts_cfg)
                logger.info(f"  Universe: {total}, final: {len(hk_shorts_tickers)}")

                if hk_shorts_tickers:
                    sorted_hk = sorted(hk_shorts_tickers)
                    dated = hk_output_dir / f"{today}_Shorts.txt"
                    prev = _previous_dated_file(hk_output_dir, today, "_Shorts.txt")
                    if safe_write_watchlist(sorted_hk, dated, fmt, baseline_path=prev):
                        logger.info(f"[HK Shorts] Final: {len(sorted_hk)} tickers -> {dated}")
                        _futu_sync(config, "hk_shorts", sorted_hk, "HK")
                else:
                    logger.warning("[HK Shorts] No tickers found after all filters")
            except Exception as e:
                logger.warning(f"[HK Shorts] Failed: {e}")

        logger.info("Done.")
        return 0

    if args.mode == "morning-gap":
        morning_cfg = config.get("morning_gap")
        if not morning_cfg:
            logger.error("[Morning Gap] No [morning_gap] config section found")
            return 1

        logger.info(f"[Morning Gap] Running: {morning_cfg['name']}")
        try:
            offset, tickers = run_morning_gap(morning_cfg)
        except Exception as e:
            logger.warning(f"[Morning Gap] Failed: {e}")
            return 1

        if offset == -1:
            return 0  # Outside scan window — not an error

        if tickers:
            sorted_tickers = sorted(set(tickers))
            # Each scan overwrites today's dated file; drop guard compares to
            # the same path (i.e. an earlier scan within the same day).
            dated = us_output_dir / f"{today}_MorningGap.txt"
            if safe_write_watchlist(sorted_tickers, dated, fmt):
                logger.info(
                    f"[Morning Gap] +{offset}min: {len(sorted_tickers)} tickers -> {dated}"
                )
                _futu_sync(config, "morning_gap", sorted_tickers, "US")
        else:
            logger.warning("[Morning Gap] No tickers passed filters")

        logger.info("Done.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
