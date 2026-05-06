#!/usr/bin/env bash
# Wrapper for the US EOD launchd job (10:00 HKT, Tue-Sat). Two responsibilities:
#   1. Rotate output/launchd_US.log if its last-modification date is not today,
#      so each calendar day starts with a fresh log. Multiple same-day
#      invocations (launchd + manual reruns) still append.
#   2. Redirect stdout/stderr to the log itself. The matching plist
#      (com.xue.finviz-to-tv.plist) therefore does NOT set
#      StandardOutPath/StandardErrorPath — launchd would otherwise hold
#      an append-mode fd open through any in-process truncate, leaving
#      a NUL-padded hole at the start of the new day's log.
#
# HK scanning has its own slot at 20:00 HKT — see run_hk_eod.sh /
# com.xue.finviz-to-tv.hk-eod.plist. The US slot uses --mode us-eod so HK is
# explicitly skipped here; running HK at 10 AM HKT would pull incomplete
# intraday k-line bars (HK market opens 09:30 HKT, just 30 min before).
set -euo pipefail

LOG=/Users/xue/finviz_to_tv/output/launchd_US.log
mkdir -p "$(dirname "$LOG")"

if [[ -f "$LOG" && "$(date -r "$LOG" +%Y-%m-%d)" != "$(date +%Y-%m-%d)" ]]; then
    : > "$LOG"
fi

exec >> "$LOG" 2>&1
exec /Users/xue/.local/bin/uv run --directory /Users/xue/finviz_to_tv main.py --mode us-eod
