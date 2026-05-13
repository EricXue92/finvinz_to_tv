#!/usr/bin/env bash
# Long-running subscriber for the morning-gap ntfy topic. Bridges every
# incoming message to macOS Notification Center via ntfy_notify.sh.
#
# Resumption strategy: ntfy_notify.sh persists each message's Unix time
# to output/state/ntfy_last_seen.txt. On startup we resume from
# (last_seen + 1) so a sleep/launchd restart replays only messages we
# missed. On a fresh run with no state file we default to 12h ago
# (= ntfy.sh free-tier retention window).
#
# launchd plist runs this with KeepAlive=true so any exit triggers a
# restart (ThrottleInterval=10s prevents storm).
set -euo pipefail

LOG=/Users/xue/finviz_to_tv/output/launchd_ntfy.log
mkdir -p "$(dirname "$LOG")"
if [[ -f "$LOG" && "$(date -r "$LOG" +%Y-%m-%d)" != "$(date +%Y-%m-%d)" ]]; then
    : > "$LOG"
fi
exec >> "$LOG" 2>&1

STATE_DIR=/Users/xue/finviz_to_tv/output/state
mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/ntfy_last_seen.txt"

NTFY=/opt/homebrew/bin/ntfy
TOPIC=xue-finviz-morning-gap-9f3k2
HELPER=/Users/xue/finviz_to_tv/scripts/ntfy_notify.sh

if [[ -f "$STATE_FILE" ]]; then
    last=$(cat "$STATE_FILE" 2>/dev/null || echo "")
    if [[ "$last" =~ ^[0-9]+$ ]]; then
        SINCE=$((last + 1))
    else
        SINCE=$(( $(date +%s) - 43200 ))
    fi
else
    SINCE=$(( $(date +%s) - 43200 ))
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting ntfy subscriber, topic=$TOPIC since=$SINCE"
exec "$NTFY" subscribe --since="$SINCE" "$TOPIC" "$HELPER"
