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
  --paper:        #F8F4ED;
  --paper-lift:   #FCFAF4;
  --ink:          #1B1814;
  --muted:        #6F665A;
  --rule:         #E0D6C0;
  --rule-strong:  #B8AB95;
  --symbol:       #16314C;
  --positive:     #2D6A4F;
  --negative:     #8B2635;
  --null:         #B8AB95;
  --highlight:    #F3E7CC;
  --accent:       #BD8B3C;
}
* { box-sizing: border-box; }
html { background: var(--paper); }
body {
  margin: 0;
  padding: 56px 32px 96px;
  color: var(--ink);
  background: var(--paper);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  font-size: 15px;
  line-height: 1.65;
  font-feature-settings: "kern", "liga", "calt";
  -webkit-font-smoothing: antialiased;
}
.sheet { max-width: 760px; margin: 0 auto; }

/* --- Masthead --- */
.masthead {
  border-top: 4px double var(--ink);
  border-bottom: 1px solid var(--rule-strong);
  padding: 24px 0 18px;
  margin-bottom: 56px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}
.masthead-title {
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  font-size: 34px;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
  line-height: 1.1;
}
.masthead-meta {
  display: flex;
  gap: 18px;
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.masthead-meta strong { color: var(--ink); font-weight: 600; }

/* --- Index --- */
.index {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px 16px;
  margin: 0 0 64px;
  padding: 16px 0;
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
}
.index a {
  display: flex;
  align-items: baseline;
  gap: 8px;
  text-decoration: none;
  color: var(--ink);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.index a:hover { color: var(--symbol); }
.index .num {
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.05em;
}
.index .sym { font-weight: 600; letter-spacing: 0.02em; }
.index .grp { color: var(--muted); font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.1em; margin-left: auto; }

/* --- Ticker block --- */
.ticker {
  margin-bottom: 88px;
  scroll-margin-top: 24px;
}
.ticker-head {
  display: grid;
  grid-template-columns: 64px 1fr;
  align-items: baseline;
  gap: 12px 18px;
  border-top: 2px solid var(--ink);
  padding-top: 22px;
  margin-bottom: 22px;
}
.ticker-num {
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  font-size: 13px;
  letter-spacing: 0.08em;
  color: var(--muted);
  font-variant-numeric: lining-nums tabular-nums;
  align-self: start;
  padding-top: 10px;
}
.ticker-symbol {
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  font-size: 44px;
  font-weight: 600;
  color: var(--symbol);
  letter-spacing: -0.02em;
  line-height: 1;
}
.ticker-symbol .em-dash {
  color: var(--muted);
  font-weight: 400;
  margin: 0 0.15em;
}
.ticker-symbol .company {
  font-style: italic;
  font-weight: 400;
  color: var(--ink);
}
.ticker-meta {
  grid-column: 2;
  margin-top: 6px;
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
}
.ticker-meta .group-tag {
  background: var(--highlight);
  color: var(--ink);
  padding: 1px 8px;
  border-radius: 2px;
  letter-spacing: 0.08em;
}

/* --- Snapshot strip --- */
.snapshot {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 1px;
  background: var(--rule);
  margin-bottom: 28px;
  border: 1px solid var(--rule);
}
.snapshot .cell {
  background: var(--paper-lift);
  padding: 10px 12px;
}
.snapshot .label {
  font-size: 9px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
  margin-bottom: 4px;
}
.snapshot .value {
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
  line-height: 1.2;
}
.snapshot .value.muted { color: var(--muted); }
.snapshot .value.positive { color: var(--positive); }
.snapshot .value.negative { color: var(--negative); }

/* --- Quarterly earnings card --- */
.quarterly {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  border: 1px solid var(--rule);
  border-left: 3px solid var(--accent);
  padding: 18px 22px;
  background: var(--paper-lift);
  margin-bottom: 28px;
}
.quarterly .qtr-label {
  font-size: 9px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
  margin-bottom: 6px;
}
.quarterly .metric-name {
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
  letter-spacing: 0.04em;
}
.quarterly .metric-value {
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 26px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
  line-height: 1;
}
.quarterly .yoy-pill {
  display: inline-block;
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.02em;
  padding: 2px 8px;
  border-radius: 2px;
  margin-top: 8px;
}
.quarterly .yoy-pill.positive { background: rgba(45,106,79,.10); color: var(--positive); }
.quarterly .yoy-pill.negative { background: rgba(139,38,53,.10); color: var(--negative); }
.quarterly .yoy-pill.null { background: var(--paper); color: var(--muted); }

/* --- Annual YoY chart --- */
.annual {
  border: 1px solid var(--rule);
  padding: 18px 22px;
  margin-bottom: 28px;
  background: var(--paper-lift);
}
.annual h4 {
  margin: 0 0 14px;
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 600;
}
.chart-row {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 16px;
  align-items: center;
  padding: 4px 0;
}
.chart-row + .chart-row { border-top: 1px solid var(--rule); padding-top: 14px; margin-top: 10px; }
.chart-name {
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink);
  font-weight: 600;
}
.chart-svg { width: 100%; height: 56px; display: block; }

/* --- Prose sections (LLM output) --- */
.prose h3 {
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.005em;
  color: var(--ink);
  margin: 26px 0 8px;
  padding-left: 14px;
  border-left: 2px solid var(--accent);
  line-height: 1.25;
}
.prose h3::before {
  content: counter(prose-section, decimal-leading-zero) ".";
  font-size: 11px;
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-weight: 500;
  color: var(--muted);
  margin-right: 8px;
  letter-spacing: 0.06em;
  vertical-align: 1px;
}
.prose { counter-reset: prose-section; }
.prose h3 { counter-increment: prose-section; }
.prose p {
  margin: 0 0 0.85em;
  text-align: justify;
  hyphens: auto;
}
.prose ul, .prose ol { padding-left: 1.4em; margin: 0.4em 0 0.85em; }
.prose li { margin: 0.15em 0; }
.prose strong { font-weight: 600; color: var(--ink); }
.prose em { color: var(--muted); }

/* --- Truncated --- */
.truncated {
  margin-top: 64px;
  padding-top: 24px;
  border-top: 4px double var(--ink);
}
.truncated h2 {
  font-family: "New York", "Iowan Old Style", "Charter", "Georgia", serif;
  font-size: 16px;
  letter-spacing: 0.04em;
  font-weight: 600;
  margin: 0 0 12px;
}
.truncated .lst {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 4px 12px;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.truncated .lst span { color: var(--muted); font-size: 11px; margin-left: 6px;
  letter-spacing: 0.08em; text-transform: uppercase; }

/* --- Empty / failure block --- */
.failure {
  padding: 14px 18px;
  border: 1px dashed var(--rule-strong);
  background: var(--paper-lift);
  font-size: 13px;
  color: var(--negative);
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
}

/* Print friendly */
@media print {
  body { padding: 0; font-size: 11pt; }
  .ticker { page-break-inside: avoid; margin-bottom: 32px; }
  .index { display: none; }
}

/* Narrow screens */
@media (max-width: 720px) {
  body { padding: 24px 14px 64px; font-size: 14px; }
  .snapshot { grid-template-columns: repeat(3, 1fr); }
  .quarterly { grid-template-columns: 1fr; gap: 14px; }
  .ticker-head { grid-template-columns: 48px 1fr; }
  .ticker-symbol { font-size: 32px; }
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

def _bar_chart_svg(values: list[float | None], labels: list[str]) -> str:
    """Mini bar chart for YoY deltas. Positive = forest green up-bar,
    negative = oxblood down-bar, null = thin gray rule on baseline.
    Width is responsive (viewBox auto-scales)."""
    n = len(values)
    if n == 0:
        return ""
    width, height = 360, 56
    pad_x = 4
    bar_gap = 6
    bar_w = (width - 2 * pad_x - (n - 1) * bar_gap) / n
    baseline = height / 2 + 4

    nonnull_abs = [abs(v) for v in values if v is not None]
    max_abs = max(nonnull_abs, default=1.0)
    if max_abs == 0:
        max_abs = 1.0
    max_bar_h = (height - 24) / 2  # leave room for labels above + below

    parts: list[str] = []
    parts.append(
        f'<line x1="0" y1="{baseline:.1f}" x2="{width}" y2="{baseline:.1f}" '
        'stroke="#D8CDB8" stroke-width="0.6"/>'
    )
    for i, v in enumerate(values):
        x = pad_x + i * (bar_w + bar_gap)
        cx = x + bar_w / 2
        # Period label below baseline
        parts.append(
            f'<text x="{cx:.1f}" y="{height - 2:.1f}" font-size="8" '
            f'font-family="SF Mono,Menlo,monospace" fill="#6F665A" '
            f'text-anchor="middle" letter-spacing="0.06em">{labels[i]}</text>'
        )
        if v is None:
            parts.append(
                f'<rect x="{x:.1f}" y="{baseline - 1:.1f}" width="{bar_w:.1f}" '
                f'height="2" fill="#B8AB95" opacity="0.5"/>'
            )
            parts.append(
                f'<text x="{cx:.1f}" y="{baseline - 6:.1f}" font-size="9" '
                f'font-family="SF Mono,Menlo,monospace" fill="#6F665A" '
                f'text-anchor="middle">—</text>'
            )
            continue
        h = (abs(v) / max_abs) * max_bar_h
        if v >= 0:
            y = baseline - h
            color = "#2D6A4F"
            label_y = max(y - 4, 10)
        else:
            y = baseline
            color = "#8B2635"
            label_y = min(baseline + h + 11, height - 14)
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
            f'height="{h:.1f}" fill="{color}" rx="1"/>'
        )
        # Numeric label
        sign = "+" if v >= 0 else ""
        parts.append(
            f'<text x="{cx:.1f}" y="{label_y:.1f}" font-size="9" '
            f'font-family="SF Mono,Menlo,monospace" fill="#1B1814" '
            f'text-anchor="middle" font-weight="600">{sign}{v:.0f}%</text>'
        )
    return (
        f'<svg class="chart-svg" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'
    )


# --- Per-ticker HTML blocks --------------------------------------------------

def _render_ticker_header(idx: int, data: dict[str, Any]) -> str:
    ticker = data.get("ticker") or "?"
    company = _short_company(data.get("company_name"))
    exchange = data.get("exchange") or ""
    group = data.get("group") or ""
    sector = data.get("sector") or ""
    industry = data.get("industry") or ""
    company_html = (
        f'<span class="em-dash">—</span><span class="company">{company}</span>'
        if company else ""
    )
    meta_parts: list[str] = []
    if exchange:
        meta_parts.append(f"<span>{exchange}</span>")
    if group:
        meta_parts.append(f'<span class="group-tag">{group}</span>')
    if sector or industry:
        tax = " / ".join(filter(None, [sector, industry]))
        meta_parts.append(f"<span>{tax}</span>")
    meta_html = "".join(meta_parts)
    return (
        f'<header class="ticker-head">'
        f'<div class="ticker-num">№ {idx:02d}</div>'
        f'<div class="ticker-symbol">{ticker}{company_html}</div>'
        f'<div class="ticker-meta">{meta_html}</div>'
        f"</header>"
    )


def _render_snapshot(data: dict[str, Any]) -> str:
    cells: list[tuple[str, str, str]] = []
    cells.append(("Market Cap", _fmt_money(data.get("market_cap")), ""))
    cells.append(("Price", _fmt_money(data.get("last_price")), ""))
    gap = data.get("gap_pct")
    cells.append(("Gap Today", _fmt_pct(gap), _yoy_class(gap)))
    rs = data.get("rs_percentile")
    cells.append(("RS Pct.", str(rs) if rs is not None else "—", ""))
    inst = data.get("institutional_holdings_pct")
    inst_str = f"{inst:.1f}%" if isinstance(inst, (int, float)) else "—"
    cells.append(("Inst. Hold", inst_str, ""))
    cells.append(("Earnings Date", _fmt_date(data.get("latest_earnings_date")), ""))
    parts = [
        f'<div class="cell"><div class="label">{lab}</div>'
        f'<div class="value{(" " + cls) if cls else ""}">{val}</div></div>'
        for lab, val, cls in cells
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
        f'<span class="yoy-pill {_yoy_class(eps_yoy)}">YoY {_fmt_pct(eps_yoy)}{eps_yoy_src}</span>'
    )
    rev_pill = (
        f'<span class="yoy-pill {_yoy_class(rev_yoy)}">YoY {_fmt_pct(rev_yoy)}{rev_yoy_src}</span>'
    )
    return (
        f'<section class="quarterly">'
        f'<div><div class="qtr-label">Latest Quarter</div>'
        f'<div class="metric-name">EPS (diluted)</div>'
        f'<div class="metric-value">{eps_str}</div>{eps_pill}</div>'
        f'<div><div class="qtr-label">&nbsp;</div>'
        f'<div class="metric-name">Revenue</div>'
        f'<div class="metric-value">{rev_str}</div>{rev_pill}</div>'
        f"</section>"
    )


def _render_annual_yoy(data: dict[str, Any]) -> str:
    eps_5y = data.get("annual_eps_yoy_5y") or [None] * 5
    rev_5y = data.get("annual_revenue_yoy_5y") or [None] * 5
    labels = ["FY−5", "FY−4", "FY−3", "FY−2", "FY−1"]
    return (
        f'<section class="annual">'
        f'<h4>Annual Earnings Increases — Past 5 Fiscal Years</h4>'
        f'<div class="chart-row">'
        f'<div class="chart-name">EPS YoY</div>'
        f'{_bar_chart_svg(eps_5y, labels)}</div>'
        f'<div class="chart-row">'
        f'<div class="chart-name">Rev. YoY</div>'
        f'{_bar_chart_svg(rev_5y, labels)}</div>'
        f"</section>"
    )


def _render_prose(prose_md: str) -> str:
    if not prose_md or not prose_md.strip():
        return '<div class="failure">[no prose returned]</div>'
    if "[配置错误" in prose_md or "[分析失败" in prose_md:
        return f'<div class="failure">{prose_md}</div>'
    body = md_lib.markdown(
        prose_md,
        extensions=["fenced_code", "sane_lists"],
    )
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
