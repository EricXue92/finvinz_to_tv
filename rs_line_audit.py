"""Read-only RS-line direction audit over the cross-day 'seen' masters.

Scores every already-surfaced ticker (output/state/eod_seen_{US,HK}.txt) by its
RS-line 21EMA 5-bar direction and prints the full distribution weakest-first with
the tolerance cut line marked, so the operator can judge whether 'direction up'
is a good screening rule before it gates any output. Touches nothing: no .txt, no
master, no Futu. yfinance is acceptable here (manual, one-off, bounded list).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _hk_master_to_futu(entry: str) -> str:
    """'HKEX:522' / '522' -> Futu key 'HK.00522' (5-digit, zero-padded)."""
    code = entry.split(":", 1)[-1].strip()
    return f"HK.{int(code):05d}"


def render_report(
    ids: list[str],
    direction: pd.DataFrame,
    tolerance: float,
    market: str,
    as_of: str,
) -> str:
    """Build the audit text. ``direction`` is indexed by id with columns
    rs_ema, rs_ema_chg_5d. Ids absent from it are 'unknown' (kept downstream)."""
    scored = [i for i in ids if i in direction.index
              and pd.notna(direction.loc[i, "rs_ema_chg_5d"])]
    unknown = [i for i in ids if i not in scored]
    scored.sort(key=lambda i: float(direction.loc[i, "rs_ema_chg_5d"]))
    would_cut = sum(
        1 for i in scored if float(direction.loc[i, "rs_ema_chg_5d"]) < -tolerance
    )

    cut_pct = f"-{tolerance * 100:.2f}%"
    lines = [
        f"RS-line direction audit — {market} — {as_of}",
        f"signal: RS-line 21EMA 5-bar slope  |  cut line: chg < {cut_pct}",
        "",
        f"  {'rank':>4}  {'ticker':<14} {'chg_5d':>9}  {'EMA21':>12}  flag",
    ]
    for rank, i in enumerate(scored, 1):
        chg = float(direction.loc[i, "rs_ema_chg_5d"])
        ema = float(direction.loc[i, "rs_ema"])
        flag = "CUT" if chg < -tolerance else ""
        lines.append(f"  {rank:>4}  {i:<14} {chg * 100:>8.2f}%  {ema:>12.6f}  {flag}")

    lines.append("")
    if unknown:
        lines.append(f"unknown (insufficient history, KEPT): {', '.join(unknown)}")
    lines.append(
        f"scanned: {len(ids)} | scored: {len(scored)} | "
        f"unknown: {len(unknown)} | would-cut: {would_cut}"
    )
    return "\n".join(lines)
