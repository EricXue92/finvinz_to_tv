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
    """6 fiscal years (most recent first) — enough for full 5-YoY extraction."""
    cols = pd.to_datetime([
        "2025-12-31", "2024-12-31", "2023-12-31",
        "2022-12-31", "2021-12-31", "2020-12-31",
    ])
    data = {
        "TotalRevenue": [4400, 4000, 3500, 3000, 2500, 2000],
        "DilutedEPS":   [4.40, 4.00, 3.50, 3.00, 2.50, 2.00],
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


def test_row_values_accepts_label_tuple_and_picks_first_match():
    """Real yfinance uses 'Total Revenue' (with space); the lookup must accept
    a tuple of fallback labels and pick whichever exists in the frame."""
    cols = pd.to_datetime(["2025-12-31", "2024-12-31"])
    df = pd.DataFrame({"TotalRevenue": [100, 90]}, index=cols).T
    assert enrich._row_values(df, ("Total Revenue", "TotalRevenue")) == [100.0, 90.0]


def test_row_values_label_tuple_picks_space_form_when_present():
    cols = pd.to_datetime(["2025-12-31", "2024-12-31"])
    df = pd.DataFrame({"Total Revenue": [120, 100]}, index=cols).T
    assert enrich._row_values(df, ("Total Revenue", "TotalRevenue")) == [120.0, 100.0]


def test_fetch_ticker_data_uses_space_form_yfinance_labels():
    """Regression: real yfinance returns 'Diluted EPS' / 'Total Revenue' with spaces.
    Earlier impl only tried 'DilutedEPS'/'TotalRevenue' and silently produced 信息不足."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "T", "currentPrice": 100, "previousClose": 99}
    qcols = pd.to_datetime(["2026-03-31", "2025-12-31", "2025-09-30",
                            "2025-06-30", "2025-03-31"])
    fake_ticker.quarterly_income_stmt = pd.DataFrame(
        {"Total Revenue": [110, 100, 95, 90, 100],
         "Diluted EPS": [1.1, 1.0, 0.95, 0.90, 1.0]},
        index=qcols,
    ).T
    acols = pd.to_datetime([
        "2025-12-31", "2024-12-31", "2023-12-31",
        "2022-12-31", "2021-12-31", "2020-12-31",
    ])
    fake_ticker.income_stmt = pd.DataFrame(
        {"Total Revenue": [4400, 4000, 3500, 3000, 2500, 2000],
         "Diluted EPS": [4.40, 4.00, 3.50, 3.00, 2.50, 2.00]},
        index=acols,
    ).T
    fake_ticker.earnings_dates = None
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker):
        data = enrich.fetch_ticker_data("T", "Leaders", "NYSE", rs_lookup=lambda t: 90)
    assert data["revenue_latest_q"] == 110
    assert data["eps_latest_q"] == 1.1
    # Frame has 6 fiscal years → 5 YoY datapoints, filling the full 5-slot array.
    assert data["annual_revenue_yoy_5y"][-1] == pytest.approx(10.0, rel=0.01)
    assert data["annual_eps_yoy_5y"][-1] == pytest.approx(10.0, rel=0.01)
    # Oldest-to-newest YoY pairs: 2000→2500 = +25%, then 2500→3000, 3000→3500,
    # 3500→4000, 4000→4400. So slot [0] is the oldest pair = +25%.
    assert data["annual_revenue_yoy_5y"][0] == pytest.approx(25.0, rel=0.01)


def test_extract_quarterly_yoy_4q_full_history():
    """8 quarters of data → 4 valid YoY datapoints (oldest→newest)."""
    cols = pd.to_datetime(
        ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30",
         "2025-03-31", "2024-12-31", "2024-09-30", "2024-06-30"]
    )
    df = pd.DataFrame(
        {"TotalRevenue": [110, 105, 100, 95, 100, 95, 90, 80]}, index=cols
    ).T
    yoy, labels = enrich.extract_quarterly_yoy(df, "TotalRevenue", 4)
    # Oldest first: Q -3 (Jun '25 vs Jun '24) … Latest (Mar '26 vs Mar '25)
    # Jun '25 (95) vs Jun '24 (80) = +18.75%
    assert yoy[0] == pytest.approx(18.75, rel=0.01)
    # Sep '25 (100) vs Sep '24 (90) = +11.11%
    assert yoy[1] == pytest.approx(11.11, rel=0.01)
    # Dec '25 (105) vs Dec '24 (95) = +10.53%
    assert yoy[2] == pytest.approx(10.53, rel=0.01)
    # Mar '26 (110) vs Mar '25 (100) = +10.0%
    assert yoy[3] == pytest.approx(10.0, rel=0.01)
    # Period labels populated, oldest first.
    assert labels[0] == "Jun'25"
    assert labels[3] == "Mar'26"


def test_extract_quarterly_yoy_4q_partial_history():
    """5 quarters of data → only the most recent quarter has a YoY pair."""
    cols = pd.to_datetime(
        ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]
    )
    df = pd.DataFrame({"TotalRevenue": [110, 105, 100, 95, 100]}, index=cols).T
    yoy, labels = enrich.extract_quarterly_yoy(df, "TotalRevenue", 4)
    assert yoy[:3] == [None, None, None]
    assert yoy[3] == pytest.approx(10.0, rel=0.01)
    assert labels[3] == "Mar'26"


def test_extract_quarterly_yoy_4q_empty_frame():
    yoy, labels = enrich.extract_quarterly_yoy(pd.DataFrame(), "TotalRevenue", 4)
    assert yoy == [None, None, None, None]
    assert labels == ["", "", "", ""]


def test_extract_annual_yoy_default_5_years_with_partial_history():
    cols = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31"])
    df = pd.DataFrame({"TotalRevenue": [120, 100, 90]}, index=cols).T
    yoy = enrich.extract_annual_yoy(df, "TotalRevenue", years_back=5)
    # 3 fiscal years = 2 YoY pairs; older 3 slots are None.
    assert yoy == [None, None, None,
                   pytest.approx(11.11, rel=0.01),
                   pytest.approx(20.0, rel=0.01)]


def test_extract_annual_yoy_full_5_years():
    cols = pd.to_datetime(
        ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31", "2020-12-31"]
    )
    df = pd.DataFrame(
        {"TotalRevenue": [600, 500, 400, 300, 250, 200]}, index=cols
    ).T
    yoy = enrich.extract_annual_yoy(df, "TotalRevenue", years_back=5)
    assert len(yoy) == 5
    # Oldest first: 250→300 +20%, 300→400 +33.3%, 400→500 +25%, 500→600 +20%
    assert yoy[0] == pytest.approx(25.0, rel=0.01)   # 200→250
    assert yoy[-1] == pytest.approx(20.0, rel=0.01)  # 500→600


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
    assert data["annual_revenue_yoy_5y"] == [None, None, None, None, None]
    assert data["institutional_holdings_pct"] is None


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
    assert data["institutional_holdings_pct"] == pytest.approx(60.0)
    assert data["rs_percentile"] == 95
    assert data["revenue_latest_q"] == 1100
    assert data["revenue_latest_q_yoy_pct"] == pytest.approx(10.0)
    assert len(data["annual_revenue_yoy_5y"]) == 5
    assert data["annual_revenue_yoy_5y"][-1] == pytest.approx(10.0, rel=0.01)


def test_fetch_ticker_data_gap_pct_handles_zero_prev_close():
    """Penny-stock or data-error case: prev_close=0 must not crash or compute spurious gap."""
    fake_ticker = MagicMock()
    fake_ticker.info = {
        "longName": "Zero Co.",
        "currentPrice": 5.0,
        "previousClose": 0.0,
    }
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker):
        data = enrich.fetch_ticker_data("ZERO", "GapUp", "NASDAQ", rs_lookup=lambda t: None)
    assert data["last_price"] == 5.0
    assert data["prev_close"] == 0.0
    assert data["gap_pct"] is None  # not computed because prev_close is not > 0
