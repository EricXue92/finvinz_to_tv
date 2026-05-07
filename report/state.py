"""Paths, env-var access, and priority/group constants for the report mode."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_REPORTS_DIR = PROJECT_ROOT / "output" / "Reports"

PRIORITY_ORDER: list[str] = [
    "EarningsGap",
    "HighVolume",
    "Leaders",
    "GapUp",
    "NewHigh52W",
    "IPO",
    "TopGainers",
    "RS",
]

_HK_EXCLUDES = {"NewHigh52W", "TopGainers"}

MAX_TICKERS_PER_REPORT = 50


def get_api_key() -> str | None:
    """Return ANTHROPIC_API_KEY env var or None when unset."""
    return os.environ.get("ANTHROPIC_API_KEY")


def input_dir_for_market(market: str) -> Path:
    """Return the dated-.txt input directory for the given market ('us' or 'hk')."""
    market = market.lower()
    if market not in ("us", "hk"):
        raise ValueError(f"unknown market: {market!r} (expected 'us' or 'hk')")
    return PROJECT_ROOT / "output" / "TV" / market.upper()


def groups_for_market(market: str) -> list[str]:
    """Priority-ordered list of groups present for the given market."""
    market = market.lower()
    if market not in ("us", "hk"):
        raise ValueError(f"unknown market: {market!r}")
    if market == "us":
        return list(PRIORITY_ORDER)
    return [g for g in PRIORITY_ORDER if g not in _HK_EXCLUDES]
