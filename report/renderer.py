"""Compose Markdown report and render to a standalone HTML file."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import markdown as md_lib

INLINE_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
  "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  max-width: 920px; margin: 2em auto; padding: 0 1.2em; color: #222;
  line-height: 1.65; background: #fafafa; }
h1 { border-bottom: 2px solid #444; padding-bottom: 0.3em; margin-top: 0; }
h2 { margin-top: 2.5em; padding: 0.45em 0.7em; background: #2c3e50; color: #fff;
  border-radius: 4px; font-size: 1.35em; }
h3 { margin-top: 1.6em; color: #2c3e50;
  border-left: 4px solid #2c3e50; padding-left: 0.6em; font-size: 1.1em; }
p { margin: 0.6em 0; }
code, pre { background: #f0f0f0; border-radius: 4px; font-family:
  "SF Mono", Menlo, Monaco, Consolas, monospace; }
code { padding: 0.1em 0.35em; font-size: 0.92em; }
pre { padding: 0.9em; overflow-x: auto; }
table { border-collapse: collapse; margin: 1em 0; width: 100%;
  background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
th, td { border: 1px solid #e0e0e0; padding: 0.5em 0.9em; text-align: left;
  vertical-align: top; }
th { background: #f5f5f5; font-weight: 600; }
tbody tr:nth-child(odd) { background: #fbfbfb; }
tbody td:first-child { font-weight: 600; color: #555; white-space: nowrap;
  width: 200px; }
ul, ol { padding-left: 1.6em; margin: 0.6em 0; }
li { margin: 0.25em 0; }
hr { border: none; border-top: 4px double #c0c0c0; margin: 3em 0; }
strong { color: #1a1a1a; }
"""


def render_markdown(
    market: str,
    date_iso: str,
    analyzed_count: int,
    truncated: list[tuple[str, str]],
    sections: list[str],
    generated_at: datetime,
) -> str:
    """Compose the full Markdown document."""
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
    for section in sections:
        body = section.rstrip() + "\n"
        parts.append(body)
        parts.append("\n---\n")
    if truncated:
        parts.append("\n## Truncated (cap = 50)\n")
        for ticker, group in truncated:
            parts.append(f"- {ticker} ({group})\n")
    return "".join(parts)


def markdown_to_html(markdown_text: str, page_title: str) -> str:
    """Render Markdown to a self-contained HTML5 document with inline CSS."""
    body_html = md_lib.markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    return (
        f"<!doctype html>\n"
        f"<html lang=\"zh\">\n"
        f"<head>\n"
        f"  <meta charset=\"utf-8\">\n"
        f"  <title>{page_title}</title>\n"
        f"  <style>{INLINE_CSS}</style>\n"
        f"</head>\n"
        f"<body>\n{body_html}\n</body>\n"
        f"</html>\n"
    )


def write_report_files(
    out_dir: Path,
    date_stem: str,
    market: str,
    markdown_text: str,
    page_title: str,
) -> tuple[Path, Path]:
    """Write both .md and .html under out_dir; return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{date_stem}_{market}.md"
    html_path = out_dir / f"{date_stem}_{market}.html"
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown_text, page_title), encoding="utf-8")
    return md_path, html_path
