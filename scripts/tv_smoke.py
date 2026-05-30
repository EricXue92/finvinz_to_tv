"""One-shot smoke test for tv_sync.

Picks the most recent non-empty .txt under output/TV/US/ and syncs it
to the TradingView list named "TopGainers" (append-only). Confirms:
  - cookie loads (from env or ~/.config/momentum-scanner/tv_cookie.json)
  - list resolution works
  - append endpoint accepts the project's bare-ticker format

Usage:
    uv run python scripts/tv_smoke.py
"""

import pathlib
import sys

# Make project root importable when running this from scripts/.
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tv_sync import sync_to_tv  # noqa: E402


def pick_latest_nonempty(folder: pathlib.Path) -> pathlib.Path | None:
    candidates = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in candidates:
        if p.stat().st_size > 0:
            return p
    return None


def main() -> int:
    us_dir = ROOT / "output" / "TV" / "US"
    src = pick_latest_nonempty(us_dir)
    if src is None:
        print(f"No non-empty .txt under {us_dir}")
        return 1
    tickers = [t.strip() for t in src.read_text().split(",") if t.strip()]
    print(f"Source: {src.name} ({len(tickers)} tickers)")
    print(f"Syncing -> TradingView list 'TopGainers' (append-only)")
    ok = sync_to_tv(tickers, "TopGainers", "US", append_only=True)
    print(f"Result: {'OK' if ok else 'FAILED (see warning above)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
