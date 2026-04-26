#!/usr/bin/env python3
"""Schedule pmset wake events for the intraday morning-gap scans.

The launchd job triggers at 21:40-22:00 HKT (EDT) or 22:40-23:00 HKT (EST).
This script schedules Mac wake-ups 11 min before each window's first scan:
  - 21:29 HKT covers EDT (NY 9:30 AM)
  - 22:29 HKT covers EST (NY 9:30 AM)

`pmset schedule` events are one-shot. Re-run periodically (e.g. weekly via
cron/launchd or manually). Re-running is safe — past events expire on their
own, duplicate future events are harmless extra wakes.

Usage:
    sudo uv run scripts/schedule_morning_gap_wakes.py [days]

Default: schedules next 14 weekdays. Verify with `pmset -g sched`.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WAKE_TIMES = ["21:29:00", "22:29:00"]
DEFAULT_DAYS = 14


def main() -> int:
    if os.geteuid() != 0:
        print("ERROR: pmset schedule requires root. Re-run with sudo.", file=sys.stderr)
        return 1

    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DAYS
    now_hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))

    scheduled = 0
    skipped_past = 0
    failed = 0
    d = now_hk.date()
    days_processed = 0
    while days_processed < n_days:
        if d.weekday() < 5:  # Mon-Fri
            for t in WAKE_TIMES:
                wake_dt = datetime.combine(
                    d,
                    datetime.strptime(t, "%H:%M:%S").time(),
                    tzinfo=ZoneInfo("Asia/Hong_Kong"),
                )
                if wake_dt <= now_hk:
                    skipped_past += 1
                    continue

                schedule_str = f"{d:%m/%d/%y} {t}"
                cmd = ["pmset", "schedule", "wake", schedule_str]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    print(f"  scheduled: {schedule_str}")
                    scheduled += 1
                else:
                    print(
                        f"  FAILED: {schedule_str} — {result.stderr.strip()}",
                        file=sys.stderr,
                    )
                    failed += 1
            days_processed += 1
        d += timedelta(days=1)

    print(
        f"\nDone: {scheduled} scheduled, {skipped_past} in past skipped, {failed} failed."
    )
    print("Verify: pmset -g sched")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
