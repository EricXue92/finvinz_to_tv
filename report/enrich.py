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
