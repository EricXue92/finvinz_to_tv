"""Per-ticker enrichment: SEC EDGAR-first fundamentals (US) with yfinance fallback,
plus yfinance for snapshot fields (info, market cap, price, earnings date) and HK.

Output schema: 5-year annual YoY + 4-quarter YoY trajectory. EDGAR is skipped only
for explicit HK exchange tickers (`HK_EXCHANGES`); empty-string and US exchange
strings both qualify (US EOD .txt files write bare tickers without an exchange
prefix, so an empty string from `_split_exchange_ticker` is the common case).
Per-field guards in `fetch_ticker_data` ensure yfinance fills any slot EDGAR
returned as None without overwriting EDGAR data.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd
import yfinance as yf

from report.edgar import fetch_edgar_fundamentals

logger = logging.getLogger(__name__)


# Exchanges for which EDGAR data is available. HK and other foreign listings
# fall back to yfinance directly.
# EDGAR is the US-only data source. We invert the gate (skip-if-HK) rather than
# allow-list US exchanges because US EOD .txt files write bare tickers (no
# exchange prefix), so `_split_exchange_ticker` returns "" — an allow-list
# would silently drop EVERY US ticker. Empty string and any US exchange
# string both qualify; only explicit HKEX is rejected.
HK_EXCHANGES: frozenset[str] = frozenset({"HKEX"})


def compute_yoy(current: float | None, prior: float | None) -> float | None:
    """Year-over-year percentage change using `abs(prior)` as the denominator
    (Bloomberg / FactSet convention). This handles loss-bearing companies
    correctly without sign-flip noise:
      - prior +10 → current +5  =  -50%   (profit shrank)
      - prior -10 → current -5  =  +50%   (loss narrowed, improved)
      - prior -10 → current -20 = -100%   (loss widened, worsened)
      - prior -10 → current +5  = +150%   (loss → profit, big swing)
      - prior +10 → current -5  = -150%   (profit → loss, big swing)
    Returns None only when an input is missing or `prior == 0`
    (division by zero — undefined, not "loss")."""
    if current is None or prior is None:
        return None
    if prior == 0:
        return None
    return (current - prior) / abs(prior) * 100.0


# yfinance row labels in the wild use spaces ("Total Revenue", "Diluted EPS").
# Older / mocked frames may use the camelCase form. Try both, in order.
REVENUE_LABELS = ("Total Revenue", "TotalRevenue", "Operating Revenue")
DILUTED_EPS_LABELS = ("Diluted EPS", "DilutedEPS")
BASIC_EPS_LABELS = ("Basic EPS", "BasicEPS")


def _row_values(df: pd.DataFrame, row_label: str | tuple[str, ...]) -> list[float | None]:
    """Return a row of the income statement as floats (most recent first).
    yfinance frames are line-items × periods, so we look up the row by index label.
    Accepts either a single label or a tuple of fallback candidates (first match wins).

    NaN sentinels (yfinance pads truncated histories with NaN — e.g. 1138.HK's
    oldest fiscal year) are normalised to None so downstream YoY math and chart
    rendering treat them as "missing" rather than letting NaN poison min/max."""
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
        if pd.isna(v):
            out.append(None)
            continue
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


def extract_quarterly_yoy(
    df: pd.DataFrame,
    row_label: str | tuple[str, ...],
    n_quarters: int = 4,
) -> tuple[list[float | None], list[str]]:
    """Last `n_quarters` quarters of YoY % (oldest→newest) + period labels.
    YoY for each quarter = current value / same quarter 4 quarters back. Pads
    with `None` and `""` when the frame doesn't have enough history (yfinance
    typically returns 4-5 quarters; full 4-quarter YoY needs 8 quarters)."""
    values = _row_values(df, row_label)
    yoy_values: list[float | None] = []
    labels: list[str] = []
    cols = list(df.columns) if (df is not None and not df.empty) else []
    for q_idx in range(n_quarters - 1, -1, -1):  # 3, 2, 1, 0 → oldest first
        try:
            current = values[q_idx]
            prior = values[q_idx + 4]
        except IndexError:
            yoy_values.append(None)
            labels.append("")
            continue
        yoy_values.append(compute_yoy(current, prior))
        try:
            d = cols[q_idx]
            if hasattr(d, "strftime"):
                # "Mar'25" — short month + 2-digit year, mono-friendly
                labels.append(d.strftime("%b'%y"))
            else:
                labels.append(str(d)[:7])
        except (IndexError, AttributeError):
            labels.append("")
    return (yoy_values, labels)


def extract_annual_yoy(
    df: pd.DataFrame,
    row_label: str | tuple[str, ...],
    years_back: int = 5,
) -> list[float | None]:
    """Up to `years_back` YoY datapoints in oldest→newest order
    (e.g. `[FY-5, FY-4, FY-3, FY-2, FY-1]`). yfinance annual frames have
    most-recent fiscal year first, so we reverse before pairing. Pads with
    None when the frame doesn't have enough fiscal years.

    Default 5 — enough for IBD CANSLIM "A" coverage. yfinance free tier
    only returns ~4 fiscal years (3 YoY pairs), so the older slots will
    typically be None when EDGAR isn't available; the EDGAR fast path
    fills them in. See report/edgar.py."""
    values = list(reversed(_row_values(df, row_label)))
    yoy: list[float | None] = []
    for i in range(-years_back, 0):
        try:
            current = values[i]
            prior = values[i - 1]
        except IndexError:
            yoy.append(None)
            continue
        yoy.append(compute_yoy(current, prior))
    return yoy


# Back-compat shim — older test fixtures may still call the 3y form
# (now identical to the new default).
def extract_annual_yoy_3y(
    df: pd.DataFrame, row_label: str | tuple[str, ...]
) -> list[float | None]:
    return extract_annual_yoy(df, row_label, years_back=3)


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
        "sector": None,
        "industry": None,
        "market_cap": None,
        "last_price": None,
        "prev_close": None,
        "gap_pct": None,
        "institutional_holdings_pct": None,
        # CANSLIM "L" support — IBD's leadership floor is ROE > 17%.
        # `info["returnOnEquity"]` is a fraction; we store percent.
        "roe_pct": None,
        # Latest quarterly earnings (CANSLIM "C")
        "eps_latest_q": None,
        "eps_latest_q_yoy_pct": None,
        "revenue_latest_q": None,
        "revenue_latest_q_yoy_pct": None,
        # Annual earnings increases — past 5 fiscal years of YoY (CANSLIM "A")
        "annual_eps_yoy_5y": [None, None, None, None, None],
        "annual_revenue_yoy_5y": [None, None, None, None, None],
        # Past 4 quarters of YoY % + period labels (CANSLIM "C" trajectory)
        "quarterly_eps_yoy_4q": [None, None, None, None],
        "quarterly_eps_yoy_4q_labels": ["", "", "", ""],
        "quarterly_revenue_yoy_4q": [None, None, None, None],
        "quarterly_revenue_yoy_4q_labels": ["", "", "", ""],
        "latest_earnings_date": None,
        "rs_percentile": None,
        # Yahoo's pre-computed MRQ YoY — used as fallback when income_stmt is sparse
        "yahoo_revenue_growth_yoy_pct": None,
        "yahoo_earnings_growth_yoy_pct": None,
        # First public trading date — populated only for `IPO` group, used by
        # the renderer to print a friendly "**N年**新上市公司" banner instead
        # of an empty fundamentals table when EDGAR + yfinance both have nothing.
        "ipo_date": None,
    }
    try:
        t = yf.Ticker(ticker)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: yf.Ticker construction failed: {e}")
        return data

    try:
        info = t.info or {}
        data["company_name"] = info.get("longName") or info.get("shortName")
        data["sector"] = info.get("sector")
        data["industry"] = info.get("industry")
        data["market_cap"] = info.get("marketCap")
        data["last_price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        data["prev_close"] = info.get("previousClose")
        if (
            data["last_price"] is not None
            and data["prev_close"] is not None
            and data["prev_close"] > 0
        ):
            data["gap_pct"] = (data["last_price"] - data["prev_close"]) / data["prev_close"] * 100.0
        inst = info.get("heldPercentInstitutions")
        data["institutional_holdings_pct"] = (
            inst * 100.0 if isinstance(inst, (int, float)) else None
        )
        roe = info.get("returnOnEquity")
        data["roe_pct"] = roe * 100.0 if isinstance(roe, (int, float)) else None
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
        # Yahoo's pre-computed MRQ YoY — fallback when quarterly_income_stmt
        # is missing or under-populated.
        for src_key, dst_key in (
            ("revenueGrowth", "yahoo_revenue_growth_yoy_pct"),
            ("earningsGrowth", "yahoo_earnings_growth_yoy_pct"),
        ):
            v = info.get(src_key)
            if isinstance(v, (int, float)):
                data[dst_key] = v * 100.0
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: info access failed: {e}")

    # IPO date for the renderer's "新上市公司" banner. Cheap path is
    # info["firstTradeDateEpochUtc"]; for many fresh IPOs it's null in info,
    # so for the `IPO` group only we pay the extra `t.history(period="max")`
    # call and read its first index. Skipped for non-IPO tickers to keep the
    # 30-tickers/day report fast.
    try:
        epoch = (info or {}).get("firstTradeDateEpochUtc") if isinstance(info, dict) else None
        if isinstance(epoch, (int, float)):
            data["ipo_date"] = pd.Timestamp(epoch, unit="s", tz="UTC").date().isoformat()
        elif group == "IPO":
            hist = t.history(period="max")
            if hist is not None and not hist.empty:
                data["ipo_date"] = hist.index[0].date().isoformat()
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: ipo_date lookup failed: {e}")

    # --- EDGAR-first fundamentals (US only) -------------------------------
    edgar_data: dict | None = None
    if exchange.upper() not in HK_EXCHANGES:
        try:
            edgar_data = fetch_edgar_fundamentals(ticker)
        except Exception as e:
            logger.warning(f"[enrich] {ticker}: EDGAR fetch raised: {e}")
            edgar_data = None
    if edgar_data:
        # Populate any non-None field from EDGAR. Leave Nones for yfinance to fill.
        for k, v in edgar_data.items():
            if v is None:
                continue
            if isinstance(v, list) and all(x is None or x == "" for x in v):
                continue
            data[k] = v

    try:
        qdf = t.quarterly_income_stmt
        if data["eps_latest_q"] is None:
            eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, DILUTED_EPS_LABELS)
            if eps_val is None:
                eps_val, eps_yoy = latest_quarterly_with_yoy(qdf, BASIC_EPS_LABELS)
            data["eps_latest_q"] = eps_val
            data["eps_latest_q_yoy_pct"] = eps_yoy
        if data["revenue_latest_q"] is None:
            rev_val, rev_yoy = latest_quarterly_with_yoy(qdf, REVENUE_LABELS)
            data["revenue_latest_q"] = rev_val
            data["revenue_latest_q_yoy_pct"] = rev_yoy
        if all(v is None for v in data["quarterly_eps_yoy_4q"]):
            eps_q, eps_lbl = extract_quarterly_yoy(qdf, DILUTED_EPS_LABELS, 4)
            if all(v is None for v in eps_q):
                eps_q, eps_lbl = extract_quarterly_yoy(qdf, BASIC_EPS_LABELS, 4)
            data["quarterly_eps_yoy_4q"] = eps_q
            data["quarterly_eps_yoy_4q_labels"] = eps_lbl
        if all(v is None for v in data["quarterly_revenue_yoy_4q"]):
            rev_q, rev_lbl = extract_quarterly_yoy(qdf, REVENUE_LABELS, 4)
            data["quarterly_revenue_yoy_4q"] = rev_q
            data["quarterly_revenue_yoy_4q_labels"] = rev_lbl
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: quarterly fetch failed: {e}")

    try:
        adf = t.income_stmt
        if all(v is None for v in data["annual_eps_yoy_5y"]):
            data["annual_eps_yoy_5y"] = extract_annual_yoy(adf, DILUTED_EPS_LABELS, years_back=5)
            if all(v is None for v in data["annual_eps_yoy_5y"]):
                data["annual_eps_yoy_5y"] = extract_annual_yoy(adf, BASIC_EPS_LABELS, years_back=5)
        if all(v is None for v in data["annual_revenue_yoy_5y"]):
            data["annual_revenue_yoy_5y"] = extract_annual_yoy(adf, REVENUE_LABELS, years_back=5)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: annual fetch failed: {e}")

    try:
        data["rs_percentile"] = rs_lookup(ticker)
    except Exception as e:
        logger.warning(f"[enrich] {ticker}: RS lookup failed: {e}")

    return data
