from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from report import renderer


def test_render_markdown_includes_header_and_sections():
    sections = ["## AAPL\nbody\n", "## NVDA\nbody\n"]
    truncated = [("WMT", "RS")]
    md = renderer.render_markdown(
        market="us",
        date_iso="2026-05-07",
        analyzed_count=2,
        truncated=truncated,
        sections=sections,
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
    )
    assert "# Scan Report — 2026-05-07 (US)" in md
    assert "Total new tickers: 3 (analyzed 2, truncated 1)" in md
    assert "## AAPL" in md
    assert "## NVDA" in md
    assert "## Truncated" in md
    assert "WMT (RS)" in md


def test_render_markdown_omits_truncated_section_when_empty():
    md = renderer.render_markdown(
        market="hk",
        date_iso="2026-05-07",
        analyzed_count=1,
        truncated=[],
        sections=["## 0700.HK\nbody\n"],
        generated_at=datetime(2026, 5, 7, 20, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
    )
    assert "## Truncated" not in md
    assert "Total new tickers: 1 (analyzed 1, truncated 0)" in md


def test_markdown_to_html_is_self_contained():
    md = "# Title\n\nHello **world**."
    html = renderer.markdown_to_html(md, page_title="Test")
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "<h1>Title</h1>" in html
    assert "<strong>world</strong>" in html
    # No external resources.
    assert "http://" not in html
    assert "https://" not in html
    assert 'src="' not in html
    assert "<link" not in html


def test_write_report_files_creates_both(tmp_path: Path):
    md = "# x"
    md_path, html_path = renderer.write_report_files(
        out_dir=tmp_path,
        date_stem="2026_05_07",
        market="us",
        markdown_text=md,
        page_title="Scan Report — 2026-05-07 (US)",
    )
    assert md_path == tmp_path / "2026_05_07_us.md"
    assert html_path == tmp_path / "2026_05_07_us.html"
    assert md_path.read_text() == md
    assert html_path.read_text().startswith("<!doctype html>")
