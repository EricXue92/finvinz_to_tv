"""Sync watchlists to TradingView via unofficial REST API.

Auth uses the sessionid + sessionid_sign cookies from a logged-in browser
session. Cookies are read from env (``TV_SESSIONID``, ``TV_SESSIONID_SIGN``)
first, then ``~/.config/momentum-scanner/tv_cookie.json`` (chmod 600). The
endpoints under ``/api/v1/symbols_list/custom/`` are not officially documented
— behavior may change without notice; all failures are soft (log + return
False), same contract as ``futu_sync``.

The .txt watchlist files remain the primary artifact. The user must
pre-create the target lists on TradingView with the exact names mapped in
``[tv_sync.lists]``; a missing name logs a warning and skips that list.

TradingView accepts bare US tickers (``AAPL``) and ``HKEX:NNNN`` for HK in
the symbols payload — same formats this project's .txt files use — so no
ticker conversion is needed.
"""

import json
import logging
import os
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

BASE = "https://www.tradingview.com"
COOKIE_FILE = Path.home() / ".config" / "momentum-scanner" / "tv_cookie.json"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Per-process caches. Populated lazily on first sync_to_tv call so the EOD
# run does one GET /custom/ even when syncing 10+ lists in sequence.
_cookies: dict[str, str] | None = None
_list_cache: dict[str, int] | None = None
_disabled_reason: str | None = None  # remember a fatal once to silence repeat warnings


def _load_cookies() -> dict[str, str] | None:
    global _cookies, _disabled_reason
    if _cookies is not None:
        return _cookies
    sid = os.environ.get("TV_SESSIONID")
    sig = os.environ.get("TV_SESSIONID_SIGN")
    if sid and sig:
        _cookies = {"sessionid": sid, "sessionid_sign": sig}
        return _cookies
    if COOKIE_FILE.exists():
        try:
            data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            sid = data.get("sessionid")
            sig = data.get("sessionid_sign")
            if sid and sig:
                _cookies = {"sessionid": sid, "sessionid_sign": sig}
                return _cookies
            _disabled_reason = f"{COOKIE_FILE} missing sessionid/sessionid_sign"
        except (OSError, json.JSONDecodeError) as e:
            _disabled_reason = f"cannot read {COOKIE_FILE}: {e}"
    else:
        _disabled_reason = (
            f"no TV_SESSIONID env vars and {COOKIE_FILE} does not exist"
        )
    logger.warning(f"  TV sync disabled: {_disabled_reason}")
    return None


def _client(cookies: dict[str, str]) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        cookies=cookies,
        headers={
            "User-Agent": _UA,
            "Referer": f"{BASE}/",
            "Origin": BASE,
            "x-requested-with": "XMLHttpRequest",
        },
        timeout=15.0,
        follow_redirects=False,
    )


def _refresh_list_cache(c: httpx.Client) -> bool:
    """Populate {list_name -> id} from GET /api/v1/symbols_list/custom/.

    Also harvests the CSRF token from the Set-Cookie response and pins it
    on the client as ``X-CSRFToken`` — TV's REST endpoint rejects mutating
    requests (POST/DELETE) without it. Reads are exempt, so a single GET
    serves both as the list-index fetch and the CSRF warm-up.
    """
    global _list_cache
    try:
        r = c.get("/api/v1/symbols_list/custom/")
    except httpx.HTTPError as e:
        logger.warning(f"  TV sync: cannot fetch list index — {e}")
        return False
    if r.status_code != 200:
        logger.warning(
            f"  TV sync: list index returned {r.status_code} "
            "(cookie expired? re-extract sessionid)"
        )
        return False
    csrf = c.cookies.get("csrftoken")
    if csrf:
        c.headers["X-CSRFToken"] = csrf
    try:
        data = r.json()
    except ValueError:
        logger.warning("  TV sync: list index was not JSON (cookie expired?)")
        return False
    _list_cache = {
        item["name"]: item["id"]
        for item in data
        if isinstance(item, dict) and "name" in item and "id" in item
    }
    return True


def sync_to_tv(
    tickers: list[str],
    list_name: str,
    market: Literal["US", "HK"],
    append_only: bool = False,
) -> bool:
    """Sync `tickers` into a TradingView custom watchlist `list_name`.

    Computes a diff against the current list contents and applies POST
    ``/append/`` and POST ``/remove/`` for the ADD and DEL ops. With
    ``append_only=True`` the DEL phase is skipped, matching the
    ``futu_sync`` append-only group semantics (used for shared lists
    that multiple scanners contribute to).

    Soft-fail: returns False on missing cookie, cookie expired, list-name
    not found on TV, or any HTTP/network error. Never raises.

    The ``market`` arg is accepted for signature parity with
    ``sync_to_futu`` but unused — TV accepts the .txt's native ticker
    format (bare US, ``HKEX:NNNN`` HK) without conversion.
    """
    if not tickers:
        return False
    cookies = _load_cookies()
    if cookies is None:
        return False
    _ = market  # signature parity with sync_to_futu

    desired = {t.strip() for t in tickers if t.strip()}
    if not desired:
        return False

    with _client(cookies) as c:
        if _list_cache is None and not _refresh_list_cache(c):
            return False
        assert _list_cache is not None

        list_id = _list_cache.get(list_name)
        if list_id is None:
            # Cache was populated at process start; user may have just
            # created the list — refresh once before giving up.
            if not _refresh_list_cache(c):
                return False
            list_id = _list_cache.get(list_name) if _list_cache else None
        if list_id is None:
            logger.warning(
                f"  TV sync ({list_name}): no matching list on TradingView; "
                "create it manually with that exact name (case-sensitive)"
            )
            return False

        try:
            r = c.get(f"/api/v1/symbols_list/custom/{list_id}/")
        except httpx.HTTPError as e:
            logger.warning(f"  TV sync ({list_name}): cannot read list — {e}")
            return False
        if r.status_code != 200:
            logger.warning(
                f"  TV sync ({list_name}): GET list returned {r.status_code}"
            )
            return False
        current = set(r.json().get("symbols") or [])

        to_add = sorted(desired - current)
        to_del = [] if append_only else sorted(current - desired)

        if to_del:
            try:
                r = c.post(
                    f"/api/v1/symbols_list/custom/{list_id}/remove/",
                    json=to_del,
                )
                if r.status_code != 200:
                    logger.warning(
                        f"  TV sync ({list_name}): DEL failed "
                        f"{r.status_code} — {r.text[:200]}"
                    )
            except httpx.HTTPError as e:
                logger.warning(f"  TV sync ({list_name}): DEL error — {e}")
        if to_add:
            try:
                r = c.post(
                    f"/api/v1/symbols_list/custom/{list_id}/append/",
                    json=to_add,
                )
                if r.status_code != 200:
                    logger.warning(
                        f"  TV sync ({list_name}): ADD failed "
                        f"{r.status_code} — {r.text[:200]}"
                    )
                    return False
            except httpx.HTTPError as e:
                logger.warning(f"  TV sync ({list_name}): ADD error — {e}")
                return False

        final = len(current | desired) if append_only else len(desired)
        logger.info(
            f"  TV sync ({list_name}): +{len(to_add)} -{len(to_del)} "
            f"({final} symbols in list"
            f"{', append-only' if append_only else ''})"
        )
        return True
