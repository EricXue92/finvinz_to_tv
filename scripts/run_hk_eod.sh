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

UV=/Users/xue/.local/bin/uv
PROJECT=/Users/xue/finviz_to_tv

"$UV" run --directory "$PROJECT" main.py --mode hk-eod
EOD_STATUS=$?

set +e
"$UV" run --directory "$PROJECT" main.py --mode report --market hk
set -e

exit $EOD_STATUS
