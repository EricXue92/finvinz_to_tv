"""Tests for notify_morning_gap body/title rendering."""
from __future__ import annotations

from unittest.mock import patch

from notify import notify_morning_gap

_CFG = {"notify": {"enabled": True, "ntfy_topic": "t", "max_tickers_in_body": 100}}


def test_body_lists_all_tickers_not_just_new() -> None:
    """The alert body must show the full scan list, not only the delta.

    Regression: a -5min scan with 2 new but 33 total showed only the 2
    new tickers in the body ("COHR, STM · total: 33").
    """
    new = ["COHR", "STM"]
    all_tickers = [f"T{i}" for i in range(31)] + new
    with patch("notify._ntfy_post") as mock_post:
        notify_morning_gap(
            new, -5, len(all_tickers), _CFG, all_tickers=all_tickers
        )
    body = mock_post.call_args.args[3]
    for t in all_tickers:
        assert t in body, f"{t} missing from body"
    assert "total: 33" in body


def test_title_is_ascii_safe() -> None:
    """Title goes into an HTTP header — non-ASCII (·) renders as mojibake.

    The separator must be ASCII so it survives latin-1 header encoding.
    """
    with patch("notify._ntfy_post") as mock_post:
        notify_morning_gap(["AAA"], -5, 1, _CFG, all_tickers=["AAA"])
    title = mock_post.call_args.args[2]
    title.encode("ascii")  # raises UnicodeEncodeError if non-ASCII present
    assert "·" not in title


def test_title_reflects_new_count() -> None:
    with patch("notify._ntfy_post") as mock_post:
        notify_morning_gap(
            ["COHR", "STM"], -5, 33, _CFG, all_tickers=["COHR", "STM"]
        )
    title = mock_post.call_args.args[2]
    assert "2 new" in title
    assert "-5min" in title


def test_promoted_title_ascii_safe() -> None:
    with patch("notify._ntfy_post") as mock_post:
        notify_morning_gap(
            [], 10, 5, _CFG, promoted=["XYZ"], all_tickers=[]
        )
    title = mock_post.call_args.args[2]
    title.encode("ascii")
    assert "PROMOTED" in title
