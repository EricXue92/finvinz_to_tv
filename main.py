#!/usr/bin/env python3
"""Finviz screener to TradingView watchlist generator."""

import logging
import sys
import time
import tomllib
from pathlib import Path

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


def check_market_down(threshold: float = -1.0) -> bool:
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

    # --- Longs ---
    longs_tickers: set[str] = set()
    for i, screener_cfg in enumerate(config.get("longs", [])):
        name = screener_cfg["name"]
        logger.info(f"[Longs] Running: {name}")
        try:
            tickers = run_screener(screener_cfg["filters"], screener_cfg.get("signal"))
            logger.info(f"  Found {len(tickers)} tickers")
            longs_tickers.update(tickers)
        except Exception as e:
            logger.warning(f"  Failed: {e}")
        if i < len(config.get("longs", [])) - 1:
            time.sleep(delay)

    if longs_tickers:
        sorted_longs = sorted(longs_tickers)
        if safe_write_watchlist(sorted_longs, output_dir / "Longs.txt", fmt):
            logger.info(f"[Longs] Total unique: {len(sorted_longs)} -> output/Longs.txt")
    else:
        logger.warning("[Longs] No tickers found")

    time.sleep(delay)

    # --- Shorts ---
    shorts_cfg = config.get("shorts")
    if shorts_cfg:
        logger.info(f"[Shorts] Running: {shorts_cfg['name']}")
        try:
            shorts_tickers = run_screener(shorts_cfg["filters"], shorts_cfg.get("signal"))
            if shorts_tickers:
                sorted_shorts = sorted(set(shorts_tickers))
                if safe_write_watchlist(sorted_shorts, output_dir / "Shorts.txt", fmt):
                    logger.info(f"[Shorts] Found {len(sorted_shorts)} tickers -> output/Shorts.txt")
            else:
                logger.warning("[Shorts] No tickers found")
        except Exception as e:
            logger.warning(f"[Shorts] Failed: {e}")

    time.sleep(delay)

    # --- RS (conditional) ---
    rs_cfg = config.get("rs")
    if rs_cfg:
        logger.info("[RS] Checking market condition...")
        try:
            if check_market_down(threshold=-1.0):
                logger.info("[RS] Condition met, running screener...")
                time.sleep(delay)
                rs_tickers = run_screener(rs_cfg["filters"], rs_cfg.get("signal"))
                if rs_tickers:
                    sorted_rs = sorted(set(rs_tickers))
                    if safe_write_watchlist(sorted_rs, output_dir / "RS.txt", fmt):
                        logger.info(f"[RS] Found {len(sorted_rs)} tickers -> output/RS.txt")
                else:
                    logger.warning("[RS] No tickers found")
            else:
                logger.info("[RS] Condition not met (SPY/QQQ not both down >1%), skipping")
        except Exception as e:
            logger.warning(f"[RS] Failed: {e}")

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
