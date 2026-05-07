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


# yfinance row labels in the wild use spaces ("Total Revenue", "Diluted EPS").
# Older / mocked frames may use the camelCase form. Try both, in order.
REVENUE_LABELS = ("Total Revenue", "TotalRevenue", "Operating Revenue")
DILUTED_EPS_LABELS = ("Diluted EPS", "DilutedEPS")
BASIC_EPS_LABELS = ("Basic EPS", "BasicEPS")


def _row_values(df: pd.DataFrame, row_label: str | tuple[str, ...]) -> list[float | None]:
    """Return a row of the income statement as floats (most recent first).
    yfinance frames are line-items × periods, so we look up the row by index label.
    Accepts either a single label or a tuple of fallback candidates (first match wins)."""
    if df is None or df.empty:
        return []
    candidates = (row_label,) if isinstance(row_label, str) else row_label
    series = None
    for label in candidates:
        if label in df.index:
            series = df.loc[label]
            break
    if series is None:
        return []
    out: list[float | None] = []
    for v in series.tolist():
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(None)
    return out


def latest_quarterly_with_yoy(
    df: pd.DataFrame, row_label: str | tuple[str, ...]
) -> tuple[float | None, float | None]:
    """Latest quarter value + YoY vs same quarter last year (4 quarters back)."""
    values = _row_values(df, row_label)
    if not values:
        return (None, None)
    latest = values[0]
    prior = values[4] if len(values) > 4 else None
    return (latest, compute_yoy(latest, prior))


def extract_annual_yoy_3y(
    df: pd.DataFrame, row_label: str | tuple[str, ...]
) -> list[float | None]:
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
        # Extended Yahoo Finance fields — populated even for unprofitable / new
        # tickers where trailing PE / annual YoY are typically null.
        "forward_pe": None,
        "ps_ratio": None,
        "profit_margin_pct": None,
        "operating_margin_pct": None,
        "gross_margin_pct": None,
        "revenue_growth_yoy_pct": None,
        "earnings_growth_yoy_pct": None,
        "debt_to_equity": None,
        "ev_to_revenue": None,
        "ev_to_ebitda": None,
        "target_mean_price": None,
        "target_high_price": None,
        "target_low_price": None,
        "num_analysts": None,
        "recommendation_mean": None,  # 1=strong buy … 5=sell
        "recommendation_key": None,   # "buy" / "hold" / "underperform" / etc.
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
        "short_pct_of_float": None,
        "sector": None,
        "industry": None,
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
        if (
            data["last_price"] is not None
            and data["prev_close"] is not None
            and data["prev_close"] > 0
        ):
            data["gap_pct"] = (data["last_price"] - data["prev_close"]) / data["prev_close"] * 100.0
        data["pe_ratio"] = info.get("trailingPE")
        roe = info.get("returnOnEquity")
        data["roe"] = roe * 100.0 if isinstance(roe, (int, float)) else None
        inst = info.get("heldPercentInstitutions")
        data["institutional_holdings_pct"] = inst * 100.0 if isinstance(inst, (int, float)) else None
        try:
            ed = t.earnings_dates
            if ed is not None and not ed.empty:
                past = ed[ed.index <= pd.Timestamp.now(tz=ed.index.tz)]
                if not past.empty:
                    data["latest_earnings_date"] = past.index.max()
        except Exception:
            pass
        if data["latest_earnings_date"] is None:
            data["latest_earnings_date"] = info.get("mostRecentQuarter")
        # Extended fundamentals — fill the gaps when trailing PE/YoY are null.
        data["forward_pe"] = info.get("forwardPE")
        data["ps_ratio"] = info.get("priceToSalesTrailing12Months")
        for src_key, dst_key in (
            ("profitMargins", "profit_margin_pct"),
            ("operatingMargins", "operating_margin_pct"),
            ("grossMargins", "gross_margin_pct"),
            ("revenueGrowth", "revenue_growth_yoy_pct"),
            ("earningsGrowth", "earnings_growth_yoy_pct"),
            ("shortPercentOfFloat", "short_pct_of_float"),
        ):
            v = info.get(src_key)
            if isinstance(v, (int, float)):
                data[dst_key] = v * 100.0
        data["debt_to_equity"] = info.get("debtToEquity")
        data["ev_to_revenue"] = info.get("enterpriseToRevenue")
        data["ev_to_ebitda"] = info.get("enterpriseToEbitda")
        data["target_mean_price"] = info.get("targetMeanPrice")
        data["target_high_price"] = info.get("targetHighPrice")
        data["target_low_price"] = info.get("targetLowPrice")
        data["num_analysts"] = info.get("numberOfAnalystOpinions")
        data["recommendation_mean"] = info.get("recommendationMean")
        data["recommendation_key"] = info.get("recommendationKey")
        data["fifty_two_week_high"] = info.get("fiftyTwoWeekHigh")
        data["fifty_two_week_low"] = info.get("fiftyTwoWeekLow")
        data["sector"] = info.get("sector")
        data["industry"] = info.get("industry")
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: info access failed: {e}")

    try:
        qdf = t.quarterly_income_stmt
        eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, DILUTED_EPS_LABELS)
        if eps_val is None:
            eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, BASIC_EPS_LABELS)
        data["eps_latest_q"] = eps_val
        data["eps_latest_q_yoy_pct"] = eps_yoy
        rev_val, rev_yoy = latest_quarterly_with_yoy(qdf, REVENUE_LABELS)
        data["revenue_latest_q"] = rev_val
        data["revenue_latest_q_yoy_pct"] = rev_yoy
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: quarterly fetch failed: {e}")

    try:
        adf = t.income_stmt
        data["annual_eps_yoy_3y"] = extract_annual_yoy_3y(adf, DILUTED_EPS_LABELS)
        if all(v is None for v in data["annual_eps_yoy_3y"]):
            data["annual_eps_yoy_3y"] = extract_annual_yoy_3y(adf, BASIC_EPS_LABELS)
        data["annual_revenue_yoy_3y"] = extract_annual_yoy_3y(adf, REVENUE_LABELS)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: annual fetch failed: {e}")

    try:
        data["rs_percentile"] = rs_lookup(ticker)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: RS lookup failed: {e}")

    return data
