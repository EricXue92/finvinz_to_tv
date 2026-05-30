"""TradingView watchlist REST PoC.

Read sessionid + sessionid_sign cookies from env, list watchlists,
optionally append symbols to a target list.

Usage:
    export TV_SESSIONID=...
    export TV_SESSIONID_SIGN=...
    uv run python scripts/tv_poc.py list
    uv run python scripts/tv_poc.py append <list_id> NASDAQ:AAPL NYSE:TSLA
"""

import json
import os
import sys

import httpx

BASE = "https://www.tradingview.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def client() -> httpx.Client:
    sid = os.environ.get("TV_SESSIONID")
    sid_sign = os.environ.get("TV_SESSIONID_SIGN")
    if not sid or not sid_sign:
        sys.exit("Missing TV_SESSIONID or TV_SESSIONID_SIGN env vars")
    return httpx.Client(
        base_url=BASE,
        cookies={"sessionid": sid, "sessionid_sign": sid_sign},
        headers={
            "User-Agent": UA,
            "Referer": f"{BASE}/",
            "Origin": BASE,
            "x-requested-with": "XMLHttpRequest",
        },
        timeout=15.0,
        follow_redirects=False,
    )


def list_watchlists() -> None:
    with client() as c:
        r = c.get("/api/v1/symbols_list/custom/")
        print(f"GET /api/v1/symbols_list/custom/ -> {r.status_code}")
        ct = r.headers.get("content-type", "")
        if "application/json" not in ct:
            print(f"Non-JSON response (content-type={ct}). First 400 chars:")
            print(r.text[:400])
            return
        data = r.json()
        print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])


def append_symbols(list_id: str, symbols: list[str]) -> None:
    payload = symbols
    with client() as c:
        url = f"/api/v1/symbols_list/custom/{list_id}/append/"
        r = c.post(url, json=payload)
        print(f"POST {url} -> {r.status_code}")
        print(r.text[:800])


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "list":
        list_watchlists()
    elif cmd == "append":
        if len(sys.argv) < 4:
            sys.exit("append requires <list_id> <symbol> [<symbol>...]")
        append_symbols(sys.argv[2], sys.argv[3:])
    else:
        sys.exit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
