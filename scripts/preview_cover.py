"""Render today's report HTML with the new editorial cover, using the
exact ticker / group list from the existing dated .md file. Skips the LLM
(empty prose blocks). Outputs `output/Reports/_cover_preview.html` for
visual review without paying for another LLM round.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from report import renderer  # noqa: E402

HKT = ZoneInfo("Asia/Hong_Kong")
ROOT = Path(__file__).parent.parent
MD = ROOT / "output" / "Reports" / "2026_05_09_us.md"
OUT = ROOT / "output" / "Reports" / "_cover_preview.html"


def main() -> None:
    text = MD.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^## \d+\. (?P<ticker>\S+) — (?P<name>.+?)  \( · (?P<group>\w+) · (?P<si>.+?)\)$",
        re.MULTILINE,
    )
    enriched = []
    for m in pattern.finditer(text):
        si = m["si"].split(" / ")
        sector = si[0] if si else None
        industry = si[1] if len(si) > 1 else None
        enriched.append({
            "ticker": m["ticker"],
            "group": m["group"],
            "exchange": "",
            "company_name": m["name"],
            "sector": sector,
            "industry": industry,
            "market_cap": None,
            "last_price": None,
            "prev_close": None,
            "gap_pct": None,
            "institutional_holdings_pct": None,
            "roe_pct": None,
            "eps_latest_q": None,
            "eps_latest_q_yoy_pct": None,
            "eps_latest_q_adj": None,
            "eps_latest_q_adj_yoy_pct": None,
            "revenue_latest_q": None,
            "revenue_latest_q_yoy_pct": None,
            "annual_eps_yoy_5y": [None] * 5,
            "annual_revenue_yoy_5y": [None] * 5,
            "quarterly_eps_yoy_4q": [None] * 4,
            "quarterly_eps_yoy_4q_labels": [""] * 4,
            "quarterly_revenue_yoy_4q": [None] * 4,
            "quarterly_revenue_yoy_4q_labels": [""] * 4,
            "latest_earnings_date": None,
            "rs_percentile": None,
            "yahoo_revenue_growth_yoy_pct": None,
            "yahoo_earnings_growth_yoy_pct": None,
        })
    prose = ["### 公司速览\n*Prose preview disabled — see the full report.*"] * len(enriched)

    html = renderer.render_html_document(
        market="us",
        date_iso="2026-05-09",
        enriched=enriched,
        prose_sections=prose,
        truncated=[],
        generated_at=datetime(2026, 5, 9, 16, 30, 10, tzinfo=HKT),
        model_label="Anthropic Claude Sonnet 4.6",
    )
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}  ({len(enriched)} tickers)")


if __name__ == "__main__":
    main()
