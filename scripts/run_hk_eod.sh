#!/usr/bin/env bash
# Wrapper for the HK EOD launchd job (20:00 HKT, Mon-Fri). Mirrors run_eod.sh
# but writes its own log file (launchd_HK.log) and runs --mode hk-eod so the
# US pipeline is skipped. HK market closes at 16:00 HKT; the 20:00 slot leaves
# 4 hours for k-line data to finalize.
set -euo pipefail

LOG=/Users/xue/momentum-scanner/output/launchd_HK.log
mkdir -p "$(dirname "$LOG")"

if [[ -f "$LOG" && "$(date -r "$LOG" +%Y-%m-%d)" != "$(date +%Y-%m-%d)" ]]; then
    : > "$LOG"
fi

exec >> "$LOG" 2>&1

# Load secrets (ANTHROPIC_API_KEY for the anthropic report backend, or
# DEEPSEEK_API_KEY + TAVILY_API_KEY for the deepseek backend) from the
# project's .env (gitignored). Launchd does NOT inherit the user's
# interactive shell environment.
ENV_FILE=/Users/xue/momentum-scanner/.env
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
fi

UV=/Users/xue/.local/bin/uv
PROJECT=/Users/xue/momentum-scanner

# Hard wall-clock cap — HK pulls ~2,400 tickers from yfinance + Futu snapshots,
# any of which can hang on a half-closed socket (see run_eod.sh comment). HK
# legitimately takes longer than US so the default ceiling is higher. macOS
# has no `timeout`; `set -m` puts the job in its own pgrp so the watchdog can
# kill uv's python grandchild along with the parent.
EOD_TIMEOUT=${EOD_TIMEOUT:-2700}
set +e
set -m
"$UV" run --directory "$PROJECT" main.py --mode hk-eod &
EOD_PID=$!
set +m
( sleep "$EOD_TIMEOUT" && kill -TERM "-$EOD_PID" 2>/dev/null \
    && sleep 15 && kill -KILL "-$EOD_PID" 2>/dev/null ) &
WATCHDOG_PID=$!
wait "$EOD_PID"
EOD_STATUS=$?
kill "$WATCHDOG_PID" 2>/dev/null
wait "$WATCHDOG_PID" 2>/dev/null
set -e

# Report is a soft side-effect; failures here must not turn the EOD run red.
set +e
"$UV" run --directory "$PROJECT" main.py --mode report --market hk
set -e

exit $EOD_STATUS
