"""Patch the `finviz` library's sync HTTP path with socket timeouts and
bounded retries.

The upstream library's sync calls (`http_request_get`, `finviz_request`
in `finviz.helper_functions.request_functions`) invoke `requests.get`
with no `timeout=` argument. On a slow/lost SSL connection the call
blocks until OS-level TCP keepalive fires (hours). Worse, `finviz_request`
is wrapped in `@tenacity.retry(wait=wait_exponential())` with no `stop`
condition — so a degraded path retries forever, with backoff capped at
600s per attempt.

The launchd wrapper's 30-min watchdog (`scripts/run_eod.sh`) was the
only thing bounding this, and on 2026-06-16 one finviz scrape ran ~12 min
which pushed the rest of the pipeline past the watchdog, killing the EOD
run mid-Leaders with no .txt written.

Importing this module replaces the two functions in every site that
re-exports them by name (`finviz.helper_functions.request_functions`,
`finviz.main_func`, `finviz.screener`, `finviz.portfolio`). Idempotent.
"""
from __future__ import annotations

import logging

import requests
import tenacity
from lxml import html

from finviz.config import USER_AGENT
from finviz.helper_functions.error_handling import ConnectionTimeout

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 30
_TIMEOUT: tuple[int, int] = (_CONNECT_TIMEOUT, _READ_TIMEOUT)

_MAX_ATTEMPTS = 4
_MAX_RETRY_DELAY = 8


def _patched_http_request_get(
    url, session=None, payload=None, parse=True, user_agent=USER_AGENT
):
    if payload is None:
        payload = {}
    try:
        if session is not None:
            content = session.get(
                url,
                params=payload,
                verify=False,
                headers={"User-Agent": user_agent},
                timeout=_TIMEOUT,
            )
        else:
            content = requests.get(
                url,
                params=payload,
                verify=False,
                headers={"User-Agent": user_agent},
                timeout=_TIMEOUT,
            )
        content.raise_for_status()
        if parse:
            return html.fromstring(content.text), content.url
        return content.text, content.url
    except requests.exceptions.Timeout:
        raise ConnectionTimeout(url)


@tenacity.retry(
    stop=tenacity.stop_after_attempt(_MAX_ATTEMPTS),
    wait=tenacity.wait_exponential(max=_MAX_RETRY_DELAY),
    reraise=True,
)
def _patched_finviz_request(url: str, user_agent: str):
    response = requests.get(
        url,
        headers={"User-Agent": user_agent},
        timeout=_TIMEOUT,
    )
    if response.text == "Too many requests.":
        raise Exception("Too many requests.")
    return response


def apply() -> None:
    """Replace the timeout-less functions in every module that bound them."""
    from finviz.helper_functions import request_functions as _rf
    from finviz import main_func as _main_func
    from finviz import screener as _screener
    from finviz import portfolio as _portfolio

    _rf.http_request_get = _patched_http_request_get
    _rf.finviz_request = _patched_finviz_request
    _main_func.http_request_get = _patched_http_request_get
    _screener.http_request_get = _patched_http_request_get
    _portfolio.http_request_get = _patched_http_request_get

    logger.debug(
        "finviz_timeout: patched (connect=%ds read=%ds max_attempts=%d)",
        _CONNECT_TIMEOUT,
        _READ_TIMEOUT,
        _MAX_ATTEMPTS,
    )


apply()
