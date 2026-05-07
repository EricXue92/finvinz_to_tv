"""Compose the daily Markdown + standalone HTML report.

Aesthetic: editorial financial broadsheet — cream paper, ink-on-paper monochrome
with two semantic accents (forest green for positive, oxblood for negative).
Per-ticker block has a numbered header, a snapshot strip, prominent latest-Q
earnings, mini SVG bar charts for 5-year EPS / Revenue YoY, and the LLM-written
Chinese prose. Self-contained: all CSS + SVG inline, no external assets.

The structured data sections (snapshot / quarterly / annual YoY) are rendered
deterministically in Python — the LLM's output is appended only as prose.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import markdown as md_lib

# --- Aesthetic constants -----------------------------------------------------

INLINE_CSS = """
:root {
  --bg:           #FFFFFF;
  --ink:          #111111;
  --ink-soft:     #2C2C2C;
  --muted:        #777777;
  --faint:        #AAAAAA;
  --rule:         #E0E0E0;
  --rule-strong:  #999999;
  --accent:       #111111;   /* monochromatic */
  --emphasis-bg:  #FAFAFA;   /* subtle gray for emphasized sections */
  --negative:     #B00020;   /* used only for clear loss markers */
  --positive:     #1A7F37;   /* used only sparingly */
}
* { box-sizing: border-box; }
html, body { background: var(--bg); }
body {
  margin: 0;
  padding: 40px 24px 80px;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue",
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.sheet { max-width: 780px; margin: 0 auto; }

/* --- Masthead (compact) --- */
.masthead {
  border-bottom: 1px solid var(--rule-strong);
  padding-bottom: 12px;
  margin-bottom: 28px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.masthead-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.005em;
}
.masthead-meta {
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.masthead-meta strong { color: var(--ink); font-weight: 600; }

/* --- Index (compact grid of tickers) --- */
.index {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 4px 12px;
  margin: 0 0 36px;
  padding: 10px 0;
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.index a {
  text-decoration: none;
  color: var(--ink);
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.index a:hover { text-decoration: underline; }
.index .num { color: var(--faint); font-size: 10px; min-width: 16px; }
.index .sym { font-weight: 600; }
.index .grp { color: var(--muted); font-size: 10px; margin-left: auto; }

/* --- Ticker block (compact) --- */
.ticker {
  margin-bottom: 56px;
  padding-top: 28px;
  border-top: 1px solid var(--rule-strong);
  scroll-margin-top: 16px;
}
.ticker:first-of-type { border-top: 0; padding-top: 0; }
.ticker-head {
  display: flex;
  align-items: baseline;
  gap: 8px 14px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.ticker-num {
  color: var(--faint);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  min-width: 22px;
}
.ticker-symbol {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.005em;
  color: var(--ink);
}
.ticker-company {
  font-size: 13px;
  color: var(--ink-soft);
}
.ticker-meta {
  font-size: 11px;
  color: var(--muted);
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.ticker-meta .sep { margin: 0 6px; color: var(--faint); }

/* --- Compact snapshot row --- */
.snapshot {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--ink-soft);
  border-bottom: 1px solid var(--rule);
  padding: 6px 0 10px;
  margin-bottom: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
}
.snapshot .field { display: inline-flex; gap: 5px; align-items: baseline; }
.snapshot .label { color: var(--muted); font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.06em; }
.snapshot .value { color: var(--ink); }
.snapshot .value.positive { color: var(--positive); }
.snapshot .value.negative { color: var(--negative); }

/* --- Latest quarter (prominent — this is core CANSLIM "C") --- */
.quarterly {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  padding: 14px 0 18px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--rule);
}
.quarterly .qtr-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
  margin-bottom: 6px;
}
.quarterly .metric-name {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 2px;
}
.quarterly .metric-value {
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
  line-height: 1.1;
}
.quarterly .yoy {
  display: inline-block;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  margin-top: 4px;
  font-weight: 500;
}
.quarterly .yoy.positive { color: var(--positive); }
.quarterly .yoy.negative { color: var(--negative); }
.quarterly .yoy.null { color: var(--muted); }

/* --- Annual YoY line charts (the user's "连线点图") --- */
.annual {
  margin-bottom: 22px;
  padding-bottom: 4px;
}
.annual-title {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--muted);
  margin-bottom: 10px;
}
.chart-row {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 12px;
  align-items: center;
  padding: 6px 0;
}
.chart-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: 0.04em;
}
.chart-svg { width: 100%; height: 70px; display: block; }

/* --- Prose sections (LLM output) — main reading area --- */
.prose h3 {
  font-size: 13px;
  font-weight: 700;
  color: var(--ink);
  margin: 18px 0 6px;
  padding-bottom: 2px;
  border-bottom: 1px solid var(--rule);
  letter-spacing: 0.02em;
}
.prose h3.emph {
  border-bottom: 2px solid var(--ink);
  margin-top: 22px;
}
.prose h3.emph::before {
  content: "● ";
  color: var(--ink);
  font-size: 10px;
  vertical-align: 1px;
  margin-right: 2px;
}
.prose p {
  margin: 0 0 8px;
}
.prose ul, .prose ol {
  padding-left: 1.4em;
  margin: 4px 0 8px;
}
.prose li { margin: 2px 0; }
.prose strong { font-weight: 700; color: var(--ink); }
.prose em { font-style: italic; color: var(--ink-soft); }

/* --- Truncated tail --- */
.truncated {
  margin-top: 48px;
  padding-top: 14px;
  border-top: 1px solid var(--rule-strong);
}
.truncated h2 {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin: 0 0 8px;
  color: var(--muted);
  font-weight: 600;
}
.truncated .lst {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 2px 12px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.truncated .lst span { color: var(--muted); font-size: 10px; margin-left: 4px; }

/* --- Failure / placeholder --- */
.failure {
  padding: 8px 12px;
  border: 1px solid var(--rule-strong);
  background: var(--bg);
  font-size: 12px;
  color: var(--negative);
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
}

/* Print */
@media print {
  body { padding: 0; font-size: 10pt; }
  .ticker { page-break-inside: avoid; margin-bottom: 28px; }
  .index { display: none; }
}

/* Narrow screens */
@media (max-width: 720px) {
  body { padding: 16px 12px 48px; }
  .quarterly { grid-template-columns: 1fr; gap: 10px; }
  .chart-row { grid-template-columns: 70px 1fr; gap: 8px; }
}
"""


# --- Number formatting -------------------------------------------------------

def _fmt_money(v: float | None) -> str:
    if v is None:
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
    if v is None:
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
    """Connected-dot line chart for YoY deltas. Black ink on white,
    one filled dot per period, line connecting non-null neighbors,
    each dot annotated with its YoY %, period labels along the
    x-axis below the zero line."""
    n = len(values)
    if n == 0:
        return ""
    width, height = 420, 70
    pad_x, pad_top, pad_bottom = 24, 14, 16
    plot_h = height - pad_top - pad_bottom
    baseline = pad_top + plot_h / 2

    nonnull_abs = [abs(v) for v in values if v is not None]
    max_abs = max(nonnull_abs, default=1.0) or 1.0
    half = plot_h / 2 - 4  # leave space for labels

    def x_for(i: int) -> float:
        if n == 1:
            return width / 2
        return pad_x + i * ((width - 2 * pad_x) / (n - 1))

    def y_for(v: float | None) -> float:
        if v is None:
            return baseline
        return baseline - (v / max_abs) * half

    parts: list[str] = []

    # Zero baseline (subtle)
    parts.append(
        f'<line x1="0" y1="{baseline:.1f}" x2="{width}" y2="{baseline:.1f}" '
        'stroke="#E0E0E0" stroke-width="0.6"/>'
    )

    # Connecting polyline through consecutive non-null points only.
    segments: list[tuple[float, float, float, float]] = []
    last_xy: tuple[float, float] | None = None
    for i, v in enumerate(values):
        if v is None:
            last_xy = None
            continue
        x = x_for(i)
        y = y_for(v)
        if last_xy is not None:
            segments.append((last_xy[0], last_xy[1], x, y))
        last_xy = (x, y)
    for x1, y1, x2, y2 in segments:
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            'stroke="#111111" stroke-width="1"/>'
        )

    # Dots + labels
    for i, v in enumerate(values):
        x = x_for(i)
        # Period label (FY-5..FY-1) below the zero line, near the bottom edge
        parts.append(
            f'<text x="{x:.1f}" y="{height - 3:.1f}" font-size="9" '
            f'font-family="SF Mono,Menlo,monospace" fill="#777777" '
            f'text-anchor="middle">{labels[i]}</text>'
        )
        if v is None:
            # Hollow gray ring on baseline + em-dash above
            parts.append(
                f'<circle cx="{x:.1f}" cy="{baseline:.1f}" r="2.5" '
                'fill="#FFFFFF" stroke="#AAAAAA" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{baseline - 6:.1f}" font-size="9" '
                f'font-family="SF Mono,Menlo,monospace" fill="#AAAAAA" '
                f'text-anchor="middle">—</text>'
            )
            continue
        y = y_for(v)
        # Filled black dot
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#111111"/>'
        )
        # YoY% label — above the dot for positive, below for negative
        sign = "+" if v >= 0 else ""
        if v >= 0:
            label_y = max(y - 6, pad_top + 4)
        else:
            label_y = min(y + 13, height - pad_bottom - 1)
        # Bold black label, with red tint when strongly negative for emphasis.
        color = "#111111" if v >= 0 else "#B00020"
        parts.append(
            f'<text x="{x:.1f}" y="{label_y:.1f}" font-size="10" '
            f'font-family="SF Mono,Menlo,monospace" fill="{color}" '
            f'text-anchor="middle" font-weight="600">{sign}{v:.1f}%</text>'
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
    company = _short_company(data.get("company_name"))
    exchange = data.get("exchange") or ""
    group = data.get("group") or ""
    sector = data.get("sector") or ""
    industry = data.get("industry") or ""
    meta_bits = [bit for bit in (exchange, group, sector, industry) if bit]
    meta_html = '<span class="sep">·</span>'.join(
        f'<span>{b}</span>' for b in meta_bits
    )
    return (
        f'<header class="ticker-head">'
        f'<span class="ticker-num">{idx:02d}</span>'
        f'<span class="ticker-symbol">{ticker}</span>'
        f'<span class="ticker-company">{company}</span>'
        f'<span class="ticker-meta">{meta_html}</span>'
        f"</header>"
    )


def _render_snapshot(data: dict[str, Any]) -> str:
    fields: list[tuple[str, str, str]] = []
    fields.append(("Cap", _fmt_money(data.get("market_cap")), ""))
    fields.append(("Price", _fmt_money(data.get("last_price")), ""))
    gap = data.get("gap_pct")
    fields.append(("Gap", _fmt_pct(gap), _yoy_class(gap) if gap is not None else ""))
    rs = data.get("rs_percentile")
    fields.append(("RS", str(rs) if rs is not None else "—", ""))
    inst = data.get("institutional_holdings_pct")
    inst_str = f"{inst:.1f}%" if isinstance(inst, (int, float)) else "—"
    fields.append(("Inst.", inst_str, ""))
    fields.append(("Earnings", _fmt_date(data.get("latest_earnings_date")), ""))
    parts = [
        f'<span class="field"><span class="label">{lab}</span>'
        f'<span class="value{(" " + cls) if cls else ""}">{val}</span></span>'
        for lab, val, cls in fields
    ]
    return f'<section class="snapshot">{"".join(parts)}</section>'


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

    eps_str = f"${eps:,.2f}" if isinstance(eps, (int, float)) else "—"
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


def _render_annual_yoy(data: dict[str, Any]) -> str:
    eps_5y = data.get("annual_eps_yoy_5y") or [None] * 5
    rev_5y = data.get("annual_revenue_yoy_5y") or [None] * 5
    labels = ["FY-5", "FY-4", "FY-3", "FY-2", "FY-1"]
    return (
        f'<section class="annual">'
        f'<div class="annual-title">5-Year Annual Earnings Increases (YoY)</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">EPS YoY</div>'
        f'{_line_chart_svg(eps_5y, labels)}</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">Rev. YoY</div>'
        f'{_line_chart_svg(rev_5y, labels)}</div>'
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
        f'{_render_snapshot(data)}'
        f'{_render_quarterly(data)}'
        f'{_render_annual_yoy(data)}'
        f'{_render_prose(prose_md)}'
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
    inst_str = f"{inst:.1f}%" if isinstance(inst, (int, float)) else "—"
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
    eps_str = f"${eps:,.2f}" if isinstance(eps, (int, float)) else "—"
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
        f"| EPS YoY | {_fmt_pct(eps_5y[0])} | {_fmt_pct(eps_5y[1])} "
        f"| {_fmt_pct(eps_5y[2])} | {_fmt_pct(eps_5y[3])} | {_fmt_pct(eps_5y[4])} |\n"
        f"| Rev. YoY | {_fmt_pct(rev_5y[0])} | {_fmt_pct(rev_5y[1])} "
        f"| {_fmt_pct(rev_5y[2])} | {_fmt_pct(rev_5y[3])} | {_fmt_pct(rev_5y[4])} |\n\n"
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
