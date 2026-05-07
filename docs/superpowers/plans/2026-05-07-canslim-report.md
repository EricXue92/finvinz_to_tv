# CANSLIM Daily Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--mode report --market {us,hk}` CLI mode that, after each EOD wrapper run, reads the day's newly-detected long-side tickers, calls Claude Opus 4.7 with `web_search` to produce a CANSLIM-flavored writeup per ticker, and writes a Markdown + standalone HTML report to `output/Reports/`. Soft-fail like Futu sync.

**Architecture:** New `report/` Python package with single-responsibility modules (`state`, `ranker`, `enrich`, `analyst`, `renderer`). System prompt under `prompts/canslim_system.md`, marked with prompt-cache. Async Anthropic SDK with `Semaphore(5)` concurrency. Wrapper scripts (`scripts/run_eod.sh`, `scripts/run_hk_eod.sh`) gain a tail call that does not break on failure.

**Tech Stack:** Python 3.12, anthropic SDK (web_search_20250305 tool), yfinance, markdown package, asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-05-07-canslim-report-design.md`

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Add `anthropic` and `markdown` dependencies |
| `report/__init__.py` | Package marker |
| `report/state.py` | Paths (`OUTPUT_REPORTS_DIR`), env-var helpers (`get_api_key`), priority constants, soft-fail logger |
| `report/ranker.py` | Read dated `.txt` files → priority-sort → cap 50 → return `(analyzed, truncated)` lists of `(ticker, group)` |
| `report/enrich.py` | Per-ticker yfinance fetch + RS percentile lookup → structured dict (incl. 3-year annual YoY) |
| `report/analyst.py` | Async Anthropic call with system-prompt cache + `web_search_20250305` tool + retry → markdown section per ticker |
| `report/renderer.py` | Sections → full Markdown doc; Markdown → standalone HTML doc; write both to disk |
| `report/__main__.py` | Entry point: orchestrates ranker → enrich → analyst → renderer; called by `main.py` mode dispatch |
| `prompts/canslim_system.md` | Static system prompt loaded once per run, injected with `cache_control` |
| `main.py` | Add `--mode report --market {us,hk} [--date YYYY-MM-DD]` branch |
| `scripts/run_eod.sh` | Replace `exec` with normal call; append report call (no-fail) |
| `scripts/run_hk_eod.sh` | Same as above |
| `tests/test_report_ranker.py` | Pure logic — input file fixtures → expected priority ordering and truncation |
| `tests/test_report_renderer.py` | Markdown render shape; HTML wrapper structure |
| `tests/test_report_enrich.py` | yfinance mocked; assert YoY math + null handling |
| `tests/test_report_analyst.py` | Anthropic SDK mocked; verify retry, missing-key skip, timeout handling |
| `CLAUDE.md` | Append "Daily CANSLIM Report" architecture section |

---

## Task 1: Add dependencies and create empty package skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `report/__init__.py`
- Create: `prompts/canslim_system.md` (placeholder)

- [ ] **Step 1: Add deps to `pyproject.toml`**

Modify the `dependencies = [...]` list to include `anthropic` and `markdown`:

```toml
[project]
name = "finviz-to-tv"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.40.0",
    "finviz>=2.0.0",
    "futu-api>=9.3.5308",
    "markdown>=3.6",
    "openpyxl>=3.1.5",
    "yfinance>=0.2.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
]
```

- [ ] **Step 2: Sync dependencies**

Run: `uv sync`
Expected: `Resolved N packages` and no errors. `uv.lock` updated.

- [ ] **Step 3: Create empty package marker**

Create `report/__init__.py` with content:

```python
"""Daily CANSLIM report generator. See docs/superpowers/specs/2026-05-07-canslim-report-design.md."""
```

- [ ] **Step 4: Create placeholder system prompt**

Create `prompts/canslim_system.md` with content:

```markdown
# CANSLIM Daily Report — System Prompt

You are an equity research assistant. For each ticker, produce a structured Markdown section using the EXACT template the user provides. Numeric/financial fields stay in English; qualitative analysis is in Chinese (Simplified). When data is missing, write "信息不足" and continue — never omit a section header. Use the `web_search` tool sparingly (max 3 per ticker) for: competitive moat, government/policy support, new products, recent catalysts.

Template (per ticker):

## $TICKER — Company Name (Exchange · Group)

**Snapshot**
- Market Cap: $X.XB | Price: $XX.XX | RS: 95
- EPS (latest Q): $X.XX (YoY +XX%) | Revenue (latest Q): $X.XB (YoY +XX%)
- Annual EPS YoY: FY-3 +XX% / FY-2 +XX% / FY-1 +XX%
- Annual Revenue YoY: FY-3 +XX% / FY-2 +XX% / FY-1 +XX%
- PE: XX | ROE: XX% | Inst. Hold: XX%
- Latest earnings date: YYYY-MM-DD

**公司速览**: 1-2 句业务描述

**基本面 / 财报**: 3 年年度 YoY + 最新季度趋势分析(加速/减速)

**竞争力**: 护城河、行业地位、主要竞争对手

**政策 / 政府支持**: 补贴、产业政策、监管利好/利空(港股/中概尤其重要)

**新产品 / 催化剂**: 近期或即将发布的产品、订单、合作

**风险点**: 2-3 条最关键的风险

**综合判断**: 1-2 句 CANSLIM 视角的多空倾向
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock report/__init__.py prompts/canslim_system.md
git commit -m "feat(report): add anthropic+markdown deps and package skeleton"
```

---

## Task 2: State module — paths, env helpers, constants

**Files:**
- Create: `report/state.py`
- Create: `tests/test_report_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_state.py`:

```python
import os
from pathlib import Path

import pytest

from report import state


def test_priority_order_is_complete():
    assert state.PRIORITY_ORDER == [
        "EarningsGap",
        "HighVolume",
        "Leaders",
        "GapUp",
        "NewHigh52W",
        "IPO",
        "TopGainers",
        "RS",
    ]


def test_max_tickers_is_50():
    assert state.MAX_TICKERS_PER_REPORT == 50


def test_get_api_key_returns_env_value(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123")
    assert state.get_api_key() == "sk-ant-test123"


def test_get_api_key_returns_none_when_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert state.get_api_key() is None


def test_reports_dir_is_under_project_root():
    assert state.OUTPUT_REPORTS_DIR.name == "Reports"
    assert state.OUTPUT_REPORTS_DIR.parent.name == "output"


def test_input_dir_for_market():
    us_dir = state.input_dir_for_market("us")
    hk_dir = state.input_dir_for_market("hk")
    assert us_dir.name == "US"
    assert hk_dir.name == "HK"
    assert us_dir.parent.name == "TV"


def test_input_dir_for_market_invalid():
    with pytest.raises(ValueError, match="market"):
        state.input_dir_for_market("uk")


def test_groups_for_us_includes_eight():
    assert state.groups_for_market("us") == [
        "EarningsGap", "HighVolume", "Leaders", "GapUp",
        "NewHigh52W", "IPO", "TopGainers", "RS",
    ]


def test_groups_for_hk_excludes_newhigh_topgainers():
    assert state.groups_for_market("hk") == [
        "EarningsGap", "HighVolume", "Leaders", "GapUp", "IPO", "RS",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report.state'` (or similar import error).

- [ ] **Step 3: Implement `report/state.py`**

```python
"""Paths, env-var access, and priority/group constants for the report mode."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_REPORTS_DIR = PROJECT_ROOT / "output" / "Reports"

PRIORITY_ORDER: list[str] = [
    "EarningsGap",
    "HighVolume",
    "Leaders",
    "GapUp",
    "NewHigh52W",
    "IPO",
    "TopGainers",
    "RS",
]

_HK_EXCLUDES = {"NewHigh52W", "TopGainers"}

MAX_TICKERS_PER_REPORT = 50


def get_api_key() -> str | None:
    """Return ANTHROPIC_API_KEY env var or None when unset."""
    return os.environ.get("ANTHROPIC_API_KEY")


def input_dir_for_market(market: str) -> Path:
    """Return the dated-.txt input directory for the given market ('us' or 'hk')."""
    market = market.lower()
    if market not in ("us", "hk"):
        raise ValueError(f"unknown market: {market!r} (expected 'us' or 'hk')")
    return PROJECT_ROOT / "output" / "TV" / market.upper()


def groups_for_market(market: str) -> list[str]:
    """Priority-ordered list of groups present for the given market."""
    market = market.lower()
    if market not in ("us", "hk"):
        raise ValueError(f"unknown market: {market!r}")
    if market == "us":
        return list(PRIORITY_ORDER)
    return [g for g in PRIORITY_ORDER if g not in _HK_EXCLUDES]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_report_state.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add report/state.py tests/test_report_state.py
git commit -m "feat(report): add state module with paths and priority constants"
```

---

## Task 3: Ranker module — read .txt files, prioritize, cap

**Files:**
- Create: `report/ranker.py`
- Create: `tests/test_report_ranker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_ranker.py`:

```python
from pathlib import Path

import pytest

from report import ranker


@pytest.fixture
def fake_us_dir(tmp_path: Path) -> Path:
    """Build a fake output/TV/US directory with dated .txt files."""
    d = tmp_path / "TV" / "US"
    d.mkdir(parents=True)
    # date stem matches "2026_05_07" used by the rest of the pipeline.
    files = {
        "2026_05_07_EarningsGap.txt": "NASDAQ:AAPL,NASDAQ:NVDA",
        "2026_05_07_HighVolume.txt": "NASDAQ:NVDA,NASDAQ:TSLA",  # NVDA dup
        "2026_05_07_Leaders.txt": "NASDAQ:META",
        "2026_05_07_GapUp.txt": "",
        "2026_05_07_NewHigh52W.txt": "NYSE:UBER",
        "2026_05_07_IPO.txt": "NASDAQ:RDDT",
        "2026_05_07_TopGainers.txt": "NASDAQ:PLTR",
        "2026_05_07_RS.txt": "NYSE:WMT",
    }
    for name, content in files.items():
        (d / name).write_text(content)
    return d


def test_read_dated_txt_returns_ticker_list(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("NASDAQ:AAPL,NASDAQ:NVDA,NYSE:UBER")
    assert ranker.read_dated_txt(p) == ["NASDAQ:AAPL", "NASDAQ:NVDA", "NYSE:UBER"]


def test_read_dated_txt_handles_empty(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("")
    assert ranker.read_dated_txt(p) == []


def test_read_dated_txt_missing_file_returns_empty(tmp_path: Path):
    assert ranker.read_dated_txt(tmp_path / "nonexistent.txt") == []


def test_read_dated_txt_strips_whitespace(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("  NASDAQ:AAPL , NASDAQ:NVDA  \n")
    assert ranker.read_dated_txt(p) == ["NASDAQ:AAPL", "NASDAQ:NVDA"]


def test_collect_market_groups(fake_us_dir):
    result = ranker.collect_market_groups(fake_us_dir, "2026_05_07", "us")
    # Returns dict[group_name -> list[ticker]] in priority order
    assert list(result.keys()) == [
        "EarningsGap", "HighVolume", "Leaders", "GapUp",
        "NewHigh52W", "IPO", "TopGainers", "RS",
    ]
    assert result["EarningsGap"] == ["NASDAQ:AAPL", "NASDAQ:NVDA"]
    assert result["GapUp"] == []


def test_rank_and_cap_priority_dedup(fake_us_dir):
    groups = ranker.collect_market_groups(fake_us_dir, "2026_05_07", "us")
    analyzed, truncated = ranker.rank_and_cap(groups, cap=50)
    tickers_only = [t for t, _ in analyzed]
    # NVDA appears in both EarningsGap and HighVolume; should land in the higher-priority one only.
    assert tickers_only.count("NASDAQ:NVDA") == 1
    # Order follows priority: EarningsGap first.
    assert analyzed[0] == ("NASDAQ:AAPL", "EarningsGap")
    assert analyzed[1] == ("NASDAQ:NVDA", "EarningsGap")
    assert analyzed[2] == ("NASDAQ:TSLA", "HighVolume")
    assert truncated == []


def test_rank_and_cap_truncates_at_limit(fake_us_dir):
    groups = ranker.collect_market_groups(fake_us_dir, "2026_05_07", "us")
    analyzed, truncated = ranker.rank_and_cap(groups, cap=3)
    assert len(analyzed) == 3
    assert len(truncated) == 4  # 7 unique total - 3 = 4
    # Cap honored.
    assert [t for t, _ in analyzed] == [
        "NASDAQ:AAPL", "NASDAQ:NVDA", "NASDAQ:TSLA",
    ]
    # Lowest-priority survivors fall into truncated.
    assert ("NYSE:WMT", "RS") in truncated


def test_rank_and_cap_empty():
    analyzed, truncated = ranker.rank_and_cap({}, cap=50)
    assert analyzed == []
    assert truncated == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report_ranker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report.ranker'`.

- [ ] **Step 3: Implement `report/ranker.py`**

```python
"""Read dated .txt files, dedup across groups by priority, cap to N tickers."""
from __future__ import annotations

from pathlib import Path

from report.state import groups_for_market

# (ticker_with_exchange_prefix, group_name)
TickerEntry = tuple[str, str]


def read_dated_txt(path: Path) -> list[str]:
    """Parse a TradingView .txt file (comma-separated, possibly with whitespace).
    Returns [] for missing or empty files."""
    if not path.is_file():
        return []
    raw = path.read_text().strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def collect_market_groups(
    input_dir: Path, date_stem: str, market: str
) -> dict[str, list[str]]:
    """Read all priority-ordered group files for the given market.
    Returns dict[group_name -> ticker list], with empty lists for missing files.
    Output dict preserves priority order (Python 3.7+ dict insertion order)."""
    result: dict[str, list[str]] = {}
    for group in groups_for_market(market):
        path = input_dir / f"{date_stem}_{group}.txt"
        result[group] = read_dated_txt(path)
    return result


def rank_and_cap(
    groups: dict[str, list[str]], cap: int
) -> tuple[list[TickerEntry], list[TickerEntry]]:
    """Walk groups in priority order; assign each ticker to the FIRST group it
    appears in. Take the first `cap` entries as `analyzed`; remainder is `truncated`.
    Both returned lists are in priority order."""
    seen: set[str] = set()
    ordered: list[TickerEntry] = []
    for group, tickers in groups.items():
        for ticker in tickers:
            if ticker in seen:
                continue
            seen.add(ticker)
            ordered.append((ticker, group))
    return ordered[:cap], ordered[cap:]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_report_ranker.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add report/ranker.py tests/test_report_ranker.py
git commit -m "feat(report): add ranker for priority-sort + 50-cap"
```

---

## Task 4: Enrich module — yfinance fetch + 3-year YoY math

**Files:**
- Create: `report/enrich.py`
- Create: `tests/test_report_enrich.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_enrich.py`:

```python
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from report import enrich


def _fake_quarterly_income_stmt() -> pd.DataFrame:
    """Columns are timestamps (most recent first), rows are line items."""
    cols = pd.to_datetime(["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30",
                           "2025-03-31"])
    data = {
        "TotalRevenue": [1100, 1000, 950, 900, 1000],
        "DilutedEPS":   [1.1,  1.0,  0.95, 0.90, 1.0],
    }
    return pd.DataFrame(data, index=cols).T


def _fake_annual_income_stmt() -> pd.DataFrame:
    """4 fiscal years (most recent first)."""
    cols = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])
    data = {
        "TotalRevenue": [4400, 4000, 3500, 3000],
        "DilutedEPS":   [4.40, 4.00, 3.50, 3.00],
    }
    return pd.DataFrame(data, index=cols).T


def test_compute_yoy_basic():
    assert enrich.compute_yoy(110, 100) == pytest.approx(10.0)


def test_compute_yoy_negative_prior_returns_none():
    assert enrich.compute_yoy(50, -10) is None


def test_compute_yoy_zero_prior_returns_none():
    assert enrich.compute_yoy(50, 0) is None


def test_compute_yoy_none_input_returns_none():
    assert enrich.compute_yoy(None, 100) is None
    assert enrich.compute_yoy(100, None) is None


def test_extract_annual_yoy_3y_revenue():
    df = _fake_annual_income_stmt()
    yoy = enrich.extract_annual_yoy_3y(df, "TotalRevenue")
    # FY-3 = 2023 vs 2022 = 16.67%; FY-2 = 2024 vs 2023 = 14.29%; FY-1 = 2025 vs 2024 = 10.0%
    assert yoy[0] == pytest.approx(16.67, rel=0.01)
    assert yoy[1] == pytest.approx(14.29, rel=0.01)
    assert yoy[2] == pytest.approx(10.0, rel=0.01)


def test_extract_annual_yoy_3y_too_few_rows():
    cols = pd.to_datetime(["2025-12-31", "2024-12-31"])
    df = pd.DataFrame({"TotalRevenue": [100, 90]}, index=cols).T
    yoy = enrich.extract_annual_yoy_3y(df, "TotalRevenue")
    assert yoy == [None, None, pytest.approx(11.11, rel=0.01)]


def test_latest_quarterly_with_yoy():
    df = _fake_quarterly_income_stmt()
    val, yoy = enrich.latest_quarterly_with_yoy(df, "TotalRevenue")
    assert val == 1100
    assert yoy == pytest.approx(10.0)


def test_fetch_ticker_data_handles_missing_yfinance_gracefully():
    """If yfinance raises during info fetch, we still return a partial dict."""
    fake_ticker = MagicMock()
    fake_ticker.info = {}  # empty
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker):
        data = enrich.fetch_ticker_data("AAPL", "EarningsGap", "NASDAQ", rs_lookup=lambda t: None)
    assert data["ticker"] == "AAPL"
    assert data["group"] == "EarningsGap"
    assert data["exchange"] == "NASDAQ"
    assert data["market_cap"] is None
    assert data["annual_revenue_yoy_3y"] == [None, None, None]


def test_fetch_ticker_data_full_path():
    fake_ticker = MagicMock()
    fake_ticker.info = {
        "longName": "Apple Inc.",
        "marketCap": 3_000_000_000_000,
        "currentPrice": 200.0,
        "previousClose": 198.0,
        "trailingPE": 30.0,
        "returnOnEquity": 1.5,
        "heldPercentInstitutions": 0.6,
    }
    fake_ticker.quarterly_income_stmt = _fake_quarterly_income_stmt()
    fake_ticker.income_stmt = _fake_annual_income_stmt()
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker):
        data = enrich.fetch_ticker_data("AAPL", "EarningsGap", "NASDAQ", rs_lookup=lambda t: 95)
    assert data["company_name"] == "Apple Inc."
    assert data["market_cap"] == 3_000_000_000_000
    assert data["last_price"] == 200.0
    assert data["pe_ratio"] == 30.0
    assert data["rs_percentile"] == 95
    assert data["revenue_latest_q"] == 1100
    assert data["revenue_latest_q_yoy_pct"] == pytest.approx(10.0)
    assert len(data["annual_revenue_yoy_3y"]) == 3
    assert data["annual_revenue_yoy_3y"][2] == pytest.approx(10.0, rel=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report.enrich'`.

- [ ] **Step 3: Implement `report/enrich.py`**

```python
"""Per-ticker yfinance fetch and 3-year YoY computation."""
from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def compute_yoy(current: float | None, prior: float | None) -> float | None:
    """Year-over-year percentage change. Returns None when prior is missing,
    zero, or negative (signs flip → meaningless)."""
    if current is None or prior is None:
        return None
    if prior <= 0:
        return None
    return (current - prior) / prior * 100.0


def _row_values(df: pd.DataFrame, row_label: str) -> list[float | None]:
    """Return a row of the income statement as floats (most recent first).
    yfinance frames are line-items × periods, so we look up the row by index label."""
    if df is None or df.empty or row_label not in df.index:
        return []
    series = df.loc[row_label]
    out: list[float | None] = []
    for v in series.tolist():
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(None)
    return out


def latest_quarterly_with_yoy(
    df: pd.DataFrame, row_label: str
) -> tuple[float | None, float | None]:
    """Latest quarter value + YoY vs same quarter last year (4 quarters back)."""
    values = _row_values(df, row_label)
    if not values:
        return (None, None)
    latest = values[0]
    prior = values[4] if len(values) > 4 else None
    return (latest, compute_yoy(latest, prior))


def extract_annual_yoy_3y(df: pd.DataFrame, row_label: str) -> list[float | None]:
    """Three YoY datapoints in [FY-3, FY-2, FY-1] order. yfinance annual frames
    have most-recent fiscal year first, so we reverse before pairing."""
    values = list(reversed(_row_values(df, row_label)))
    yoy: list[float | None] = []
    for i in range(-3, 0):
        try:
            current = values[i]
            prior = values[i - 1]
        except IndexError:
            yoy.append(None)
            continue
        yoy.append(compute_yoy(current, prior))
    return yoy


def fetch_ticker_data(
    ticker: str,
    group: str,
    exchange: str,
    rs_lookup: Callable[[str], int | None],
) -> dict[str, Any]:
    """Build the structured dict for one ticker. Per-field try/except so a
    yfinance schema drift on one attribute does not blank the whole record."""
    data: dict[str, Any] = {
        "ticker": ticker,
        "group": group,
        "exchange": exchange,
        "company_name": None,
        "market_cap": None,
        "last_price": None,
        "prev_close": None,
        "gap_pct": None,
        "pe_ratio": None,
        "roe": None,
        "institutional_holdings_pct": None,
        "eps_latest_q": None,
        "eps_latest_q_yoy_pct": None,
        "revenue_latest_q": None,
        "revenue_latest_q_yoy_pct": None,
        "annual_eps_yoy_3y": [None, None, None],
        "annual_revenue_yoy_3y": [None, None, None],
        "latest_earnings_date": None,
        "rs_percentile": None,
    }
    try:
        t = yf.Ticker(ticker)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: yf.Ticker construction failed: {e}")
        return data

    try:
        info = t.info or {}
        data["company_name"] = info.get("longName") or info.get("shortName")
        data["market_cap"] = info.get("marketCap")
        data["last_price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        data["prev_close"] = info.get("previousClose")
        if data["last_price"] and data["prev_close"]:
            data["gap_pct"] = (data["last_price"] - data["prev_close"]) / data["prev_close"] * 100.0
        data["pe_ratio"] = info.get("trailingPE")
        roe = info.get("returnOnEquity")
        data["roe"] = roe * 100.0 if isinstance(roe, (int, float)) else None
        inst = info.get("heldPercentInstitutions")
        data["institutional_holdings_pct"] = inst * 100.0 if isinstance(inst, (int, float)) else None
        data["latest_earnings_date"] = info.get("lastFiscalYearEnd")
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: info access failed: {e}")

    try:
        qdf = t.quarterly_income_stmt
        eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, "DilutedEPS")
        if eps_val is None:
            eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, "BasicEPS")
        data["eps_latest_q"] = eps_val
        data["eps_latest_q_yoy_pct"] = eps_yoy
        rev_val, rev_yoy = latest_quarterly_with_yoy(qdf, "TotalRevenue")
        data["revenue_latest_q"] = rev_val
        data["revenue_latest_q_yoy_pct"] = rev_yoy
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: quarterly fetch failed: {e}")

    try:
        adf = t.income_stmt
        data["annual_eps_yoy_3y"] = extract_annual_yoy_3y(adf, "DilutedEPS")
        if all(v is None for v in data["annual_eps_yoy_3y"]):
            data["annual_eps_yoy_3y"] = extract_annual_yoy_3y(adf, "BasicEPS")
        data["annual_revenue_yoy_3y"] = extract_annual_yoy_3y(adf, "TotalRevenue")
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: annual fetch failed: {e}")

    try:
        data["rs_percentile"] = rs_lookup(ticker)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: RS lookup failed: {e}")

    return data
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_report_enrich.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add report/enrich.py tests/test_report_enrich.py
git commit -m "feat(report): yfinance enrichment with 3-year annual YoY"
```

---

## Task 5: Analyst module — async Anthropic + web_search + retry

**Files:**
- Create: `report/analyst.py`
- Create: `tests/test_report_analyst.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_analyst.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from report import analyst


@pytest.fixture
def fake_data():
    return {
        "ticker": "AAPL",
        "group": "EarningsGap",
        "exchange": "NASDAQ",
        "company_name": "Apple Inc.",
        "market_cap": 3_000_000_000_000,
        "last_price": 200.0,
        "prev_close": 198.0,
        "gap_pct": 1.01,
        "pe_ratio": 30.0,
        "roe": 150.0,
        "institutional_holdings_pct": 60.0,
        "eps_latest_q": 1.1,
        "eps_latest_q_yoy_pct": 10.0,
        "revenue_latest_q": 1.1e9,
        "revenue_latest_q_yoy_pct": 10.0,
        "annual_eps_yoy_3y": [16.67, 14.29, 10.0],
        "annual_revenue_yoy_3y": [16.67, 14.29, 10.0],
        "latest_earnings_date": "2026-03-31",
        "rs_percentile": 95,
    }


def test_build_user_message_includes_ticker_and_data(fake_data):
    msg = analyst.build_user_message(fake_data)
    assert "AAPL" in msg
    assert "EarningsGap" in msg
    assert "Apple Inc." in msg
    # YoY arrays serialized
    assert "16.67" in msg or "16.7" in msg


def _make_anthropic_response(text: str):
    """Mock the SDK response shape: response.content[*].text."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"
    return response


@pytest.mark.asyncio
async def test_analyze_ticker_success(fake_data):
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_make_anthropic_response("## AAPL\n\n**Snapshot**...")
    )
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<system prompt>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "## AAPL" in section
    fake_client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_ticker_retries_on_5xx(fake_data):
    import anthropic
    fake_client = MagicMock()
    failure = anthropic.APIStatusError(
        message="server error",
        response=MagicMock(status_code=503),
        body=None,
    )
    fake_client.messages.create = AsyncMock(
        side_effect=[failure, _make_anthropic_response("## AAPL\nfine")]
    )
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<sp>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "## AAPL" in section
    assert fake_client.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_analyze_ticker_returns_failure_section_after_retry_exhausted(fake_data):
    import anthropic
    fake_client = MagicMock()
    failure = anthropic.APIConnectionError(request=MagicMock())
    fake_client.messages.create = AsyncMock(side_effect=[failure, failure])
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<sp>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "AAPL" in section
    assert "分析失败" in section


def test_extract_text_handles_mixed_blocks():
    """Web-search responses include server-tool-use blocks; we want concatenated text only."""
    text_block = MagicMock(type="text", text="Hello.")
    tool_block = MagicMock(type="server_tool_use")
    other_text = MagicMock(type="text", text=" World.")
    response = MagicMock()
    response.content = [text_block, tool_block, other_text]
    assert analyst._extract_text(response) == "Hello. World."
```

Add `pytest-asyncio` to dev deps for the `@pytest.mark.asyncio` decorator. (See step 3.)

- [ ] **Step 2: Add `pytest-asyncio` to dev deps and re-sync**

Modify `pyproject.toml` `[dependency-groups]` block:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]
```

Add a pytest config so the asyncio mode is on by default. Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Run: `uv sync`
Expected: resolves cleanly.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_report_analyst.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report.analyst'`.

- [ ] **Step 4: Implement `report/analyst.py`**

```python
"""Async Anthropic call with web_search tool, system-prompt cache, and retry."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 1500
WEB_SEARCH_MAX_USES = 3
RETRY_BACKOFF_SECONDS = 5.0
PER_CALL_TIMEOUT_SECONDS = 90.0


def build_user_message(data: dict[str, Any]) -> str:
    """Serialize the enrichment dict as a structured prompt for the model."""
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    return (
        f"Ticker: {data['ticker']}  |  Group: {data['group']}  |  Exchange: {data['exchange']}\n\n"
        f"Structured data (use these numbers verbatim in the Snapshot block; for any field that is "
        f"null, write '信息不足' in the qualitative analysis):\n\n```json\n{payload}\n```\n\n"
        f"Generate the Markdown section per the template in the system prompt. Use the web_search "
        f"tool sparingly (≤3 calls) for the qualitative legs."
    )


def _extract_text(response: Any) -> str:
    """Concatenate all text blocks in the response, ignoring tool_use blocks."""
    parts: list[str] = []
    for block in response.content or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts).strip()


async def analyze_ticker(
    client: Any,
    system_prompt: str,
    data: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> str:
    """Call Anthropic for one ticker. On retry exhaustion, return a placeholder
    Markdown section so the renderer never sees a missing entry."""
    user_msg = build_user_message(data)
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            async with semaphore:
                response = await asyncio.wait_for(
                    client.messages.create(
                        model=MODEL,
                        max_tokens=MAX_TOKENS,
                        system=[
                            {
                                "type": "text",
                                "text": system_prompt,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        tools=[
                            {
                                "type": "web_search_20250305",
                                "name": "web_search",
                                "max_uses": WEB_SEARCH_MAX_USES,
                            }
                        ],
                        messages=[{"role": "user", "content": user_msg}],
                    ),
                    timeout=PER_CALL_TIMEOUT_SECONDS,
                )
            text = _extract_text(response)
            if not text:
                raise RuntimeError("empty response")
            return text
        except (
            anthropic.APIStatusError,
            anthropic.APIConnectionError,
            asyncio.TimeoutError,
            RuntimeError,
        ) as e:
            last_error = e
            logger.warning(
                f"[analyst] {data['ticker']}: attempt {attempt} failed: "
                f"{type(e).__name__}: {e}"
            )
            if attempt == 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
    return (
        f"## {data['ticker']} — {data.get('company_name') or '?'} "
        f"({data['exchange']} · {data['group']})\n\n"
        f"[分析失败: {type(last_error).__name__}: {last_error}]\n"
    )
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/test_report_analyst.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add report/analyst.py tests/test_report_analyst.py pyproject.toml uv.lock
git commit -m "feat(report): async Anthropic analyst with web_search and retry"
```

---

## Task 6: Renderer module — Markdown doc + standalone HTML

**Files:**
- Create: `report/renderer.py`
- Create: `tests/test_report_renderer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report_renderer.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report_renderer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report.renderer'`.

- [ ] **Step 3: Implement `report/renderer.py`**

```python
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
        f"**Total new tickers:** {total} "
        f"(analyzed {analyzed_count}, truncated {len(truncated)})\n"
    )
    parts.append(f"**Generated:** {generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
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
    md_path.write_text(markdown_text)
    html_path.write_text(markdown_to_html(markdown_text, page_title))
    return md_path, html_path
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_report_renderer.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add report/renderer.py tests/test_report_renderer.py
git commit -m "feat(report): markdown + standalone-html renderer"
```

---

## Task 7: Orchestrator (`report/__main__.py`) and CLI dispatch

**Files:**
- Create: `report/__main__.py`
- Modify: `main.py:1162` (`--mode` choices) and add a new dispatch branch
- Test: covered by manual end-to-end in Task 9

- [ ] **Step 1: Implement `report/__main__.py`**

Create `report/__main__.py`:

```python
"""Orchestrator for `python -m report` / `main.py --mode report`.

Read dated .txt files for the given market+date, prioritize and cap, enrich each
ticker with yfinance + RS table, fan out async Claude calls, render and write
the .md + .html artifacts. Soft-fail on any unexpected error."""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic

from report import analyst, enrich, ranker, renderer
from report.state import (
    MAX_TICKERS_PER_REPORT,
    OUTPUT_REPORTS_DIR,
    PROJECT_ROOT,
    get_api_key,
    input_dir_for_market,
)

logger = logging.getLogger(__name__)
HKT = ZoneInfo("Asia/Hong_Kong")
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts" / "canslim_system.md"


def _split_exchange_ticker(qualified: str) -> tuple[str, str]:
    """`NASDAQ:AAPL` -> `('NASDAQ', 'AAPL')`. HK format `HKEX:00700` keeps the leading zeros."""
    if ":" in qualified:
        ex, sym = qualified.split(":", 1)
        return ex, sym
    return ("", qualified)


def _yf_ticker(symbol: str, market: str) -> str:
    """yfinance expects bare US tickers and `<5-digit>.HK` for Hong Kong."""
    if market == "hk":
        return f"{symbol.lstrip('0').zfill(4)}.HK"
    return symbol


def _load_rs_lookup(market: str, date_stem: str):
    """Return a callable `(ticker) -> percentile or None` reading the cached
    RS table written by rs_rating.py / hk_rs.py during the EOD run.
    Missing cache → all-None lookup (the report still runs, just without RS field)."""
    state_dir = PROJECT_ROOT / "output" / "state"
    candidates = [
        state_dir / f"rs_rating_{date_stem.replace('_', '-')}.csv",
        state_dir / f"rs_rating_{date_stem}.csv",
        state_dir / f"hk_rs_rating_{date_stem.replace('_', '-')}.csv",
        state_dir / f"hk_rs_rating_{date_stem}.csv",
    ]
    table: dict[str, int] = {}
    for path in candidates:
        if not path.is_file():
            continue
        if (market == "us" and "hk_rs_rating" in path.name) or (
            market == "hk" and path.name.startswith("rs_rating_")
        ):
            continue
        try:
            with path.open() as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    sym = row.get("ticker") or row.get("Ticker")
                    pct = row.get("percentile") or row.get("rs_percentile")
                    if sym and pct:
                        try:
                            table[sym.upper()] = int(float(pct))
                        except ValueError:
                            continue
            break
        except Exception as e:
            logger.warning(f"[report] failed to read RS cache {path}: {e}")
    if not table:
        logger.info(f"[report] no RS cache found for {market} {date_stem}; field will be null")
    return lambda t: table.get(t.upper())


async def _run_async(market: str, date_stem: str, date_iso: str) -> int:
    api_key = get_api_key()
    if not api_key:
        logger.warning("[report] ANTHROPIC_API_KEY not set; skipping report generation")
        return 0

    input_dir = input_dir_for_market(market)
    groups = ranker.collect_market_groups(input_dir, date_stem, market)
    total = sum(len(v) for v in groups.values())
    if total == 0:
        logger.info(f"[report] no tickers in any {market.upper()} group for {date_stem}; skip")
        return 0

    analyzed_entries, truncated_entries = ranker.rank_and_cap(
        groups, cap=MAX_TICKERS_PER_REPORT
    )
    logger.info(
        f"[report] {market.upper()} {date_stem}: "
        f"{len(analyzed_entries)} to analyze, {len(truncated_entries)} truncated"
    )

    rs_lookup = _load_rs_lookup(market, date_stem)

    # Enrich (sequential — yfinance is the bottleneck and parallel pulls trip rate limits).
    enriched: list[dict] = []
    for qualified, group in analyzed_entries:
        exchange, symbol = _split_exchange_ticker(qualified)
        yf_sym = _yf_ticker(symbol, market)
        try:
            data = enrich.fetch_ticker_data(yf_sym, group, exchange, rs_lookup)
        except Exception as e:
            logger.warning(f"[report] enrich failed for {qualified}: {e}")
            data = {
                "ticker": yf_sym, "group": group, "exchange": exchange,
                "company_name": None, "market_cap": None, "last_price": None,
                "prev_close": None, "gap_pct": None, "pe_ratio": None, "roe": None,
                "institutional_holdings_pct": None, "eps_latest_q": None,
                "eps_latest_q_yoy_pct": None, "revenue_latest_q": None,
                "revenue_latest_q_yoy_pct": None,
                "annual_eps_yoy_3y": [None, None, None],
                "annual_revenue_yoy_3y": [None, None, None],
                "latest_earnings_date": None, "rs_percentile": None,
            }
        enriched.append(data)

    if not SYSTEM_PROMPT_PATH.is_file():
        logger.error(f"[report] system prompt missing at {SYSTEM_PROMPT_PATH}")
        return 0
    system_prompt = SYSTEM_PROMPT_PATH.read_text()

    client = anthropic.AsyncAnthropic(api_key=api_key)
    semaphore = asyncio.Semaphore(5)
    coroutines = [
        analyst.analyze_ticker(client, system_prompt, data, semaphore)
        for data in enriched
    ]
    sections = await asyncio.gather(*coroutines)

    md_text = renderer.render_markdown(
        market=market,
        date_iso=date_iso,
        analyzed_count=len(analyzed_entries),
        truncated=[(q.split(":", 1)[-1], g) for q, g in truncated_entries],
        sections=sections,
        generated_at=datetime.now(HKT),
    )
    md_path, html_path = renderer.write_report_files(
        out_dir=OUTPUT_REPORTS_DIR,
        date_stem=date_stem,
        market=market,
        markdown_text=md_text,
        page_title=f"Scan Report — {date_iso} ({market.upper()})",
    )
    logger.info(f"[report] wrote {md_path}")
    logger.info(f"[report] wrote {html_path}")
    return 0


def run(market: str, override_date: str | None = None) -> int:
    """Entry point invoked by main.py. Soft-fails on any exception."""
    try:
        if override_date:
            d = date.fromisoformat(override_date)
        else:
            d = datetime.now(HKT).date()
        date_stem = d.strftime("%Y_%m_%d")
        date_iso = d.isoformat()
        return asyncio.run(_run_async(market.lower(), date_stem, date_iso))
    except Exception as e:
        logger.exception(f"[report] aborted: {e}")
        return 0


def _cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["us", "hk"], required=True)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today HKT")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    return run(args.market, args.date)


if __name__ == "__main__":
    sys.exit(_cli())
```

- [ ] **Step 2: Modify `main.py` to register the new mode**

Edit `main.py:1162` to extend the `--mode` choices and help text:

```python
    parser.add_argument(
        "--mode",
        choices=["eod", "us-eod", "hk-eod", "morning-gap", "report"],
        default="eod",
        help="eod: full end-of-day run (US + HK). "
             "us-eod: US only (Longs/Leaders/Shorts/RS/IPO) — for the morning HKT slot when HK market is mid-session. "
             "hk-eod: HK only (Shorts + Longs/Leaders/RS) — for the evening HKT slot after HK market closes. "
             "morning-gap: intraday gap-up scanner. "
             "report: generate CANSLIM Markdown+HTML report from today's dated .txt files (requires --market).",
    )
    parser.add_argument(
        "--market", choices=["us", "hk"],
        help="Required when --mode=report. Selects which market's .txt files to analyze.",
    )
    parser.add_argument(
        "--date",
        help="Optional YYYY-MM-DD override for --mode=report (default: today HKT).",
    )
```

Then add a dispatch branch immediately after the existing `if args.mode == "hk-eod":` block (around `main.py:1247`, just before the `if args.mode in ("eod", "us-eod"):` block):

```python
    if args.mode == "report":
        if not args.market:
            logger.error("--mode=report requires --market {us,hk}")
            return 1
        from report.__main__ import run as run_report
        return run_report(args.market, args.date)
```

- [ ] **Step 3: Smoke-run the CLI without API key**

Run: `unset ANTHROPIC_API_KEY && uv run main.py --mode report --market us --date 2026-05-07`
Expected: log line `[report] ANTHROPIC_API_KEY not set; skipping report generation`, exit code 0, no crash.

- [ ] **Step 4: Smoke-run unit tests still all pass**

Run: `uv run pytest tests/ -v`
Expected: all green (existing + new report tests).

- [ ] **Step 5: Commit**

```bash
git add report/__main__.py main.py
git commit -m "feat(report): wire --mode report --market dispatch + orchestrator"
```

---

## Task 8: Wrapper script integration (US + HK)

**Files:**
- Modify: `scripts/run_eod.sh`
- Modify: `scripts/run_hk_eod.sh`

The current wrappers use `exec /Users/xue/.local/bin/uv run ... main.py --mode <eod>`. `exec` replaces the shell, so any post-EOD command is unreachable. We replace the `exec` with a normal call and append a soft-fail report invocation.

- [ ] **Step 1: Modify `scripts/run_eod.sh`**

Replace the final two lines (the `exec >> "$LOG" 2>&1` and the `exec /Users/xue/.local/bin/uv run ...` block at the end of the file) with:

```bash
exec >> "$LOG" 2>&1

UV=/Users/xue/.local/bin/uv
PROJECT=/Users/xue/finviz_to_tv

"$UV" run --directory "$PROJECT" main.py --mode us-eod
EOD_STATUS=$?

# Report is a soft side-effect; failures here must not turn the EOD run red.
set +e
"$UV" run --directory "$PROJECT" main.py --mode report --market us
set -e

exit $EOD_STATUS
```

- [ ] **Step 2: Modify `scripts/run_hk_eod.sh`**

Same edit pattern — replace the `exec /Users/xue/.local/bin/uv run ...` ending with:

```bash
exec >> "$LOG" 2>&1

UV=/Users/xue/.local/bin/uv
PROJECT=/Users/xue/finviz_to_tv

"$UV" run --directory "$PROJECT" main.py --mode hk-eod
EOD_STATUS=$?

set +e
"$UV" run --directory "$PROJECT" main.py --mode report --market hk
set -e

exit $EOD_STATUS
```

- [ ] **Step 3: Verify scripts are executable and syntactically valid**

Run: `bash -n scripts/run_eod.sh && bash -n scripts/run_hk_eod.sh && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/run_eod.sh scripts/run_hk_eod.sh
git commit -m "feat(report): wrapper scripts call --mode report after EOD (soft-fail)"
```

---

## Task 9: Manual end-to-end with real Anthropic API

This task is interactive and ALSO produces real `.md` and `.html` artifacts. Skip if no API key is available; the test suite already covers the code paths in mocks.

- [ ] **Step 1: Confirm `ANTHROPIC_API_KEY` is set**

Run: `echo "${ANTHROPIC_API_KEY:0:10}..."`
Expected: prints first 10 chars of the key (e.g. `sk-ant-api...`).

- [ ] **Step 2: Choose a recent date with non-empty .txt files**

Run: `ls output/TV/US/*_Leaders.txt | tail -3`
Pick the most recent dated stem (e.g. `2026_05_06`).

- [ ] **Step 3: Run report mode**

Run: `uv run main.py --mode report --market us --date 2026-05-06`
Expected:
- Log lines: `[report] US 2026_05_06: N to analyze, M truncated`
- Per-ticker `[analyst]` warnings only on retries (most should succeed silently)
- Two final lines: `[report] wrote .../2026_05_06_us.md` and `..._us.html`
- Exit code 0

- [ ] **Step 4: Visually inspect outputs**

Open both files:

```bash
open output/Reports/2026_05_06_us.md
open output/Reports/2026_05_06_us.html
```

Verify:
- HTML renders with the inline CSS, readable in a browser
- Each ticker section has all 7 sub-sections + Snapshot block
- Numeric fields populated (Market Cap, EPS, Revenue, YoY arrays, PE, ROE)
- Qualitative sections in Chinese, with concrete content (not boilerplate)
- Truncated section present iff more than 50 tickers existed

- [ ] **Step 5: No commit**

This task is a smoke test; nothing to commit.

---

## Task 10: Document in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (append a new architecture section)

- [ ] **Step 1: Edit `CLAUDE.md`**

Find the "## Architecture" section. After the morning-gap entry (and before the "**Key mechanisms:**" subsection), the architecture intro paragraph at the top mentions `notify.py` etc — extend that intro line to also list the report module. More importantly, add a new screener-group-style entry for the report:

In the "**Thirteen screener groups**" section, change "Thirteen" to "Thirteen (plus a daily research report)". After the `[hk_settings]` HK Long-side block, before the `[morning_gap]` Morning Gap block, insert:

```markdown
- **Daily CANSLIM Report** (`--mode report --market {us,hk}`; no config block): Reads today's dated `.txt` files for the chosen market and produces a per-ticker fundamentals + outlook brief via Claude Opus 4.7 with the `web_search_20250305` tool. Output: `output/Reports/<date>_{us,hk}.md` and a self-contained `<date>_{us,hk}.html` (inline CSS, no external assets). Inputs: 8 US long-side files (EarningsGap, HighVolume, Leaders, GapUp, NewHigh52W, IPO, TopGainers, RS) or 6 HK files (same minus NewHigh52W and TopGainers). Capped at 50 tickers/market/day with priority `EarningsGap > HighVolume > Leaders > GapUp > NewHigh52W > IPO > TopGainers > RS`; overflow listed in a "Truncated" section. Triggered by `scripts/run_eod.sh` (US, after `--mode us-eod`) and `scripts/run_hk_eod.sh` (HK, after `--mode hk-eod`). **Soft-fail like Futu sync** — wrapper exit code reflects only the EOD step, not the report step. Requires `ANTHROPIC_API_KEY` env var; missing → step skipped with a warning, `.txt` artifacts unaffected. Cost envelope: ~$0.13–0.25/ticker (Opus 4.7 + web_search), ~$10–15 typical day, $25/day cap (50 × 2 markets × $0.25). Per-ticker structured data (Market Cap, EPS, Revenue, **3-year annual YoY**, PE, ROE, RS percentile, latest earnings date) comes from yfinance; qualitative analysis (competitive moat, government/policy support, new products, catalysts, risks, bottom-line) comes from the model with up to 3 web_search calls. Snapshot fields stay in English/numbers; qualitative sections are in Chinese. Shorts, HK Shorts, and Morning Gap are intentionally excluded (technical/intraday plays, fundamentals are not the deciding signal).
```

- [ ] **Step 2: Verify file structure**

Run: `grep -n "Daily CANSLIM Report" CLAUDE.md`
Expected: one match.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): document --mode report and Reports/ artifacts"
```

---

## Self-Review (writer's notes)

**Spec coverage check:**
- ✅ Two markets (`--market us|hk`) with separate runs — Task 7 (CLI) + Task 8 (wrappers)
- ✅ Eight US groups / six HK groups — Task 2 (`groups_for_market`)
- ✅ Priority order `EarningsGap > HighVolume > Leaders > GapUp > NewHigh52W > IPO > TopGainers > RS` — Task 2 (`PRIORITY_ORDER`), tested in Task 3
- ✅ 50-ticker cap with truncation list — Task 3
- ✅ yfinance enrichment incl. 3-year annual YoY — Task 4
- ✅ Opus 4.7, web_search_20250305, prompt cache, 5-way concurrency, retry — Task 5
- ✅ Markdown + standalone HTML output, both files written locally — Task 6
- ✅ `ANTHROPIC_API_KEY` env-var contract, missing → skip — Task 7 (`get_api_key` check)
- ✅ Soft-fail wrappers — Task 8
- ✅ CLAUDE.md update — Task 10

**Things explicitly NOT in this plan (per spec out-of-scope):** email delivery, attachment handling, cross-day report dedup, charting, Shorts / HK Shorts / Morning Gap analysis.

**Risk note:** Task 9 (real API call) requires manual inspection. If Anthropic SDK signatures drift between releases, the analyst module's `messages.create(...)` kwargs may need adjustment — the tests in Task 5 mock the client so won't catch SDK drift; rely on Task 9 smoke run.
