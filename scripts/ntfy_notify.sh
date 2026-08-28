#!/usr/bin/env bash
# Called by `ntfy subscribe TOPIC HELPER` once per message. Bridges the
# message to macOS Notification Center via osascript, dedupes replayed
# messages by ID, and updates the last-seen-timestamp state file so the
# subscriber wrapper can resume from the right offset after a restart.
#
# Dedup: the ntfy CLI reuses its ORIGINAL --since on every internal
# reconnect, replaying all messages still in the ntfy.sh retention
# window (observed 2026-08-27/28: same push fired 3 banners across
# reconnects). Each message's unique $NTFY_ID is recorded in a seen-IDs
# file (trimmed to the last 200); an already-seen ID exits without
# notifying. Missing $NTFY_ID degrades to always-notify, never silence.
#
# Env vars provided by ntfy CLI:
#   $NTFY_ID, $NTFY_TIME, $NTFY_TITLE, $NTFY_MESSAGE, $NTFY_PRIORITY (1=min..5=max)
#
# NTFY_BRIDGE_* overrides exist for the test suite (hermetic tmp paths).
set -u

STATE_FILE="${NTFY_BRIDGE_STATE_FILE:-/Users/xue/momentum-scanner/output/state/ntfy_last_seen.txt}"
IDS_FILE="${NTFY_BRIDGE_IDS_FILE:-/Users/xue/momentum-scanner/output/state/ntfy_seen_ids.txt}"
LOG="${NTFY_BRIDGE_LOG:-/Users/xue/momentum-scanner/output/launchd_ntfy.log}"
NOTIFIER="${NTFY_BRIDGE_NOTIFIER:-/usr/bin/osascript}"

title="${NTFY_TITLE:-ntfy}"
message="${NTFY_MESSAGE:-}"
priority="${NTFY_PRIORITY:-3}"
msg_id="${NTFY_ID:-}"

# Advance last_seen monotonically — a replayed old message must not
# rewind the wrapper's resume offset.
if [[ "${NTFY_TIME:-}" =~ ^[0-9]+$ ]]; then
    last=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
    [[ "$last" =~ ^[0-9]+$ ]] || last=0
    if (( NTFY_TIME > last )); then
        printf '%s' "$NTFY_TIME" > "$STATE_FILE"
    fi
fi

if [[ -n "$msg_id" ]]; then
    if grep -qxF "$msg_id" "$IDS_FILE" 2>/dev/null; then
        printf '[%s] dup skipped (%s) :: %s\n' \
            "$(date '+%Y-%m-%d %H:%M:%S')" "$msg_id" "$title" >> "$LOG"
        exit 0
    fi
    printf '%s\n' "$msg_id" >> "$IDS_FILE"
    if (( $(wc -l < "$IDS_FILE") > 200 )); then
        tail -n 200 "$IDS_FILE" > "$IDS_FILE.tmp" && mv "$IDS_FILE.tmp" "$IDS_FILE"
    fi
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

"$NOTIFIER" -e "display notification \"$message_esc\" with title \"$title_esc\"$sound" || true
printf '[%s] %s :: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$title" "$message" >> "$LOG"
