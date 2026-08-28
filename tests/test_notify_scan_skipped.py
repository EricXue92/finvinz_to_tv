"""Tests for notify_scan_skipped — ntfy alert when net-ready timeout skips a scan."""
from __future__ import annotations

from unittest.mock import patch

from notify import notify_scan_skipped

_CFG = {"notify": {"enabled": True, "ntfy_topic": "t"}}


def test_posts_high_priority_alert_naming_mode() -> None:
    """A skipped run must fire a high-priority alert that names the mode,
    so a silently-lost pre-market window (2026-08-27) becomes visible."""
    with patch("notify._ntfy_post") as mock_post:
        notify_scan_skipped(
            "morning-gap",
            "network never came up after 300s",
            _CFG,
        )
    assert mock_post.called
    _server, _topic, title, body, priority = (
        mock_post.call_args.args + tuple(mock_post.call_args.kwargs.values())
    )
    assert "morning-gap" in title
    assert "network never came up after 300s" in body
    assert priority == "high"


def test_title_is_ascii_safe() -> None:
    """Title goes into an HTTP header — must survive latin-1 encoding."""
    with patch("notify._ntfy_post") as mock_post:
        notify_scan_skipped("morning-gap", "reason", _CFG)
    title = mock_post.call_args.args[2]
    title.encode("ascii")


def test_disabled_config_is_noop() -> None:
    with patch("notify._ntfy_post") as mock_post:
        notify_scan_skipped(
            "morning-gap", "reason", {"notify": {"enabled": False, "ntfy_topic": "t"}}
        )
    assert not mock_post.called


def test_missing_topic_is_noop() -> None:
    with patch("notify._ntfy_post") as mock_post:
        notify_scan_skipped("morning-gap", "reason", {"notify": {"enabled": True}})
    assert not mock_post.called
