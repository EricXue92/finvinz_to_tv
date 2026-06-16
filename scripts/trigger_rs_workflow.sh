#!/usr/bin/env bash
# Pre-EOD trigger: dispatches a GH Actions workflow via `gh workflow run` so
# the pipeline doesn't depend on GH's scheduled cron, which is notoriously
# delayed (5+ hours observed) or skipped entirely under GH load. The
# workflow's commit step is idempotent (git diff --staged --quiet → exit 0),
# so this is safe even if GH's own cron also fires later for the same day.
#
# Watches the run and ntfy-alerts on failure so a stale-fallback EOD doesn't
# get noticed only after the run completes silently.
#
# Usage: trigger_rs_workflow.sh <workflow-file> <market-label>
#   e.g.: trigger_rs_workflow.sh update_us_rs_3m.yml US
set -uo pipefail

WORKFLOW="${1:?workflow file required (e.g. update_us_rs_3m.yml)}"
MARKET="${2:?market label required (US or HK)}"
REPO="EricXue92/momentum-scanner"
GH=/opt/homebrew/bin/gh

LOG=/Users/xue/momentum-scanner/output/launchd_rs_trigger.log
mkdir -p "$(dirname "$LOG")"

# Rotate on date change (same pattern as run_eod.sh).
if [[ -f "$LOG" && "$(date -r "$LOG" +%Y-%m-%d)" != "$(date +%Y-%m-%d)" ]]; then
    : > "$LOG"
fi

exec >> "$LOG" 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# launchd inherits no PATH; gh shells out to git which expects a usable PATH.
export PATH="/opt/homebrew/bin:/usr/bin:/bin"

# ntfy topic is reused from the morning-gap subscriber so all pipeline alerts
# land in the same place (config.toml [notify]).
NTFY_TOPIC="xue-finviz-morning-gap-9f3k2"
ntfy_alert() {
    local title="$1" body="$2"
    /usr/bin/curl -fsS -m 10 \
        -H "Title: $title" \
        -H "Priority: high" \
        -d "$body" \
        "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

echo
echo "[$(ts)] [trigger:$MARKET] dispatching $WORKFLOW"

if ! "$GH" --repo "$REPO" workflow run "$WORKFLOW" 2>&1; then
    echo "[$(ts)] [trigger:$MARKET] gh workflow run FAILED (auth? rate limit?)"
    ntfy_alert "$MARKET RS trigger failed" \
        "gh workflow run $WORKFLOW failed — likely auth issue. Check 'gh auth status'."
    exit 1
fi

# Give the API a moment so the new run shows up in `gh run list`.
sleep 5

RUN_ID=$("$GH" --repo "$REPO" run list \
    --workflow "$WORKFLOW" --event workflow_dispatch --limit 1 \
    --json databaseId --jq '.[0].databaseId' 2>/dev/null)

if [[ -z "$RUN_ID" ]]; then
    echo "[$(ts)] [trigger:$MARKET] could not resolve run ID — dispatched but not watched"
    exit 0
fi

echo "[$(ts)] [trigger:$MARKET] dispatched run $RUN_ID, watching"

# `gh run watch` polls every 3s and blocks until the run finishes. Historical
# runtimes: US ~10-12 min, HK ~3-5 min.
if "$GH" --repo "$REPO" run watch "$RUN_ID" --exit-status >/dev/null 2>&1; then
    echo "[$(ts)] [trigger:$MARKET] run $RUN_ID completed successfully"
else
    echo "[$(ts)] [trigger:$MARKET] run $RUN_ID FAILED — EOD will use stale fallback"
    ntfy_alert "$MARKET RS workflow failed" \
        "Run $RUN_ID failed. EOD will fall back to yesterday's CSV (≤3 days OK)."
    exit 1
fi
