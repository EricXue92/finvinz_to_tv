#!/usr/bin/env python3
"""Finviz screener to TradingView watchlist generator."""

import logging
import sys
import time
import tomllib
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from finviz import get_stock
from finviz.screener import Screener

logger = logging.getLogger(__name__)


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


def filter_dollar_volume(
    filters: list[str], signal: str | None, min_dollar_volume: float
) -> tuple[int, list[str]]:
    """Run screener with Ownership table and filter by dollar volume.
    Returns (total_found, filtered_tickers)."""
    kwargs = {"filters": filters, "table": "Ownership"}
    if signal:
        kwargs["signal"] = signal
    stock_list = Screener(**kwargs)

    total = len(stock_list.data)
    tickers = []
    for stock in stock_list.data:
        try:
            price = parse_number(stock["Price"])
            avg_vol = parse_number(stock["Avg Volume"])
            if price * avg_vol >= min_dollar_volume:
                tickers.append(stock["Ticker"])
        except (KeyError, ValueError):
            pass
    return total, tickers


def filter_shorts(
    filters: list[str],
    signal: str | None,
    min_dollar_volume: float,
    perf_large_cap: float,
    perf_mid_cap: float,
    perf_small_cap: float,
    delay: float,
) -> tuple[int, list[str]]:
    """Run screener twice (Ownership + Performance), merge, and apply:
    - dollar volume filter (price * avg vol >= min_dollar_volume)
    - cap-conditional month performance filter
    Returns (total_found, filtered_tickers)."""
    kwargs_own = {"filters": filters, "table": "Ownership"}
    if signal:
        kwargs_own["signal"] = signal
    ownership = Screener(**kwargs_own)
    total = len(ownership.data)

    own_by_ticker: dict[str, dict] = {}
    for stock in ownership.data:
        own_by_ticker[stock["Ticker"]] = stock

    time.sleep(delay)

    kwargs_perf = {"filters": filters, "table": "Performance"}
    if signal:
        kwargs_perf["signal"] = signal
    performance = Screener(**kwargs_perf)

    perf_by_ticker: dict[str, dict] = {}
    for stock in performance.data:
        perf_by_ticker[stock["Ticker"]] = stock

    tickers = []
    for ticker, own in own_by_ticker.items():
        try:
            price = parse_number(own["Price"])
            avg_vol = parse_number(own["Avg Volume"])
            if price * avg_vol < min_dollar_volume:
                continue

            market_cap = parse_number(own["Market Cap"])

            perf = perf_by_ticker.get(ticker)
            if not perf:
                continue
            month_perf_str = perf.get("Perf Month", "0%").strip("%")
            month_perf = float(month_perf_str)

            if market_cap >= 10e9:
                threshold = perf_large_cap
            elif market_cap >= 2e9:
                threshold = perf_mid_cap
            else:
                threshold = perf_small_cap

            if month_perf >= threshold:
                tickers.append(ticker)
        except (KeyError, ValueError):
            pass

    return total, tickers


def filter_consecutive_up_days(tickers: list[str], min_days: int) -> list[str]:
    """Filter tickers to those with >= min_days consecutive up days.
    Uses yfinance to fetch recent daily close prices."""
    if not tickers:
        return []

    data = yf.download(tickers, period="1mo", progress=False, group_by="ticker")
    result = []

    # If US market is still open, today's data is incomplete — exclude it
    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = now_et.hour < 16 and now_et.weekday() < 5
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


def filter_relative_volume(tickers: list[str], min_rvol: float, days: int = 20) -> list[str]:
    """Filter tickers by relative volume: latest day's volume / N-day average volume >= min_rvol.
    Uses yfinance to fetch daily volume data."""
    if not tickers:
        return []

    data = yf.download(tickers, period="2mo", progress=False, group_by="ticker", threads=4)
    result = []

    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = now_et.hour < 16 and now_et.weekday() < 5

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                volumes = data["Volume"].dropna()
            else:
                volumes = data[ticker]["Volume"].dropna()

            if market_open and len(volumes) > 0 and volumes.index[-1].date() == now_et.date():
                volumes = volumes.iloc[:-1]

            if len(volumes) < days + 1:
                logger.warning(f"  yfinance: insufficient volume data for {ticker}, keeping it")
                result.append(ticker)
                continue

            current_vol = volumes.iloc[-1]
            avg_vol = volumes.iloc[-(days + 1):-1].mean()

            if avg_vol > 0:
                rvol = current_vol / avg_vol
                if rvol >= min_rvol:
                    result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, keeping it")
            result.append(ticker)

    return result


def check_market_down(threshold: float = -1.5) -> bool:
    """Check if both SPY and QQQ are down more than threshold%."""
    spy = get_stock("SPY")
    qqq = get_stock("QQQ")
    spy_change = float(spy["Change"].strip("%"))
    qqq_change = float(qqq["Change"].strip("%"))
    logger.info(f"  SPY: {spy_change:+.2f}%  QQQ: {qqq_change:+.2f}%")
    return spy_change < threshold and qqq_change < threshold


def safe_write_watchlist(
    tickers: list[str], output_path: Path, fmt: str = "comma", drop_threshold: float = 0.5
) -> bool:
    """Write tickers to file. If the file already exists and new count drops by
    more than drop_threshold (e.g. 0.5 = 50%), keep the old file and warn.
    Returns True if the file was written, False if skipped."""
    if output_path.exists():
        old_content = output_path.read_text().strip()
        old_count = len(old_content.split(",")) if "," in old_content else len(old_content.splitlines())
        if old_count > 0 and len(tickers) < old_count * (1 - drop_threshold):
            logger.warning(
                f"  SKIPPED writing {output_path.name}: new count ({len(tickers)}) "
                f"is {(1 - len(tickers) / old_count) * 100:.0f}% less than previous ({old_count}). "
                f"Possible rate limiting. Previous file kept."
            )
            return False

    if fmt == "comma":
        content = ",".join(tickers)
    else:
        content = "\n".join(tickers)
    output_path.write_text(content + "\n")
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

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

    min_dollar_volume = settings.get("min_dollar_volume", 0)

    # --- Longs ---
    longs_tickers: set[str] = set()
    for i, screener_cfg in enumerate(config.get("longs", [])):
        name = screener_cfg["name"]
        logger.info(f"[Longs] Running: {name}")
        try:
            if min_dollar_volume > 0:
                total, tickers = filter_dollar_volume(
                    screener_cfg["filters"], screener_cfg.get("signal"), min_dollar_volume
                )
                logger.info(f"  Found {total} tickers, {len(tickers)} after dollar volume filter")
            else:
                tickers = run_screener(screener_cfg["filters"], screener_cfg.get("signal"))
                logger.info(f"  Found {len(tickers)} tickers")
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

    today = date.today().strftime("%Y_%m_%d")

    if longs_tickers:
        sorted_longs = sorted(longs_tickers)
        if safe_write_watchlist(sorted_longs, output_dir / "Longs.txt", fmt):
            logger.info(f"[Longs] Total unique: {len(sorted_longs)} -> output/Longs.txt")
            safe_write_watchlist(sorted_longs, output_dir / f"{today}_Longs.txt", fmt)
    else:
        logger.warning("[Longs] No tickers found")

    time.sleep(delay)

    # --- Shorts ---
    shorts_cfg = config.get("shorts")
    if shorts_cfg:
        logger.info(f"[Shorts] Running: {shorts_cfg['name']}")
        try:
            shorts_min_dv = shorts_cfg.get("min_dollar_volume", 100_000_000)
            total, shorts_tickers = filter_shorts(
                shorts_cfg["filters"],
                shorts_cfg.get("signal"),
                min_dollar_volume=shorts_min_dv,
                perf_large_cap=shorts_cfg.get("perf_large_cap", 50),
                perf_mid_cap=shorts_cfg.get("perf_mid_cap", 200),
                perf_small_cap=shorts_cfg.get("perf_small_cap", 300),
                delay=delay,
            )
            logger.info(
                f"  Found {total} tickers, {len(shorts_tickers)} after dollar volume + performance filter"
            )

            min_up_days = shorts_cfg.get("min_consecutive_up_days", 3)
            if shorts_tickers and min_up_days > 0:
                shorts_tickers = filter_consecutive_up_days(shorts_tickers, min_up_days)
                logger.info(f"  {len(shorts_tickers)} after consecutive up days filter (>= {min_up_days})")

            if shorts_tickers:
                sorted_shorts = sorted(set(shorts_tickers))
                if safe_write_watchlist(sorted_shorts, output_dir / "Shorts.txt", fmt):
                    logger.info(f"[Shorts] Final: {len(sorted_shorts)} tickers -> output/Shorts.txt")
                    safe_write_watchlist(sorted_shorts, output_dir / f"{today}_Shorts.txt", fmt)
            else:
                logger.warning("[Shorts] No tickers found after all filters")
        except Exception as e:
            logger.warning(f"[Shorts] Failed: {e}")

    time.sleep(delay)

    # --- RS (conditional) ---
    rs_cfg = config.get("rs")
    if rs_cfg:
        logger.info("[RS] Checking market condition...")
        try:
            if check_market_down():
                logger.info("[RS] Condition met, running screener...")
                time.sleep(delay)
                rs_tickers = run_screener(rs_cfg["filters"], rs_cfg.get("signal"))
                if rs_tickers:
                    sorted_rs = sorted(set(rs_tickers))
                    if safe_write_watchlist(sorted_rs, output_dir / "RS.txt", fmt):
                        logger.info(f"[RS] Found {len(sorted_rs)} tickers -> output/RS.txt")
                        safe_write_watchlist(sorted_rs, output_dir / f"{today}_RS.txt", fmt)
                else:
                    logger.warning("[RS] No tickers found")
            else:
                logger.info("[RS] Condition not met (SPY/QQQ not both down >1.5%), skipping")
        except Exception as e:
            logger.warning(f"[RS] Failed: {e}")

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
