import csv
from pathlib import Path

import pytest

from report import __main__ as orch


def test_normalize_rs_key_us():
    assert orch._normalize_rs_key("AAPL", "us") == "AAPL"
    assert orch._normalize_rs_key("aapl", "us") == "AAPL"


def test_normalize_rs_key_hk_strips_futu_prefix_and_padding():
    assert orch._normalize_rs_key("HK.00700", "hk") == "0700.HK"
    assert orch._normalize_rs_key("HK.00001", "hk") == "0001.HK"
    assert orch._normalize_rs_key("HK.09988", "hk") == "9988.HK"


def test_normalize_rs_key_hk_passthrough_when_already_yf_form():
    # Should not double-process if input is already yfinance form
    assert orch._normalize_rs_key("0700.HK", "hk") == "0700.HK"


def test_split_exchange_ticker():
    assert orch._split_exchange_ticker("NASDAQ:AAPL") == ("NASDAQ", "AAPL")
    assert orch._split_exchange_ticker("HKEX:00700") == ("HKEX", "00700")
    assert orch._split_exchange_ticker("AAPL") == ("", "AAPL")


def test_yf_ticker_us_unchanged():
    assert orch._yf_ticker("AAPL", "us") == "AAPL"


def test_yf_ticker_hk_pads_to_four_digits():
    assert orch._yf_ticker("00700", "hk") == "0700.HK"
    assert orch._yf_ticker("700", "hk") == "0700.HK"
    assert orch._yf_ticker("09988", "hk") == "9988.HK"


def test_load_rs_lookup_us_capital_p_column(tmp_path: Path, monkeypatch):
    """Real US CSV uses capital 'Percentile'; lookup must read that column."""
    state_dir = tmp_path / "output" / "state"
    state_dir.mkdir(parents=True)
    csv_path = state_dir / "rs_rating_2026_05_07.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Rank", "Ticker", "Percentile"])
        writer.writerow([1, "AAPL", 95])
        writer.writerow([2, "NVDA", 99])
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    lookup = orch._load_rs_lookup("us", "2026_05_07")
    assert lookup("AAPL") == 95
    assert lookup("NVDA") == 99
    assert lookup("UNKNOWN") is None


def test_load_rs_lookup_hk_normalizes_futu_prefix(tmp_path: Path, monkeypatch):
    """HK CSV stores 'HK.00700'; lookup must normalize to yfinance form '0700.HK'."""
    state_dir = tmp_path / "output" / "state"
    state_dir.mkdir(parents=True)
    csv_path = state_dir / "hk_rs_rating_2026-05-07.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "percentile"])
        writer.writerow(["HK.00700", 92])
        writer.writerow(["HK.09988", 88])
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    lookup = orch._load_rs_lookup("hk", "2026_05_07")
    assert lookup("0700.HK") == 92
    assert lookup("9988.HK") == 88
    assert lookup("UNKNOWN.HK") is None


def test_load_rs_lookup_missing_cache_returns_passthrough(tmp_path: Path, monkeypatch):
    """No cache file → lookup is callable, returns None for everything."""
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    (tmp_path / "output" / "state").mkdir(parents=True)
    lookup = orch._load_rs_lookup("us", "2026_05_07")
    assert lookup("AAPL") is None
