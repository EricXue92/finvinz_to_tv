"""Compose the daily Markdown + standalone HTML report.

Aesthetic: editorial financial broadsheet — cream paper, ink-on-paper monochrome
with two semantic accents (forest green for positive, oxblood for negative).
Per-ticker block has a numbered header, a snapshot strip, prominent latest-Q
earnings, mini SVG line charts for 5-year EPS / Revenue YoY + 4-quarter
trajectory, and the LLM-written Chinese prose. Self-contained: all CSS +
SVG inline, no external assets.

The structured data sections (snapshot / quarterly / annual YoY) are rendered
deterministically in Python — the LLM's output is appended only as prose.
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

import markdown as md_lib

# --- Aesthetic constants -----------------------------------------------------

INLINE_CSS = """
:root {
  --bg:           #FAFAFA;
  --card:         #FFFFFF;
  --ink:          #0A0A0A;     /* near-black */
  --ink-soft:     #222222;     /* deeper than before */
  --muted:        #555555;     /* darker secondary text */
  --faint:        #888888;
  --rule:         #D0D0D0;
  --navy:         #1F2D3D;     /* deeper navy */
  --navy-soft:    #2C3E50;
  --tint:         #F1F4F7;
  --positive:     #1E7E34;     /* deeper green */
  --negative:     #A02828;     /* deeper red */
}
* { box-sizing: border-box; }
html, body { background: var(--bg); }
body {
  margin: 0;
  padding: 32px 24px 80px;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  font-size: 17px;
  line-height: 1.7;
  -webkit-font-smoothing: antialiased;
}
.sheet { max-width: 920px; margin: 0 auto; padding: 0 1.2em; }

/* --- Masthead --- */
.masthead {
  border-bottom: 2px solid var(--navy);
  padding-bottom: 14px;
  margin-bottom: 24px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}
.masthead-title {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--navy);
}
.masthead-meta {
  font-size: 13px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.masthead-meta strong { color: var(--ink); font-weight: 600; }

/* --- Index (anchored ticker grid) --- */
.index {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 6px 16px;
  margin: 0 0 40px;
  padding: 12px 0;
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.index a {
  text-decoration: none;
  color: var(--ink);
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.index a:hover { color: var(--navy); }
.index a:hover .sym { text-decoration: underline; }
.index .num { color: var(--faint); font-size: 11px; min-width: 18px; }
.index .sym { font-weight: 600; }
.index .grp {
  color: var(--muted);
  font-size: 10px;
  margin-left: auto;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* --- Ticker block --- */
.ticker {
  margin-bottom: 56px;
  scroll-margin-top: 16px;
}
.ticker-head {
  background: var(--navy);
  color: #FFFFFF;
  padding: 10px 16px;
  border-radius: 5px 5px 0 0;
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.ticker-num {
  font-size: 12px;
  color: rgba(255,255,255,0.55);
  font-variant-numeric: tabular-nums;
  min-width: 22px;
}
.ticker-symbol {
  font-size: 23px;
  font-weight: 700;
  letter-spacing: -0.005em;
}
.ticker-group {
  font-size: 15px;
  font-weight: 500;
  color: rgba(255,255,255,0.88);
  letter-spacing: 0.02em;
}
.ticker-company {
  font-size: 15px;
  font-weight: 400;
  color: rgba(255,255,255,0.78);
  margin-left: 4px;
}

.ticker-body {
  background: var(--card);
  border: 1px solid var(--rule);
  border-top: 0;
  border-radius: 0 0 5px 5px;
  padding: 18px 20px 22px;
}

/* --- Snapshot table (vertical, zebra-striped) --- */
.snapshot { margin-bottom: 22px; }
.snapshot-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 15px;
  font-variant-numeric: tabular-nums;
  background: var(--card);
  border: 1px solid var(--rule);
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.snapshot-table thead th {
  background: var(--tint);
  color: var(--navy);
  font-weight: 700;
  font-size: 13px;
  text-align: left;
  padding: 9px 16px;
  border-bottom: 1px solid var(--rule);
  letter-spacing: 0.04em;
}
.snapshot-table tbody td {
  padding: 9px 16px;
  border-bottom: 1px solid var(--rule);
  vertical-align: top;
}
.snapshot-table tbody tr:last-child td { border-bottom: 0; }
.snapshot-table tbody tr:nth-child(odd) td { background: #FBFBFB; }
.snapshot-table .snap-label {
  font-weight: 600;
  color: var(--ink-soft);
  width: 200px;
  white-space: nowrap;
}
.snapshot-table .snap-value { color: var(--ink); }
.snapshot-table .snap-value.positive { color: var(--positive); font-weight: 500; }
.snapshot-table .snap-value.negative { color: var(--negative); font-weight: 500; }

/* --- Latest quarter (CANSLIM "C") --- */
.quarterly {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 22px;
  padding: 16px 18px;
  margin-bottom: 22px;
  background: var(--card);
  border: 1px solid var(--rule);
  border-left: 4px solid var(--navy);
  border-radius: 3px;
}
.quarterly .qtr-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
  margin-bottom: 7px;
  font-weight: 700;
}
.quarterly .metric-value {
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 30px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
  line-height: 1.1;
  color: var(--navy);
}
.quarterly .yoy {
  display: inline-block;
  font-size: 14px;
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-variant-numeric: tabular-nums;
  margin-top: 7px;
  padding: 3px 10px;
  border-radius: 3px;
  font-weight: 700;
}
.quarterly .yoy.positive { background: rgba(39,174,96,0.10); color: var(--positive); }
.quarterly .yoy.negative { background: rgba(192,57,43,0.10); color: var(--negative); }
.quarterly .yoy.null { background: var(--tint); color: var(--muted); font-weight: 500; }

/* --- Annual YoY line charts --- */
.annual {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 3px;
  padding: 14px 18px;
  margin-bottom: 24px;
}
.annual-title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
  margin-bottom: 12px;
  font-weight: 700;
}
.chart-row {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 14px;
  align-items: center;
  padding: 10px 0;
}
.chart-row + .chart-row {
  border-top: 1px solid var(--rule);
  margin-top: 4px;
  padding-top: 14px;
}
.chart-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--navy);
  letter-spacing: 0.02em;
}
.chart-svg { width: 100%; height: 88px; display: block; }

/* --- Prose sections (LLM output) — main reading area --- */
.prose h3 {
  margin: 26px 0 10px;
  color: var(--navy);
  border-left: 4px solid var(--navy);
  padding: 4px 0 4px 14px;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.3;
}
.prose h3.emph {
  border-left-width: 5px;
  background: var(--tint);
  padding-top: 8px;
  padding-bottom: 8px;
  font-size: 19px;
}
.prose p { margin: 0 0 10px; }
.prose ul, .prose ol { padding-left: 1.5em; margin: 4px 0 10px; }
.prose li { margin: 3px 0; }
.prose strong { font-weight: 700; color: var(--ink); }
.prose em { font-style: italic; color: var(--ink-soft); }

/* --- Truncated tail --- */
.truncated {
  margin-top: 48px;
  padding-top: 16px;
  border-top: 2px solid var(--navy);
}
.truncated h2 {
  font-size: 14px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin: 0 0 10px;
  color: var(--navy);
  font-weight: 700;
}
.truncated .lst {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 4px 14px;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.truncated .lst span {
  color: var(--muted);
  font-size: 10px;
  margin-left: 4px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

/* --- Failure / placeholder --- */
.failure {
  padding: 10px 14px;
  border: 1px dashed var(--negative);
  background: var(--card);
  font-size: 13px;
  color: var(--negative);
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  border-radius: 3px;
}

/* Print */
@media print {
  body { padding: 0; font-size: 11pt; background: #FFFFFF; }
  .ticker { page-break-inside: avoid; margin-bottom: 28px; }
  .index { display: none; }
  .ticker-head { background: #2C3E50 !important; -webkit-print-color-adjust: exact;
    print-color-adjust: exact; }
}

/* Narrow screens */
@media (max-width: 720px) {
  body { padding: 18px 12px 48px; font-size: 14px; }
  .sheet { padding: 0; }
  .quarterly { grid-template-columns: 1fr; gap: 12px; }
  .chart-row { grid-template-columns: 70px 1fr; gap: 8px; }
  .ticker-meta { margin-left: 0; flex-basis: 100%; }
}
"""


# --- Number formatting -------------------------------------------------------

def _fmt_money(v: float | None) -> str:
    # NaN slips through here when yfinance returns a float NaN (vs. None);
    # treat it the same as missing instead of rendering "$nan".
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"${v / 1e12:.2f}T"
    if a >= 1e9:
        return f"${v / 1e9:.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    if a >= 1e3:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.2f}"


def _fmt_pct(v: float | None, sign: bool = True) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    s = "+" if sign and v >= 0 else ""
    return f"{s}{v:.1f}%"


def _yoy_class(v: float | None) -> str:
    if v is None:
        return "null"
    return "positive" if v >= 0 else "negative"


def _short_company(name: str | None, max_len: int = 38) -> str:
    if not name:
        return ""
    n = name.strip()
    return n if len(n) <= max_len else n[: max_len - 1].rstrip() + "…"


def _fmt_date(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v)).strftime("%Y-%m-%d")
        except (OSError, ValueError, OverflowError):
            return "—"
    s = str(v)
    return s.split(" ")[0] if " " in s else s[:10]


# --- Bar chart SVG -----------------------------------------------------------

def _line_chart_svg(values: list[float | None], labels: list[str]) -> str:
    """Connected-dot line chart for YoY deltas.

    Layout uses three horizontal bands so labels never overlap dots:

        y= 0..VLAB_H       value-label band (top)   — for positive values
        y= VLAB_H..PLOT    plot area                — dots + connecting line
        y= PLOT..PLOT+VLAB value-label band (bot.)  — for negative values
        y= PLOT+VLAB..H    period-label band        — FY-3 / FY-2 / FY-1

    Y-axis auto-scales to the actual data range. When all values share a
    sign the baseline is parked at the relevant edge of the plot so the
    full plot height encodes magnitude (instead of wasting half the canvas
    on the empty negative half)."""
    n = len(values)
    if n == 0:
        return ""

    # Compact horizontal footprint — sized to read 5 dots (annual) cleanly;
    # the 4-quarter trajectory chart shares the same canvas.
    width, height = 320, 96
    pad_x = 28
    vlab_h = 18           # value-label band height (top + bottom)
    period_h = 14         # period label band at bottom
    plot_top = vlab_h
    plot_bot = height - vlab_h - period_h
    plot_h = plot_bot - plot_top

    nonnull = [v for v in values if v is not None]
    if nonnull:
        v_max = max(max(nonnull), 0.0)
        v_min = min(min(nonnull), 0.0)
    else:
        v_max, v_min = 1.0, 0.0
    span = v_max - v_min or 1.0

    # Baseline (zero line) y-position — auto-fit to data sign distribution.
    if v_min >= 0:
        baseline = plot_bot                       # all non-negative → zero at bottom
    elif v_max <= 0:
        baseline = plot_top                       # all non-positive → zero at top
    else:
        baseline = plot_top + (v_max / span) * plot_h

    def x_for(i: int) -> float:
        if n == 1:
            return width / 2
        return pad_x + i * ((width - 2 * pad_x) / (n - 1))

    def y_for(v: float) -> float:
        # Linear scale; clamps to plot band defensively.
        return plot_top + ((v_max - v) / span) * plot_h

    parts: list[str] = []

    # Zero baseline (subtle dashed when not at an edge, solid when at edge).
    is_edge = baseline <= plot_top + 0.5 or baseline >= plot_bot - 0.5
    base_stroke = "#D8D8D8" if is_edge else "#CCCCCC"
    base_dash = "" if is_edge else ' stroke-dasharray="3 3"'
    parts.append(
        f'<line x1="{pad_x - 6:.1f}" y1="{baseline:.1f}" '
        f'x2="{width - pad_x + 6:.1f}" y2="{baseline:.1f}" '
        f'stroke="{base_stroke}" stroke-width="0.7"{base_dash}/>'
    )

    # Connecting polyline through consecutive non-null points only.
    last_xy: tuple[float, float] | None = None
    for i, v in enumerate(values):
        if v is None:
            last_xy = None
            continue
        x = x_for(i)
        y = y_for(v)
        if last_xy is not None:
            parts.append(
                f'<line x1="{last_xy[0]:.1f}" y1="{last_xy[1]:.1f}" '
                f'x2="{x:.1f}" y2="{y:.1f}" '
                'stroke="#111111" stroke-width="1.6" stroke-linecap="round"/>'
            )
        last_xy = (x, y)

    # Dots + value labels + period labels
    period_y = height - 3
    top_label_y = vlab_h - 5         # baseline of top value-label band (text)
    bot_label_y = plot_bot + vlab_h - 4  # baseline of bottom value-label band
    for i, v in enumerate(values):
        x = x_for(i)
        # Period label (FY-3 / FY-2 / FY-1) at the bottom edge.
        parts.append(
            f'<text x="{x:.1f}" y="{period_y:.1f}" font-size="10" '
            f'font-family="SF Mono,Menlo,monospace" fill="#777777" '
            f'text-anchor="middle">{labels[i]}</text>'
        )
        if v is None:
            # Hollow gray ring on baseline + "n/a" in the bottom label band.
            parts.append(
                f'<circle cx="{x:.1f}" cy="{baseline:.1f}" r="2.6" '
                'fill="#FFFFFF" stroke="#B5B5B5" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{bot_label_y:.1f}" font-size="10" '
                f'font-family="SF Mono,Menlo,monospace" fill="#999999" '
                f'text-anchor="middle">n/a</text>'
            )
            continue
        y = y_for(v)
        # Filled black dot — slightly larger than before so it reads at a glance.
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" fill="#111111"/>'
        )
        # Value label always sits in a dedicated band, never on the dot:
        # positive → top band, negative → bottom band, zero → bottom band.
        sign = "+" if v >= 0 else ""
        label_y = top_label_y if v > 0 else bot_label_y
        color = "#0A0A0A" if v >= 0 else "#A02828"
        parts.append(
            f'<text x="{x:.1f}" y="{label_y:.1f}" font-size="11.5" '
            f'font-family="SF Mono,Menlo,monospace" fill="{color}" '
            f'text-anchor="middle" font-weight="700">{sign}{v:.1f}%</text>'
        )
    return (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'
    )


# Keep legacy name as alias so existing test imports still resolve.
_bar_chart_svg = _line_chart_svg


# --- Per-ticker HTML blocks --------------------------------------------------

def _render_ticker_header(idx: int, data: dict[str, Any]) -> str:
    ticker = data.get("ticker") or "?"
    group = data.get("group") or ""
    company = _short_company(data.get("company_name"), max_len=42)
    company_html = (
        f'<span class="ticker-company">· {company}</span>' if company else ""
    )
    return (
        f'<header class="ticker-head">'
        f'<span class="ticker-num">{idx:02d}</span>'
        f'<span class="ticker-symbol">{ticker}</span>'
        f'<span class="ticker-group">({group})</span>'
        f"{company_html}"
        f"</header>"
    )


def _render_snapshot(data: dict[str, Any]) -> str:
    """Vertical 2-column Snapshot table (指标 | 数值, zebra-striped) — same
    shape as the markdown-emitted table the user liked, but with deterministic
    Python-formatted numbers and per-row CSS classes for sign coloring."""
    rows: list[tuple[str, str, str]] = []
    sector = data.get("sector") or ""
    industry = data.get("industry") or ""
    sector_str = " / ".join(filter(None, [sector, industry])) or "—"
    rows.append(("Sector / Industry", sector_str, ""))
    gap = data.get("gap_pct")
    if gap is None:
        gap_val = "—"
    else:
        prev = data.get("prev_close")
        last = data.get("last_price")
        if isinstance(prev, (int, float)) and isinstance(last, (int, float)):
            gap_val = f"{_fmt_pct(gap)}  (prev close ${prev:,.2f} → ${last:,.2f})"
        else:
            gap_val = _fmt_pct(gap)
    rows.append(("Gap (today)", gap_val, _yoy_class(gap) if gap is not None else ""))
    rs = data.get("rs_percentile")
    rows.append(("RS Percentile", str(rs) if rs is not None else "—", ""))
    inst = data.get("institutional_holdings_pct")
    inst_str = f"{inst:.1f}%" if isinstance(inst, (int, float)) else "—"
    rows.append(("Inst. Hold", inst_str, ""))
    rows.append(("Latest earnings date",
                 _fmt_date(data.get("latest_earnings_date")), ""))

    body_html = "".join(
        f'<tr><td class="snap-label">{lab}</td>'
        f'<td class="snap-value{(" " + cls) if cls else ""}">{val}</td></tr>'
        for lab, val, cls in rows
    )
    return (
        '<section class="snapshot">'
        '<table class="snapshot-table">'
        '<thead><tr><th>指标</th><th>数值</th></tr></thead>'
        f'<tbody>{body_html}</tbody>'
        '</table>'
        '</section>'
    )


def _render_quarterly(data: dict[str, Any]) -> str:
    eps = data.get("eps_latest_q")
    eps_yoy = data.get("eps_latest_q_yoy_pct")
    if eps_yoy is None:
        eps_yoy = data.get("yahoo_earnings_growth_yoy_pct")
        eps_yoy_src = " (Yahoo)" if eps_yoy is not None else ""
    else:
        eps_yoy_src = ""
    rev = data.get("revenue_latest_q")
    rev_yoy = data.get("revenue_latest_q_yoy_pct")
    if rev_yoy is None:
        rev_yoy = data.get("yahoo_revenue_growth_yoy_pct")
        rev_yoy_src = " (Yahoo)" if rev_yoy is not None else ""
    else:
        rev_yoy_src = ""

    eps_usable = isinstance(eps, (int, float)) and not (isinstance(eps, float) and math.isnan(eps))
    eps_str = f"${eps:,.2f}" if eps_usable else "—"
    rev_str = _fmt_money(rev)

    eps_pill = (
        f'<div class="yoy {_yoy_class(eps_yoy)}">YoY {_fmt_pct(eps_yoy)}{eps_yoy_src}</div>'
    )
    rev_pill = (
        f'<div class="yoy {_yoy_class(rev_yoy)}">YoY {_fmt_pct(rev_yoy)}{rev_yoy_src}</div>'
    )
    return (
        f'<section class="quarterly">'
        f'<div><div class="qtr-label">Latest Quarter — EPS</div>'
        f'<div class="metric-value">{eps_str}</div>{eps_pill}</div>'
        f'<div><div class="qtr-label">Latest Quarter — Revenue</div>'
        f'<div class="metric-value">{rev_str}</div>{rev_pill}</div>'
        f"</section>"
    )


def _render_quarterly_trend(data: dict[str, Any]) -> str:
    """Past 4 quarters of YoY growth — shows whether quarterly EPS / Revenue
    YoY is accelerating, decelerating, or rolling over. EDGAR provides up to
    8 quarterly periods (= 4 YoY pairs); yfinance fallback fills less."""
    eps = data.get("quarterly_eps_yoy_4q") or [None] * 4
    rev = data.get("quarterly_revenue_yoy_4q") or [None] * 4
    eps_lbl = data.get("quarterly_eps_yoy_4q_labels") or [""] * 4
    rev_lbl = data.get("quarterly_revenue_yoy_4q_labels") or [""] * 4
    labels = []
    for i, l in enumerate(eps_lbl):
        if l:
            labels.append(l)
        elif rev_lbl[i]:
            labels.append(rev_lbl[i])
        else:
            n = len(eps_lbl) - 1 - i
            labels.append("Latest" if n == 0 else f"−{n}Q")
    return (
        f'<section class="annual">'
        f'<div class="annual-title">Past 4 Quarters — YoY Growth</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">EPS YoY</div>'
        f'{_line_chart_svg(eps, labels)}</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">Rev. YoY</div>'
        f'{_line_chart_svg(rev, labels)}</div>'
        f"</section>"
    )


def _render_annual_yoy(data: dict[str, Any]) -> str:
    eps = data.get("annual_eps_yoy_5y") or [None] * 5
    rev = data.get("annual_revenue_yoy_5y") or [None] * 5
    labels = ["FY-5", "FY-4", "FY-3", "FY-2", "FY-1"]
    return (
        f'<section class="annual">'
        f'<div class="annual-title">5-Year Annual Earnings Increases (YoY)</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">EPS YoY</div>'
        f'{_line_chart_svg(eps, labels)}</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">Rev. YoY</div>'
        f'{_line_chart_svg(rev, labels)}</div>'
        f"</section>"
    )


# Section names whose H3 should be marked with an "emphasis" class so the CSS
# can highlight them. These map to the user-prioritized analysis legs:
# fundamentals, policy/government support, bottom-line CANSLIM judgement.
_EMPHASIS_HEADINGS = ("基本面", "政策", "综合判断")


def _emphasize_prose_html(html: str) -> str:
    """Wrap heading + following content in <section class="emphasis"> for the
    user-prioritized prose legs. Done by string replacement on the rendered
    H3s — the markdown package doesn't expose a hook for per-heading classes."""
    import re
    # Find each emphasized H3 and inject a class on the heading element.
    # Markdown lib emits `<h3>基本面 / 财报</h3>` (no attributes), so a simple
    # regex is safe.
    for needle in _EMPHASIS_HEADINGS:
        pattern = re.compile(rf'<h3>([^<]*{re.escape(needle)}[^<]*)</h3>')
        html = pattern.sub(r'<h3 class="emph">\1</h3>', html)
    return html


def _render_prose(prose_md: str) -> str:
    if not prose_md or not prose_md.strip():
        return '<div class="failure">[no prose returned]</div>'
    if "[配置错误" in prose_md or "[分析失败" in prose_md:
        return f'<div class="failure">{prose_md}</div>'
    body = md_lib.markdown(
        prose_md,
        extensions=["fenced_code", "sane_lists"],
    )
    body = _emphasize_prose_html(body)
    return f'<div class="prose">{body}</div>'


def _render_ticker_block(idx: int, data: dict[str, Any], prose_md: str) -> str:
    ticker = data.get("ticker") or "?"
    return (
        f'<article class="ticker" id="t-{ticker}">'
        f'{_render_ticker_header(idx, data)}'
        f'<div class="ticker-body">'
        f'{_render_snapshot(data)}'
        f'{_render_quarterly(data)}'
        f'{_render_quarterly_trend(data)}'
        f'{_render_annual_yoy(data)}'
        f'{_render_prose(prose_md)}'
        f"</div>"
        f"</article>"
    )


def _render_index(enriched: list[dict[str, Any]]) -> str:
    if not enriched:
        return ""
    parts: list[str] = []
    for i, d in enumerate(enriched, 1):
        ticker = d.get("ticker") or "?"
        group = d.get("group") or ""
        parts.append(
            f'<a href="#t-{ticker}">'
            f'<span class="num">{i:02d}</span>'
            f'<span class="sym">{ticker}</span>'
            f'<span class="grp">{group}</span></a>'
        )
    return f'<nav class="index">{"".join(parts)}</nav>'


def _render_truncated(truncated: list[tuple[str, str]]) -> str:
    if not truncated:
        return ""
    items = "".join(
        f"<div>{t} <span>{g}</span></div>" for t, g in truncated
    )
    return (
        f'<section class="truncated">'
        f'<h2>Truncated (cap = 50)</h2>'
        f'<div class="lst">{items}</div>'
        f"</section>"
    )


# --- Document-level renderers ------------------------------------------------

def render_html_document(
    market: str,
    date_iso: str,
    enriched: list[dict[str, Any]],
    prose_sections: list[str],
    truncated: list[tuple[str, str]],
    generated_at: datetime,
) -> str:
    market_label = market.upper()
    analyzed_count = len(enriched)
    total = analyzed_count + len(truncated)
    blocks = [
        _render_ticker_block(i + 1, d, p)
        for i, (d, p) in enumerate(zip(enriched, prose_sections))
    ]
    index_html = _render_index(enriched)
    truncated_html = _render_truncated(truncated)
    masthead = (
        f'<header class="masthead">'
        f'<h1 class="masthead-title">Daily Scan · {date_iso}</h1>'
        f'<div class="masthead-meta">'
        f'<span><strong>{market_label}</strong></span>'
        f'<span>analyzed <strong>{analyzed_count}</strong> · '
        f'truncated <strong>{len(truncated)}</strong> · total <strong>{total}</strong></span>'
        f'<span>{generated_at.strftime("%Y-%m-%d %H:%M %Z")}</span>'
        f"</div>"
        f"</header>"
    )
    body = (
        f'<div class="sheet">{masthead}{index_html}'
        f'{"".join(blocks)}{truncated_html}</div>'
    )
    title = f"Daily Scan — {date_iso} ({market_label})"
    return (
        f"<!doctype html>\n<html lang=\"zh\">\n<head>\n"
        f'  <meta charset="utf-8">\n'
        f'  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"  <title>{title}</title>\n"
        f"  <style>{INLINE_CSS}</style>\n"
        f"</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def render_markdown_document(
    market: str,
    date_iso: str,
    enriched: list[dict[str, Any]],
    prose_sections: list[str],
    truncated: list[tuple[str, str]],
    generated_at: datetime,
) -> str:
    """Plain-text Markdown twin of the HTML report. Suitable for grep / diff /
    archival; the .html file is the rendered artifact."""
    market_label = market.upper()
    analyzed_count = len(enriched)
    total = analyzed_count + len(truncated)
    parts: list[str] = []
    parts.append(f"# Daily Scan — {date_iso} ({market_label})\n\n")
    parts.append(
        f"Total: {total}  ·  Analyzed: {analyzed_count}  ·  Truncated: {len(truncated)}\n"
    )
    parts.append(
        f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
    )
    parts.append("---\n\n")
    for i, (d, prose) in enumerate(zip(enriched, prose_sections), 1):
        parts.append(_render_md_ticker(i, d, prose))
        parts.append("\n---\n\n")
    if truncated:
        parts.append("## Truncated (cap = 50)\n\n")
        for t, g in truncated:
            parts.append(f"- {t} ({g})\n")
    return "".join(parts)


def _render_md_ticker(idx: int, d: dict[str, Any], prose: str) -> str:
    ticker = d.get("ticker") or "?"
    company = d.get("company_name") or ""
    exchange = d.get("exchange") or ""
    group = d.get("group") or ""
    sector = d.get("sector") or ""
    industry = d.get("industry") or ""
    head = f"## {idx:02d}. {ticker}"
    if company:
        head += f" — {company}"
    head += f"  ({exchange} · {group}"
    if sector or industry:
        head += f" · {' / '.join(filter(None, [sector, industry]))}"
    head += ")\n\n"

    snap = (
        f"| Market Cap | Price | Gap | RS | Inst. Hold | Earnings Date |\n"
        f"|---|---|---|---|---|---|\n"
        f"| {_fmt_money(d.get('market_cap'))} "
        f"| {_fmt_money(d.get('last_price'))} "
        f"| {_fmt_pct(d.get('gap_pct'))} "
        f"| {d.get('rs_percentile') if d.get('rs_percentile') is not None else '—'} "
        f"| {d.get('institutional_holdings_pct'):.1f}%"
        if isinstance(d.get('institutional_holdings_pct'), (int, float))
        else f"| {_fmt_money(d.get('market_cap'))} "
             f"| {_fmt_money(d.get('last_price'))} "
             f"| {_fmt_pct(d.get('gap_pct'))} "
             f"| {d.get('rs_percentile') if d.get('rs_percentile') is not None else '—'} "
             f"| —"
    )
    # Simpler: rebuild snap without the ternary spaghetti
    inst = d.get("institutional_holdings_pct")
    inst_usable = isinstance(inst, (int, float)) and not (isinstance(inst, float) and math.isnan(inst))
    inst_str = f"{inst:.1f}%" if inst_usable else "—"
    snap = (
        f"| Market Cap | Price | Gap | RS | Inst. Hold | Earnings Date |\n"
        f"|---|---|---|---|---|---|\n"
        f"| {_fmt_money(d.get('market_cap'))} "
        f"| {_fmt_money(d.get('last_price'))} "
        f"| {_fmt_pct(d.get('gap_pct'))} "
        f"| {d.get('rs_percentile') if d.get('rs_percentile') is not None else '—'} "
        f"| {inst_str} "
        f"| {_fmt_date(d.get('latest_earnings_date'))} |\n\n"
    )

    eps = d.get("eps_latest_q")
    eps_usable = isinstance(eps, (int, float)) and not (isinstance(eps, float) and math.isnan(eps))
    eps_str = f"${eps:,.2f}" if eps_usable else "—"
    rev_str = _fmt_money(d.get("revenue_latest_q"))
    eps_yoy = d.get("eps_latest_q_yoy_pct")
    rev_yoy = d.get("revenue_latest_q_yoy_pct")
    if eps_yoy is None:
        eps_yoy = d.get("yahoo_earnings_growth_yoy_pct")
        eps_src = " (Yahoo)" if eps_yoy is not None else ""
    else:
        eps_src = ""
    if rev_yoy is None:
        rev_yoy = d.get("yahoo_revenue_growth_yoy_pct")
        rev_src = " (Yahoo)" if rev_yoy is not None else ""
    else:
        rev_src = ""
    qtr = (
        f"**Latest Quarter:**  EPS {eps_str} (YoY {_fmt_pct(eps_yoy)}{eps_src})  "
        f"·  Revenue {rev_str} (YoY {_fmt_pct(rev_yoy)}{rev_src})\n\n"
    )

    eps_5y = d.get("annual_eps_yoy_5y") or [None] * 5
    rev_5y = d.get("annual_revenue_yoy_5y") or [None] * 5
    annual = (
        "| Year | FY−5 | FY−4 | FY−3 | FY−2 | FY−1 |\n"
        "|---|---|---|---|---|---|\n"
        f"| EPS YoY | {_fmt_pct(eps_5y[0])} | {_fmt_pct(eps_5y[1])} | {_fmt_pct(eps_5y[2])} | {_fmt_pct(eps_5y[3])} | {_fmt_pct(eps_5y[4])} |\n"
        f"| Rev. YoY | {_fmt_pct(rev_5y[0])} | {_fmt_pct(rev_5y[1])} | {_fmt_pct(rev_5y[2])} | {_fmt_pct(rev_5y[3])} | {_fmt_pct(rev_5y[4])} |\n\n"
    )

    return head + snap + qtr + annual + (prose.rstrip() + "\n")


# --- File output -------------------------------------------------------------

def write_report_files(
    out_dir: Path,
    date_stem: str,
    market: str,
    enriched: list[dict[str, Any]],
    prose_sections: list[str],
    truncated: list[tuple[str, str]],
    generated_at: datetime,
    date_iso: str,
) -> tuple[Path, Path]:
    """Write both the .md and .html reports; return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{date_stem}_{market}.md"
    html_path = out_dir / f"{date_stem}_{market}.html"
    md_text = render_markdown_document(
        market=market, date_iso=date_iso, enriched=enriched,
        prose_sections=prose_sections, truncated=truncated,
        generated_at=generated_at,
    )
    html_text = render_html_document(
        market=market, date_iso=date_iso, enriched=enriched,
        prose_sections=prose_sections, truncated=truncated,
        generated_at=generated_at,
    )
    md_path.write_text(md_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    return md_path, html_path


# --- Backwards-compat shims (older tests still call these) -------------------

def render_markdown(
    market: str,
    date_iso: str,
    analyzed_count: int,
    truncated: list[tuple[str, str]],
    sections: list[str],
    generated_at: datetime,
) -> str:
    """Legacy: header + flat sections + truncated. Kept so older tests pass.
    New code should call render_markdown_document(...)."""
    market_label = market.upper()
    total = analyzed_count + len(truncated)
    parts: list[str] = []
    parts.append(f"# Scan Report — {date_iso} ({market_label})\n")
    parts.append(
        f"Total new tickers: {total} "
        f"(analyzed {analyzed_count}, truncated {len(truncated)})\n"
    )
    parts.append(f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
    parts.append("\n---\n")
    for s in sections:
        parts.append(s.rstrip() + "\n")
        parts.append("\n---\n")
    if truncated:
        parts.append("\n## Truncated (cap = 50)\n")
        for t, g in truncated:
            parts.append(f"- {t} ({g})\n")
    return "".join(parts)


def markdown_to_html(markdown_text: str, page_title: str) -> str:
    """Legacy: render free-form Markdown into the editorial HTML shell."""
    body = md_lib.markdown(
        markdown_text, extensions=["tables", "fenced_code", "sane_lists"]
    )
    return (
        f"<!doctype html>\n<html lang=\"zh\">\n<head>\n"
        f'  <meta charset="utf-8">\n'
        f"  <title>{page_title}</title>\n"
        f"  <style>{INLINE_CSS}</style>\n"
        f"</head>\n<body><div class=\"sheet prose\">{body}</div></body>\n</html>\n"
    )
