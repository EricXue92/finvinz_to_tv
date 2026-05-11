#!/usr/bin/env python3
"""One-shot probe: does Futu OpenAPI give us enough HK data for a morning-gap scan?

Run while OpenD is up. Best windows to probe:
  • 09:00–09:20 HKT  → tests pre-auction `pre_change_rate` / `pre_volume`
  • 09:30–16:00 HKT  → tests continuous-trading fields (last_price, prev_close_price, volume, total_market_val)
  • outside hours    → still tests basicinfo + static metadata; live fields will be flat

Prints a focused report so we can decide whether HK morning-gap is feasible
on Futu alone (no Finviz fallback exists for HK).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from futu_sync import _opend_reachable  # noqa: E402

HOST, PORT = "127.0.0.1", 11111

SAMPLE_HK_CODES = [
    "HK.00700",  # Tencent
    "HK.09988",  # Alibaba
    "HK.03690",  # Meituan
    "HK.00388",  # HKEX
    "HK.01024",  # Kuaishou
    "HK.01211",  # BYD
    "HK.00939",  # CCB (大盘股, 应该有 pre_volume)
    "HK.00005",  # HSBC
]

INTERESTING_COLS = [
    "code",
    "name",
    "last_price",
    "prev_close_price",
    "open_price",
    "volume",
    "turnover",
    "total_market_val",
    "pre_change_rate",
    "pre_volume",
    "pre_price",
    "after_change_rate",
    "after_volume",
    "after_price",
    "suspension",
    "delisting",
    "stock_status",
]


def main() -> int:
    if not _opend_reachable(HOST, PORT):
        print(f"OpenD not reachable at {HOST}:{PORT} — start FutuOpenD first.")
        return 1

    try:
        from futu import OpenQuoteContext, RET_OK, Market, SecurityType
    except ImportError:
        print("futu-api not installed. Run: uv add futu-api")
        return 1

    ctx = OpenQuoteContext(host=HOST, port=PORT)
    try:
        # 1. basicinfo — universe builder
        print("=" * 78)
        print("1. get_stock_basicinfo(market=HK, stock_type=STOCK)")
        print("=" * 78)
        ret, basic = ctx.get_stock_basicinfo(
            market=Market.HK, stock_type=SecurityType.STOCK
        )
        if ret != RET_OK:
            print(f"  FAILED: {basic}")
            return 1
        print(f"  rows: {len(basic)}")
        print(f"  columns: {list(basic.columns)}")
        print()
        print("  sample (first 3 rows):")
        print(basic.head(3).to_string(max_colwidth=30))
        print()
        if "exchange_type" in basic.columns:
            print(f"  exchange_type unique values: {basic['exchange_type'].unique().tolist()}")
        if "delisting" in basic.columns:
            print(f"  delisting dtype: {basic['delisting'].dtype}, "
                  f"True count: {(basic['delisting'] == True).sum()}, "  # noqa: E712
                  f"False count: {(basic['delisting'] == False).sum()}")  # noqa: E712
        print()

        # 2. snapshot — what fields are populated right now?
        print("=" * 78)
        print(f"2. get_market_snapshot({SAMPLE_HK_CODES})")
        print("=" * 78)
        ret, snap = ctx.get_market_snapshot(SAMPLE_HK_CODES)
        if ret != RET_OK:
            print(f"  FAILED: {snap}")
            return 1
        print(f"  rows: {len(snap)}")
        print(f"  total columns: {len(snap.columns)}")
        present_cols = [c for c in INTERESTING_COLS if c in snap.columns]
        missing_cols = [c for c in INTERESTING_COLS if c not in snap.columns]
        print(f"  present (of interest): {present_cols}")
        print(f"  missing (of interest): {missing_cols}")
        print()
        print("  values (interesting cols only):")
        print(snap[present_cols].to_string(max_colwidth=30))
        print()

        # 3. focused readout: pre-market fields per ticker
        print("=" * 78)
        print("3. Pre-market / pre-auction readiness check")
        print("=" * 78)
        for _, row in snap.iterrows():
            code = row.get("code")
            last = row.get("last_price")
            prev = row.get("prev_close_price")
            pre_rate = row.get("pre_change_rate")
            pre_vol = row.get("pre_volume")
            pre_price = row.get("pre_price") if "pre_price" in snap.columns else None
            gap_post = None
            try:
                if prev and float(prev) > 0:
                    gap_post = (float(last) - float(prev)) / float(prev) * 100.0
            except (TypeError, ValueError):
                pass
            print(
                f"  {code}: last={last}  prev={prev}  "
                f"post_open_gap={gap_post if gap_post is None else f'{gap_post:+.2f}%'}  "
                f"pre_rate={pre_rate}  pre_vol={pre_vol}  pre_price={pre_price}"
            )
        return 0
    finally:
        try:
            ctx.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
