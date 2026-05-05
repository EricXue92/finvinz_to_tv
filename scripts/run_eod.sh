#!/usr/bin/env bash
# Wrapper for the EOD launchd job. Two responsibilities:
#   1. Rotate output/launchd.log if its last-modification date is not today,
#      so each calendar day starts with a fresh log. Multiple same-day
#      invocations (launchd + manual reruns) still append.
#   2. Redirect stdout/stderr to the log itself. The matching plist
#      (com.xue.finviz-to-tv.plist) therefore does NOT set
#      StandardOutPath/StandardErrorPath — launchd would otherwise hold
#      an append-mode fd open through any in-process truncate, leaving
#      a NUL-padded hole at the start of the new day's log.
set -euo pipefail

LOG=/Users/xue/finviz_to_tv/output/launchd.log
mkdir -p "$(dirname "$LOG")"

if [[ -f "$LOG" && "$(date -r "$LOG" +%Y-%m-%d)" != "$(date +%Y-%m-%d)" ]]; then
    : > "$LOG"
fi

exec >> "$LOG" 2>&1
exec /Users/xue/.local/bin/uv run --directory /Users/xue/finviz_to_tv main.py
