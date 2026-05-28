"""Read-only RS-line direction audit over the cross-day 'seen' masters.

Scores every already-surfaced ticker (output/state/eod_seen_{US,HK}.txt) by its
RS-line 21EMA 5-bar direction and prints the full distribution weakest-first with
the tolerance cut line marked, so the operator can judge whether 'direction up'
is a good screening rule before it gates any output. Touches nothing: no .txt, no
master, no Futu. yfinance is acceptable here (manual, one-off, bounded list).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

import rs_line

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


def _load_seen(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


def _audit_market(market: str, config: dict, output_dir: Path) -> str | None:
    """Return the report text for one market, or None if its master is empty."""
    seen_path = output_dir / "state" / f"eod_seen_{market.upper()}.txt"
    ids = _load_seen(seen_path)
    if not ids:
        logger.info(f"[rs-line-audit] {market.upper()} master empty — skipping")
        return None

    direction_kwargs = rs_line.direction_params_from_config(config)
    tolerance = rs_line.tolerance_from_config(config)

    if market == "us":
        from us_rs_3m import fetch_us_klines_yf
        bench = fetch_us_klines_yf(["SPY"], period="6mo", batch_size=1).get("SPY")
        klines = fetch_us_klines_yf(ids, period="6mo")  # keyed by symbol == master entry
    else:  # hk
        from hk_eod import fetch_hk_klines_yf, fetch_hsi_kline_yf
        bench = fetch_hsi_kline_yf(period="2y")
        codes = {e: e.split(":", 1)[-1].strip().zfill(4) for e in ids}
        fetched = fetch_hk_klines_yf(list(codes.values()), period="2y")
        klines = {e: fetched.get(_hk_master_to_futu(e)) for e in ids}

    direction = rs_line.compute_rs_direction(klines, bench, **direction_kwargs)
    as_of = date.today().strftime("%Y-%m-%d")
    text = render_report(ids, direction, tolerance, market.upper(), as_of)

    out_file = output_dir / f"rs_line_audit_{market.upper()}_{as_of}.txt"
    out_file.write_text(text + "\n")
    logger.info(f"[rs-line-audit] wrote {out_file}")
    return text


def run_audit(config: dict, output_dir: Path, market: str = "both") -> int:
    """Read-only audit entry point. Never mutates .txt / master / Futu."""
    markets = ["us", "hk"] if market == "both" else [market]
    for m in markets:
        text = _audit_market(m, config, output_dir)
        if text is not None:
            print("\n" + text + "\n")
    return 0
