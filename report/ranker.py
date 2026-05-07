"""Read dated .txt files, dedup across groups by priority, cap to N tickers."""
from __future__ import annotations

from pathlib import Path

from report.state import groups_for_market

# (ticker_with_exchange_prefix, group_name)
TickerEntry = tuple[str, str]


def read_dated_txt(path: Path) -> list[str]:
    """Parse a TradingView .txt file (comma-separated, possibly with whitespace).
    Returns [] for missing or empty files."""
    if not path.is_file():
        return []
    raw = path.read_text().strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def collect_market_groups(
    input_dir: Path, date_stem: str, market: str
) -> dict[str, list[str]]:
    """Read all priority-ordered group files for the given market.
    Returns dict[group_name -> ticker list], with empty lists for missing files.
    Output dict preserves priority order (Python 3.7+ dict insertion order)."""
    result: dict[str, list[str]] = {}
    for group in groups_for_market(market):
        path = input_dir / f"{date_stem}_{group}.txt"
        result[group] = read_dated_txt(path)
    return result


def rank_and_cap(
    groups: dict[str, list[str]], cap: int
) -> tuple[list[TickerEntry], list[TickerEntry]]:
    """Walk groups in priority order; assign each ticker to the FIRST group it
    appears in. Take the first `cap` entries as `analyzed`; remainder is `truncated`.
    Both returned lists are in priority order."""
    seen: set[str] = set()
    ordered: list[TickerEntry] = []
    for group, tickers in groups.items():
        for ticker in tickers:
            if ticker in seen:
                continue
            seen.add(ticker)
            ordered.append((ticker, group))
    return ordered[:cap], ordered[cap:]
