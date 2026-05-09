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


def test_compute_yoy_negative_prior_uses_abs_denominator():
    # Loss → profit: prior -10, current +5 → (5 - -10)/abs(-10) = +150% (big improvement)
    assert enrich.compute_yoy(5, -10) == pytest.approx(150.0)
    # Loss narrowing: prior -10, current -5 → +50% (got better)
    assert enrich.compute_yoy(-5, -10) == pytest.approx(50.0)
    # Loss widening: prior -10, current -20 → -100% (got worse)
    assert enrich.compute_yoy(-20, -10) == pytest.approx(-100.0)


def test_compute_yoy_profit_to_loss_negative():
    # prior +10, current -5 → -150% (sign-flip into loss, big swing)
    assert enrich.compute_yoy(-5, 10) == pytest.approx(-150.0)


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
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None):
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
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None):
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
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None):
        data = enrich.fetch_ticker_data("AAPL", "EarningsGap", "NASDAQ", rs_lookup=lambda t: 95)
    assert data["company_name"] == "Apple Inc."
    assert data["market_cap"] == 3_000_000_000_000
    assert data["last_price"] == 200.0
    assert data["institutional_holdings_pct"] == pytest.approx(60.0)
    assert data["roe_pct"] == pytest.approx(150.0)  # 1.5 → 150%
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
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None):
        data = enrich.fetch_ticker_data("ZERO", "GapUp", "NASDAQ", rs_lookup=lambda t: None)
    assert data["last_price"] == 5.0
    assert data["prev_close"] == 0.0
    assert data["gap_pct"] is None  # not computed because prev_close is not > 0


def test_fetch_ticker_data_roe_missing_returns_none():
    """yfinance can return info dicts where returnOnEquity is absent or NaN
    (newly-IPO'd, foreign listings without ROE coverage). Should produce
    `roe_pct = None`, not crash."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "No ROE Co.", "currentPrice": 10.0}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None):
        data = enrich.fetch_ticker_data("XYZ", "Leaders", "NASDAQ", rs_lookup=lambda t: None)
    assert data["roe_pct"] is None


def test_fetch_ticker_data_uses_edgar_when_available():
    """EDGAR returns a full fundamentals dict → yfinance income_stmt is NOT consulted."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Apple", "currentPrice": 200, "previousClose": 198}
    # If yfinance income statement WERE consulted, the values below would
    # show up; assert they don't.
    fake_ticker.quarterly_income_stmt = pd.DataFrame(
        {"Total Revenue": [999], "Diluted EPS": [9.9]},
        index=pd.to_datetime(["2026-03-31"]),
    ).T
    fake_ticker.income_stmt = fake_ticker.quarterly_income_stmt
    fake_ticker.earnings_dates = None
    edgar_full = {
        "eps_latest_q": 1.55,
        "eps_latest_q_yoy_pct": 5.0,
        "revenue_latest_q": 85_000_000_000,
        "revenue_latest_q_yoy_pct": 6.0,
        "annual_eps_yoy_5y": [10.0, 11.0, 12.0, 13.0, 14.0],
        "annual_revenue_yoy_5y": [20.0, 21.0, 22.0, 23.0, 24.0],
        "quarterly_eps_yoy_4q": [1.0, 2.0, 3.0, 4.0],
        "quarterly_eps_yoy_4q_labels": ["Sep'24", "Dec'24", "Mar'25", "Jun'25"],
        "quarterly_revenue_yoy_4q": [5.0, 6.0, 7.0, 8.0],
        "quarterly_revenue_yoy_4q_labels": ["Sep'24", "Dec'24", "Mar'25", "Jun'25"],
    }
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=edgar_full):
        data = enrich.fetch_ticker_data("AAPL", "Leaders", "NASDAQ", rs_lookup=lambda t: 95)
    assert data["revenue_latest_q"] == 85_000_000_000   # from EDGAR, not yfinance 999
    assert data["eps_latest_q"] == 1.55
    assert data["annual_revenue_yoy_5y"][-1] == 24.0


def test_fetch_ticker_data_falls_back_to_yfinance_when_edgar_returns_none():
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Penny", "currentPrice": 5, "previousClose": 4.5}
    fake_ticker.quarterly_income_stmt = pd.DataFrame(
        {"Total Revenue": [100, 80], "Diluted EPS": [1.0, 0.9]},
        index=pd.to_datetime(["2026-03-31", "2025-12-31"]),
    ).T
    fake_ticker.income_stmt = pd.DataFrame(
        {"Total Revenue": [400, 360], "Diluted EPS": [4.0, 3.6]},
        index=pd.to_datetime(["2025-12-31", "2024-12-31"]),
    ).T
    fake_ticker.earnings_dates = None
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None):
        data = enrich.fetch_ticker_data("PENNY", "EarningsGap", "NASDAQ", rs_lookup=lambda t: None)
    # yfinance fallback ran → latest_q populated.
    assert data["revenue_latest_q"] == 100
    assert data["eps_latest_q"] == 1.0


def test_fetch_ticker_data_per_field_fallback_when_edgar_partial():
    """EDGAR returns a dict with revenue but EPS fields all None → yfinance
    fills the EPS slots without overwriting the EDGAR revenue values."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Mixed", "currentPrice": 10, "previousClose": 9.5}
    fake_ticker.quarterly_income_stmt = pd.DataFrame(
        {"Total Revenue": [500, 450], "Diluted EPS": [2.5, 2.3]},
        index=pd.to_datetime(["2026-03-31", "2025-12-31"]),
    ).T
    fake_ticker.income_stmt = pd.DataFrame(
        {"Total Revenue": [2000, 1800], "Diluted EPS": [10.0, 9.0]},
        index=pd.to_datetime(["2025-12-31", "2024-12-31"]),
    ).T
    fake_ticker.earnings_dates = None
    edgar_partial = {
        "eps_latest_q": None,
        "eps_latest_q_yoy_pct": None,
        "revenue_latest_q": 9_999_999,         # distinctive EDGAR value
        "revenue_latest_q_yoy_pct": 11.0,
        "annual_eps_yoy_5y": [None] * 5,
        "annual_revenue_yoy_5y": [None, None, None, 50.0, 60.0],
        "quarterly_eps_yoy_4q": [None] * 4,
        "quarterly_eps_yoy_4q_labels": [""] * 4,
        "quarterly_revenue_yoy_4q": [None, None, 30.0, 40.0],
        "quarterly_revenue_yoy_4q_labels": ["", "", "Mar'25", "Jun'25"],
    }
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=edgar_partial):
        data = enrich.fetch_ticker_data("MIX", "Leaders", "NASDAQ", rs_lookup=lambda t: 92)
    # EDGAR's revenue values preserved.
    assert data["revenue_latest_q"] == 9_999_999
    assert data["annual_revenue_yoy_5y"][-1] == 60.0
    # EDGAR EPS was None → yfinance fallback filled it.
    assert data["eps_latest_q"] == 2.5


def test_fetch_ticker_data_skips_edgar_for_non_us_exchange():
    """HK ticker (exchange=HKEX) must not call EDGAR — yfinance is the only source."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Tencent", "currentPrice": 350, "previousClose": 345}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    fake_ticker.earnings_dates = None
    edgar_calls = {"n": 0}

    def _track(_):
        edgar_calls["n"] += 1
        return None

    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", side_effect=_track):
        enrich.fetch_ticker_data("0700.HK", "HKLeaders", "HKEX", rs_lookup=lambda t: 95)
    assert edgar_calls["n"] == 0


def test_fetch_ticker_data_calls_edgar_when_exchange_is_empty_string():
    """US EOD .txt files write bare tickers (no exchange prefix), so the
    `_split_exchange_ticker` helper returns ("", ticker). EDGAR must still
    be consulted in that case — otherwise every US-EOD ticker silently
    falls through to yfinance and the report loses its EDGAR coverage."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "Flex", "currentPrice": 133, "previousClose": 134.5}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    fake_ticker.earnings_dates = None
    edgar_calls = {"n": 0}

    def _track(_):
        edgar_calls["n"] += 1
        return None

    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", side_effect=_track):
        enrich.fetch_ticker_data("FLEX", "HighVolume", "", rs_lookup=lambda t: 97)
    assert edgar_calls["n"] == 1


def test_fetch_ticker_data_extracts_adjusted_eps_from_earnings_dates():
    """yfinance earnings_dates has 'Reported EPS' column = consensus/Adjusted
    convention. We extract latest past row + same row 4 entries earlier for
    YoY. EDGAR GAAP fields stay independent."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "AKAM-mock", "currentPrice": 100, "previousClose": 99}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    idx = pd.to_datetime([
        "2026-08-06 16:00", "2026-05-07 16:00", "2026-02-19 16:00",
        "2025-11-06 16:00", "2025-08-07 16:00", "2025-05-08 16:00",
    ], utc=True)
    fake_ticker.earnings_dates = pd.DataFrame(
        {"EPS Estimate": [1.61, 1.60, 1.75, 1.64, 1.53, 1.57],
         "Reported EPS": [float("nan"), 1.61, 1.84, 1.86, 1.73, 1.70],
         "Surprise(%)": [float("nan"), 0.35, 5.12, 13.71, 13.18, 8.58]},
        index=idx,
    )
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None), \
         patch("report.enrich.pd.Timestamp.now", return_value=pd.Timestamp("2026-05-09", tz="UTC")):
        data = enrich.fetch_ticker_data("AKAM", "Leaders", "NASDAQ", rs_lookup=lambda t: 90)
    assert data["eps_latest_q_adj"] == pytest.approx(1.61)
    assert data["eps_latest_q_adj_yoy_pct"] == pytest.approx(-5.294, rel=0.01)


def test_fetch_ticker_data_adj_eps_handles_nan_latest_row():
    """Reported EPS is NaN for the just-released earnings (financial calendar
    sometimes lags by hours). Adj fields should be None (not NaN), so the
    renderer falls back to GAAP-only display."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "T", "currentPrice": 100, "previousClose": 99}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    idx = pd.to_datetime([
        "2026-05-08 16:00", "2026-02-08 16:00", "2025-11-08 16:00",
        "2025-08-08 16:00", "2025-05-08 16:00",
    ], utc=True)
    fake_ticker.earnings_dates = pd.DataFrame(
        {"EPS Estimate": [0.50, 0.45, 0.40, 0.35, 0.30],
         "Reported EPS": [float("nan"), 0.46, 0.41, 0.36, 0.31],
         "Surprise(%)": [float("nan"), 2.0, 2.5, 2.5, 3.0]},
        index=idx,
    )
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None), \
         patch("report.enrich.pd.Timestamp.now", return_value=pd.Timestamp("2026-05-09", tz="UTC")):
        data = enrich.fetch_ticker_data("T", "EarningsGap", "NASDAQ", rs_lookup=lambda t: 90)
    assert data["eps_latest_q_adj"] is None
    assert data["eps_latest_q_adj_yoy_pct"] is None


def test_fetch_ticker_data_adj_eps_missing_prior_year_row():
    """Only 3 past rows = no row 4 entries earlier. Latest is set; YoY None."""
    fake_ticker = MagicMock()
    fake_ticker.info = {"longName": "T", "currentPrice": 100, "previousClose": 99}
    fake_ticker.quarterly_income_stmt = pd.DataFrame()
    fake_ticker.income_stmt = pd.DataFrame()
    idx = pd.to_datetime([
        "2026-05-08 16:00", "2026-02-08 16:00", "2025-11-08 16:00",
    ], utc=True)
    fake_ticker.earnings_dates = pd.DataFrame(
        {"EPS Estimate": [0.50, 0.45, 0.40],
         "Reported EPS": [0.55, 0.46, 0.41],
         "Surprise(%)": [10.0, 2.0, 2.5]},
        index=idx,
    )
    with patch("report.enrich.yf.Ticker", return_value=fake_ticker), \
         patch("report.enrich.fetch_edgar_fundamentals", return_value=None), \
         patch("report.enrich.pd.Timestamp.now", return_value=pd.Timestamp("2026-05-09", tz="UTC")):
        data = enrich.fetch_ticker_data("T", "Leaders", "NASDAQ", rs_lookup=lambda t: 90)
    assert data["eps_latest_q_adj"] == pytest.approx(0.55)
    assert data["eps_latest_q_adj_yoy_pct"] is None
