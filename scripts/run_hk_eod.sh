#!/usr/bin/env bash
# Wrapper for the HK EOD launchd job (20:00 HKT, Mon-Fri). Mirrors run_eod.sh
# but writes its own log file (launchd_HK.log) and runs --mode hk-eod so the
# US pipeline is skipped. HK market closes at 16:00 HKT; the 20:00 slot leaves
# 4 hours for k-line data to finalize.
set -euo pipefail

LOG=/Users/xue/finviz_to_tv/output/launchd_HK.log
mkdir -p "$(dirname "$LOG")"

if [[ -f "$LOG" && "$(date -r "$LOG" +%Y-%m-%d)" != "$(date +%Y-%m-%d)" ]]; then
    : > "$LOG"
fi

exec >> "$LOG" 2>&1

# Load secrets (ANTHROPIC_API_KEY for the report step) from the project's
# .env (gitignored). Launchd does NOT inherit the user's interactive shell
# environment.
ENV_FILE=/Users/xue/finviz_to_tv/.env
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
fi

UV=/Users/xue/.local/bin/uv
PROJECT=/Users/xue/finviz_to_tv

"$UV" run --directory "$PROJECT" main.py --mode hk-eod
# Under `set -e`, a non-zero EOD exits the script here; the report only runs on
# success. EOD_STATUS is preserved so launchd sees the EOD's true exit code
# even if the report step (below) is reached and itself fails.
EOD_STATUS=$?

# Report is a soft side-effect; failures here must not turn the EOD run red.
set +e
"$UV" run --directory "$PROJECT" main.py --mode report --market hk
set -e

exit $EOD_STATUS
