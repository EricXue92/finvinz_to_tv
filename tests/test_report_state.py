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
