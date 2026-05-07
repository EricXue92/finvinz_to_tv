"""Compose Markdown report and render to a standalone HTML file."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import markdown as md_lib

INLINE_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  max-width: 900px; margin: 2em auto; padding: 0 1em; color: #222; line-height: 1.55; }
h1 { border-bottom: 2px solid #ddd; padding-bottom: 0.3em; }
h2 { margin-top: 2em; border-bottom: 1px solid #eee; padding-bottom: 0.2em; }
h3 { margin-top: 1.5em; }
code, pre { background: #f5f5f5; border-radius: 4px; }
code { padding: 0.1em 0.3em; }
pre { padding: 0.8em; overflow-x: auto; }
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #ddd; padding: 0.4em 0.8em; text-align: left; }
ul { padding-left: 1.5em; }
hr { border: none; border-top: 1px solid #eee; margin: 2em 0; }
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
