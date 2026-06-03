#!/usr/bin/env bash
# Manual smoke test for the catalyst report. Requires DEEPSEEK_API_KEY
# and TAVILY_API_KEY in the environment (or .env). Writes to
# output/Reports/<today>_us_premarket.md.
#
# The CLI deletes its --snapshot argument when done, so we copy the
# fixture to a tempfile first so the fixture stays in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."
TODAY="$(date +%Y-%m-%d)"
TMPSNAP="$(mktemp -t morning_snap.XXXXXX.json)"
cp tests/fixtures/morning_snapshot_smoke.json "$TMPSNAP"
uv run python -m report.morning \
  --snapshot "$TMPSNAP" \
  --date "$TODAY" \
  --offset -20
echo "--- report ---"
cat "output/Reports/${TODAY//-/_}_us_premarket.md"
