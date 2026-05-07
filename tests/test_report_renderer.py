from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from report import renderer


HKT = ZoneInfo("Asia/Hong_Kong")


def _fake_data(ticker: str, group: str = "Leaders") -> dict:
    return {
        "ticker": ticker,
        "group": group,
        "exchange": "NASDAQ",
        "company_name": f"{ticker} Inc.",
        "sector": "Technology",
        "industry": "Software",
        "market_cap": 50_000_000_000,
        "last_price": 200.0,
        "prev_close": 195.0,
        "gap_pct": 2.56,
        "institutional_holdings_pct": 65.4,
        "eps_latest_q": 1.25,
        "eps_latest_q_yoy_pct": 18.5,
        "revenue_latest_q": 1_200_000_000,
        "revenue_latest_q_yoy_pct": 22.0,
        "annual_eps_yoy_5y": [None, 12.0, 18.0, 25.0, 30.0],
        "annual_revenue_yoy_5y": [None, 15.0, 20.0, 25.0, 28.0],
        "latest_earnings_date": "2026-04-30",
        "rs_percentile": 92,
        "yahoo_revenue_growth_yoy_pct": None,
        "yahoo_earnings_growth_yoy_pct": None,
    }


# --- HTML document tests -----------------------------------------------------

def test_render_html_document_is_self_contained():
    enriched = [_fake_data("AAPL")]
    html = renderer.render_html_document(
        market="us",
        date_iso="2026-05-07",
        enriched=enriched,
        prose_sections=["### 公司速览\n\nApple makes iPhones."],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "AAPL" in html
    assert "Apple makes iPhones" in html or "Apple" in html
    # No external resources (`http(s)://` only appears in SVG namespace
    # `http://www.w3.org/2000/svg`, which is namespace-only and not a fetch).
    assert 'src="' not in html
    assert "<link" not in html
    assert "@import" not in html
    assert "url(http" not in html


def test_render_html_renders_per_ticker_anchors_and_index():
    enriched = [_fake_data("AAPL"), _fake_data("NVDA", "EarningsGap")]
    html = renderer.render_html_document(
        market="us",
        date_iso="2026-05-07",
        enriched=enriched,
        prose_sections=["### 公司速览\nx\n", "### 公司速览\ny\n"],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert 'id="t-AAPL"' in html
    assert 'id="t-NVDA"' in html
    assert 'href="#t-AAPL"' in html
    assert 'href="#t-NVDA"' in html
    assert "EarningsGap" in html


def test_render_html_includes_svg_bar_chart():
    enriched = [_fake_data("AAPL")]
    html = renderer.render_html_document(
        market="us",
        date_iso="2026-05-07",
        enriched=enriched,
        prose_sections=["### 公司速览\nbody"],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    # Both YoY rows present as inline SVG.
    assert html.count("<svg") >= 2
    assert "EPS YoY" in html
    assert "Rev. YoY" in html


def test_render_html_marks_positive_negative_yoy():
    """Positive YoY gets `positive` class; negative gets `negative` class
    on the latest-quarter YoY badge."""
    d = _fake_data("XYZ")
    d["eps_latest_q_yoy_pct"] = -25.0
    d["revenue_latest_q_yoy_pct"] = 30.0
    html = renderer.render_html_document(
        market="us",
        date_iso="2026-05-07",
        enriched=[d],
        prose_sections=["### 公司速览\nbody"],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert 'class="yoy negative"' in html
    assert 'class="yoy positive"' in html


def test_render_html_failure_prose_renders_as_failure_block():
    html = renderer.render_html_document(
        market="us",
        date_iso="2026-05-07",
        enriched=[_fake_data("XYZ")],
        prose_sections=["## XYZ — ?  (NASDAQ · Leaders)\n\n[配置错误: HTTP 401: bad key]\n"],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert 'class="failure"' in html
    assert "配置错误" in html


def test_render_html_truncated_section_appears():
    html = renderer.render_html_document(
        market="us",
        date_iso="2026-05-07",
        enriched=[_fake_data("AAPL")],
        prose_sections=["### 公司速览\nbody"],
        truncated=[("WMT", "RS"), ("PLTR", "TopGainers")],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "Truncated" in html
    assert "WMT" in html
    assert "PLTR" in html


# --- Markdown document tests -------------------------------------------------

def test_render_markdown_document_includes_data_tables():
    md = renderer.render_markdown_document(
        market="us",
        date_iso="2026-05-07",
        enriched=[_fake_data("AAPL")],
        prose_sections=["### 公司速览\nApple"],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "Daily Scan — 2026-05-07 (US)" in md
    assert "AAPL" in md
    assert "Market Cap" in md
    assert "Latest Quarter" in md
    assert "FY−1" in md
    assert "公司速览" in md


# --- write_report_files (new API) -------------------------------------------

def test_write_report_files_writes_both(tmp_path: Path):
    md_path, html_path = renderer.write_report_files(
        out_dir=tmp_path,
        date_stem="2026_05_07",
        market="us",
        enriched=[_fake_data("AAPL")],
        prose_sections=["### 公司速览\nbody"],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
        date_iso="2026-05-07",
    )
    assert md_path == tmp_path / "2026_05_07_us.md"
    assert html_path == tmp_path / "2026_05_07_us.html"
    assert "Daily Scan" in md_path.read_text(encoding="utf-8")
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_write_report_files_handles_chinese(tmp_path: Path):
    d = _fake_data("0700.HK", "Leaders")
    d["company_name"] = "腾讯控股"
    md_path, html_path = renderer.write_report_files(
        out_dir=tmp_path,
        date_stem="2026_05_07",
        market="hk",
        enriched=[d],
        prose_sections=["### 公司速览\n\n中国互联网龙头。\n\n### 综合判断\n\n看涨。"],
        truncated=[],
        generated_at=datetime(2026, 5, 7, 20, 5, 0, tzinfo=HKT),
        date_iso="2026-05-07",
    )
    md_text = md_path.read_text(encoding="utf-8")
    html_text = html_path.read_text(encoding="utf-8")
    assert "腾讯控股" in md_text
    assert "腾讯控股" in html_text
    assert "公司速览" in html_text


# --- Bar chart helper --------------------------------------------------------

def test_bar_chart_handles_all_null():
    svg = renderer._bar_chart_svg([None] * 5, ["FY-5", "FY-4", "FY-3", "FY-2", "FY-1"])
    assert svg.startswith("<svg")
    assert svg.count("<text") >= 5  # period labels still rendered


def test_line_chart_handles_mix_of_signs():
    """Line chart: black ink on white, red emphasis only on negative labels."""
    svg = renderer._line_chart_svg([10.0, -5.0, None, 25.0, -15.0],
                                    ["FY-5", "FY-4", "FY-3", "FY-2", "FY-1"])
    assert "<circle" in svg                    # at least one filled dot
    assert "#0A0A0A" in svg                    # ink color (positive label)
    assert "#A02828" in svg                    # negative-label color
    assert svg.count("<line") >= 2             # baseline + connecting segments


# --- Legacy shims (keep older callers working) -------------------------------

def test_legacy_render_markdown_still_works():
    md = renderer.render_markdown(
        market="us",
        date_iso="2026-05-07",
        analyzed_count=1,
        truncated=[("WMT", "RS")],
        sections=["## AAPL\nbody"],
        generated_at=datetime(2026, 5, 7, 10, 5, 0, tzinfo=HKT),
    )
    assert "Scan Report" in md
    assert "AAPL" in md
    assert "WMT (RS)" in md


def test_legacy_markdown_to_html_still_works():
    html = renderer.markdown_to_html("# x", page_title="Test")
    assert html.startswith("<!doctype html>")
    assert "<h1>x</h1>" in html
