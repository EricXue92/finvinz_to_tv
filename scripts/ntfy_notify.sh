#!/usr/bin/env bash
# Called by `ntfy subscribe TOPIC HELPER` once per message. Bridges the
# message to macOS Notification Center via osascript, and updates the
# last-seen-timestamp state file so the subscriber wrapper can resume
# from the right offset after a sleep/launchd restart.
#
# Env vars provided by ntfy CLI:
#   $NTFY_TIME, $NTFY_TITLE, $NTFY_MESSAGE, $NTFY_PRIORITY (1=min..5=max)
set -u

STATE_FILE=/Users/xue/momentum-scanner/output/state/ntfy_last_seen.txt
LOG=/Users/xue/momentum-scanner/output/launchd_ntfy.log

title="${NTFY_TITLE:-ntfy}"
message="${NTFY_MESSAGE:-}"
priority="${NTFY_PRIORITY:-3}"

if [[ -n "${NTFY_TIME:-}" ]]; then
    printf '%s' "$NTFY_TIME" > "$STATE_FILE"
fi

# AppleScript string-escape: backslash first, then double-quote.
title_esc=${title//\\/\\\\}
title_esc=${title_esc//\"/\\\"}
message_esc=${message//\\/\\\\}
message_esc=${message_esc//\"/\\\"}

sound=""
if [[ "$priority" =~ ^[0-9]+$ ]] && (( priority >= 4 )); then
    sound=' sound name "Glass"'
fi

/usr/bin/osascript -e "display notification \"$message_esc\" with title \"$title_esc\"$sound" || true
printf '[%s] %s :: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$title" "$message" >> "$LOG"
