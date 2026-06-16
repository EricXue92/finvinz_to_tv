"""Tests for the finviz library timeout / bounded-retry monkey patch.

Why: the upstream `finviz` library's sync path (`http_request_get` and
`finviz_request` in `finviz.helper_functions.request_functions`) calls
`requests.get` with no `timeout` argument and wraps `finviz_request` in
`@tenacity.retry(wait=wait_exponential())` with no `stop` condition.
A slow/lost SSL connection blocks the EOD pipeline until the launchd
wrapper's 30-min watchdog kills it (2026-06-16 incident — the wrapper
SIGTERM'd mid-Leaders and no .txt was written).

These tests pin the patched behaviour:
  - both functions pass a `timeout=(connect, read)` to `requests.get`,
  - `finviz_request` stops after a bounded number of attempts instead of
    retrying forever.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
import requests


def test_http_request_get_passes_timeout(monkeypatch):
    import finviz_timeout
    from finviz.helper_functions import request_functions as rf

    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        resp = MagicMock()
        resp.text = "<html></html>"
        resp.url = url
        resp.raise_for_status = lambda: None
        return resp

    monkeypatch.setattr(rf.requests, "get", fake_get)

    rf.http_request_get("https://finviz.com/screener.ashx", payload={"v": "111"})

    assert "timeout" in captured["kwargs"], "patched http_request_get must pass timeout="
    timeout = captured["kwargs"]["timeout"]
    assert isinstance(timeout, tuple) and len(timeout) == 2
    connect, read = timeout
    assert 1 <= connect <= 30, f"connect timeout {connect}s out of sane range"
    assert 5 <= read <= 60, f"read timeout {read}s out of sane range"


def test_http_request_get_with_session_passes_timeout(monkeypatch):
    """The session branch of http_request_get must also pass timeout."""
    import finviz_timeout  # noqa: F401
    from finviz.helper_functions import request_functions as rf

    captured: dict = {}
    session = MagicMock()

    def session_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        resp = MagicMock()
        resp.text = "<html></html>"
        resp.url = url
        resp.raise_for_status = lambda: None
        return resp

    session.get = session_get

    rf.http_request_get("https://finviz.com/quote.ashx", session=session)

    assert "timeout" in captured["kwargs"]


def test_finviz_request_passes_timeout(monkeypatch):
    import finviz_timeout  # noqa: F401
    from finviz.helper_functions import request_functions as rf

    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["kwargs"] = kwargs
        resp = MagicMock()
        resp.text = "<html></html>"
        return resp

    monkeypatch.setattr(rf.requests, "get", fake_get)

    rf.finviz_request("https://finviz.com/screener.ashx?r=21", user_agent="UA")

    assert "timeout" in captured["kwargs"]


def test_finviz_request_retries_are_bounded(monkeypatch):
    """A persistently failing request must give up — not retry forever."""
    import finviz_timeout  # noqa: F401
    from finviz.helper_functions import request_functions as rf

    calls = {"n": 0}

    def always_timeout(url, **kwargs):
        calls["n"] += 1
        raise requests.exceptions.ReadTimeout("simulated slow finviz.com")

    monkeypatch.setattr(rf.requests, "get", always_timeout)

    start = time.monotonic()
    with pytest.raises(Exception):
        rf.finviz_request("https://finviz.com/screener.ashx?r=21", user_agent="UA")
    elapsed = time.monotonic() - start

    # Original library: infinite retries, exp backoff capped at 600s — would
    # take hours. Patched: bounded attempts + capped wait → seconds.
    assert calls["n"] >= 2, "must retry at least once before giving up"
    assert calls["n"] <= 8, f"retried {calls['n']}x — bound is too loose"
    assert elapsed < 60, f"bounded retry should give up in <60s, took {elapsed:.1f}s"


def test_screener_module_uses_patched_function():
    """Screener does `from finviz.helper_functions.request_functions import
    http_request_get` — that binds a NAME in screener's own module. Patching
    only the source module would leave screener.http_request_get pointing at
    the original (timeout-less) function. Verify the patch covered it.
    """
    import finviz_timeout  # noqa: F401
    from finviz import screener as screener_mod
    from finviz.helper_functions import request_functions as rf

    assert screener_mod.http_request_get is rf.http_request_get


def test_main_func_module_uses_patched_function():
    """Same concern for `finviz.main_func` (used by `get_stock("SPY")`)."""
    import finviz_timeout  # noqa: F401
    from finviz import main_func
    from finviz.helper_functions import request_functions as rf

    assert main_func.http_request_get is rf.http_request_get
