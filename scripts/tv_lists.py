"""Diff TV-side custom lists against the names expected in config.toml.

Prints three buckets:
  - on TV but not in config  (lists you have that we don't sync to)
  - in config and found      (everything good)
  - in config but missing on TV (you need to create these on tradingview.com)

Usage:
    uv run python scripts/tv_lists.py
"""

import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tv_sync import BASE, _client, _load_cookies  # noqa: E402


def main() -> int:
    cookies = _load_cookies()
    if cookies is None:
        return 1
    with _client(cookies) as c:
        r = c.get("/api/v1/symbols_list/custom/")
    if r.status_code != 200:
        print(f"GET /custom/ -> {r.status_code}")
        print(r.text[:300])
        return 1
    tv_lists = {item["name"]: item["id"] for item in r.json()}
    print(f"TV account has {len(tv_lists)} custom list(s)")

    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    expected = set((cfg.get("tv_sync") or {}).get("lists", {}).values())

    tv_names = set(tv_lists)
    missing = sorted(expected - tv_names)
    matched = sorted(expected & tv_names)
    extra = sorted(tv_names - expected)

    print()
    print(f"=== ✅ Matched ({len(matched)}/{len(expected)}) ===")
    for n in matched:
        print(f"  {n}  (id={tv_lists[n]})")
    print()
    print(f"=== ❌ Missing on TV ({len(missing)}) — create these manually ===")
    for n in missing:
        print(f"  {n}")
    print()
    print(f"=== ℹ️  On TV but not in our config ({len(extra)}) — ignored ===")
    for n in extra:
        print(f"  {n}  (id={tv_lists[n]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
