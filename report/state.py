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


def load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a project-root `.env` file, but never override
    values already set in the environment. Silent no-op if the file is absent.

    Without this, running `uv run main.py --mode report --market us` directly
    (instead of via the wrapper) wouldn't see ANTHROPIC_API_KEY because the
    wrapper's `source .env` only applies to launchd-triggered runs.
    """
    p = path or PROJECT_ROOT / ".env"
    if not p.is_file():
        return
    try:
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as e:
        logger.warning(f"[state] failed to read {p}: {e}")


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
