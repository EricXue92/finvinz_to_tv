"""One-time bootstrap: create the 18 empty TV lists named in config.toml.

Skips names that already exist on TV. Idempotent — safe to re-run.

Usage:
    uv run python scripts/tv_bootstrap.py
"""

import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tv_sync import _client, _load_cookies  # noqa: E402


def main() -> int:
    cookies = _load_cookies()
    if cookies is None:
        return 1

    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    expected = sorted(set((cfg.get("tv_sync") or {}).get("lists", {}).values()))
    if not expected:
        print("No names in [tv_sync.lists] — nothing to do")
        return 0

    with _client(cookies) as c:
        r = c.get("/api/v1/symbols_list/custom/")
        if r.status_code != 200:
            print(f"GET /custom/ -> {r.status_code} {r.text[:200]}")
            return 1
        # TV gates POST/DELETE on a CSRF token issued via the GET response's
        # Set-Cookie. Pin it as a default header for the subsequent POSTs.
        csrf = c.cookies.get("csrftoken")
        if csrf:
            c.headers["X-CSRFToken"] = csrf
        existing = {item["name"] for item in r.json()}

        created = []
        skipped = []
        failed = []
        for name in expected:
            if name in existing:
                skipped.append(name)
                continue
            r = c.post(
                "/api/v1/symbols_list/custom/",
                json={"name": name, "symbols": []},
            )
            if r.status_code == 201:
                created.append(name)
            else:
                failed.append((name, r.status_code, r.text[:120]))

    print(f"Created: {len(created)}")
    for n in created:
        print(f"  + {n}")
    print(f"Skipped (already existed): {len(skipped)}")
    for n in skipped:
        print(f"  = {n}")
    if failed:
        print(f"Failed: {len(failed)}")
        for n, code, body in failed:
            print(f"  ! {n} -> {code} | {body}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
