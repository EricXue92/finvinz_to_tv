# Morning Gap Up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an intraday `--mode morning-gap` scanner to `main.py` that runs at +10/+15/+20/+25/+30 min after US market open, filters stocks gapping up with cumulative volume ≥ their 20-day average daily volume, and writes results to `output/US/MorningGap.txt`.

**Architecture:** Single-file extension of `main.py`. New helpers compute current ET-time-based scan offset and apply intraday cumulative volume filter. New orchestration function `run_morning_gap` reuses existing `_yf_download_with_retry`, `_filter_dollar_volume_from_data`, `_trim_today`, `safe_write_watchlist`. `main()` adds `--mode` argparse flag dispatching between EOD (default) and morning-gap. Scheduling via a new launchd plist with 10 calendar entries (5 EDT + 5 EST), DST handled implicitly by ET self-validation in script.

**Tech Stack:** Python 3.12, yfinance (1m + 1d intervals), finviz, launchd. No test framework — verification is end-to-end run + log inspection (matches existing project style).

**Spec:** `docs/superpowers/specs/2026-04-27-morning-gap-design.md`

---

## File Structure

- **Modify** `main.py`:
  - Add helper `_get_et_scan_offset` (after `check_market_down`)
  - Add helper `_filter_intraday_cumulative_volume` (after `_filter_dollar_volume_from_data`)
  - Add orchestration function `run_morning_gap` (after `filter_shorts`)
  - Refactor `main()`: add argparse with `--mode`, dispatch on mode, isolate existing EOD code under `if mode == "eod":` branch
- **Modify** `config.toml`: add `[morning_gap]` section
- **Modify** `README.md`: add intraday usage section
- **Create** `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist`

---

### Task 1: Add `[morning_gap]` config section

**Files:**
- Modify: `config.toml` — append at end

- [ ] **Step 1: Append config section**

Append to `config.toml`:

```toml

# --- Morning Gap: 盘中扫描 (开盘后 10/15/20/25/30 分钟运行) ---

[morning_gap]
name = "Morning Gap Up"
filters = ["sh_avgvol_o500","sh_price_o10","ta_beta_o1.5","ta_gap_u5","ta_sma200_pa"]
avg_volume_days = 20
min_dollar_volume = 100_000_000
scan_offsets = [10, 15, 20, 25, 30]
archive_offset = 30
offset_tolerance_minutes = 2
```

- [ ] **Step 2: Verify TOML parses**

Run: `uv run python -c "import tomllib; tomllib.load(open('config.toml','rb')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add config.toml
git commit -m "config: add morning_gap section"
```

---

### Task 2: Add `_get_et_scan_offset` helper

**Files:**
- Modify: `main.py` — insert after `check_market_down` function (around line 583)

- [ ] **Step 1: Add helper function**

Insert after `check_market_down` (which ends at the `return` line near 583):

```python
def _get_et_scan_offset(
    scan_offsets: list[int], tolerance_minutes: int
) -> int | None:
    """Determine which scan offset (in minutes after 9:30 ET) the current
    ET time matches, within tolerance. Returns None if outside any window
    or outside trading hours / weekend."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return None
    market_open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes_since_open = (now_et - market_open_et).total_seconds() / 60
    if minutes_since_open < 0 or minutes_since_open > max(scan_offsets) + tolerance_minutes:
        return None
    best = min(scan_offsets, key=lambda o: abs(o - minutes_since_open))
    if abs(best - minutes_since_open) <= tolerance_minutes:
        return best
    return None
```

- [ ] **Step 2: Smoke test the helper**

Run:
```bash
uv run python -c "
from main import _get_et_scan_offset
print(_get_et_scan_offset([10,15,20,25,30], 2))
"
```
Expected: prints `None` (current time is unlikely to be within scan window, unless you happen to run during 9:30-10:02 ET on a weekday).

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add _get_et_scan_offset helper for intraday scheduling"
```

---

### Task 3: Add `_filter_intraday_cumulative_volume` helper

**Files:**
- Modify: `main.py` — insert after `_filter_dollar_volume_from_data` (around line 532)

- [ ] **Step 1: Add helper function**

Insert after `_filter_dollar_volume_from_data` (which ends around line 532):

```python
def _filter_intraday_cumulative_volume(
    tickers: list[str],
    intraday_data,
    avg_daily_volumes: dict[str, float],
    offset_minutes: int,
) -> list[str]:
    """For each ticker, sum the 1-minute volume bars from 9:30 ET up to
    9:30 + offset_minutes ET (exclusive of the +offset bar) and keep
    tickers whose cumulative volume >= their 20-day average daily volume.
    Strict: tickers with missing data are dropped."""
    if not tickers:
        return []

    et = ZoneInfo("America/New_York")
    today_et = datetime.now(et).date()
    open_ts = datetime.combine(today_et, datetime.min.time(), tzinfo=et).replace(hour=9, minute=30)
    end_ts = open_ts + timedelta(minutes=offset_minutes)

    single = len(tickers) == 1
    result = []
    for ticker in tickers:
        try:
            if single:
                volumes = intraday_data["Volume"].dropna()
            else:
                volumes = intraday_data[ticker]["Volume"].dropna()

            if len(volumes) == 0:
                logger.warning(f"  yfinance 1m: no data for {ticker}, dropping")
                continue

            # yfinance 1m index is tz-aware in ET (or UTC depending on version).
            # Normalize to ET for comparison.
            idx = volumes.index
            if idx.tz is None:
                idx = idx.tz_localize("America/New_York")
            else:
                idx = idx.tz_convert("America/New_York")
            mask = (idx >= open_ts) & (idx < end_ts)
            cumulative = volumes[mask.values].sum()

            avg_daily = avg_daily_volumes.get(ticker)
            if avg_daily is None or avg_daily <= 0:
                continue

            if cumulative >= avg_daily:
                result.append(ticker)
        except (KeyError, TypeError, AttributeError):
            logger.warning(f"  yfinance 1m: failed to process {ticker}, dropping")

    return result
```

- [ ] **Step 2: Add `timedelta` import**

In `main.py` top, change:
```python
from datetime import date, datetime
```
to:
```python
from datetime import date, datetime, timedelta
```

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from main import _filter_intraday_cumulative_volume; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add _filter_intraday_cumulative_volume helper"
```

---

### Task 4: Add `run_morning_gap` orchestration function

**Files:**
- Modify: `main.py` — insert after `filter_shorts` (around line 363)

- [ ] **Step 1: Add the orchestration function**

Insert after `filter_shorts` ends (around line 363, before `def filter_consecutive_up_days`):

```python
def run_morning_gap(config: dict) -> tuple[int, list[str]]:
    """Run the intraday morning-gap scan. Determines current scan offset
    from ET time, runs Finviz screener, applies dollar-volume and
    intraday cumulative volume filters. Returns (offset, tickers).
    Returns (-1, []) if outside scan window."""
    scan_offsets = config.get("scan_offsets", [10, 15, 20, 25, 30])
    tolerance = config.get("offset_tolerance_minutes", 2)
    offset = _get_et_scan_offset(scan_offsets, tolerance)
    if offset is None:
        logger.info("[Morning Gap] Not in scan window, exiting")
        return -1, []

    logger.info(f"[Morning Gap] Running for offset +{offset}min")

    # Phase 1: Finviz screener
    tickers = run_screener(config["filters"], config.get("signal"))
    logger.info(f"  Found {len(tickers)} tickers from Finviz screener")
    if not tickers:
        return offset, []

    # Phase 2: 20-day daily data — used by both dollar volume and avg volume
    daily_data = _yf_download_with_retry(
        tickers, period="2mo", interval="1d", progress=False,
        group_by="ticker", threads=False,
    )
    if daily_data is None or daily_data.empty:
        logger.warning("  Daily yfinance download failed, exiting")
        return offset, []

    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = True  # we only run during market hours
    today_et = now_et.date()

    # Phase 3: Dollar volume filter
    min_dv = config.get("min_dollar_volume", 0)
    if min_dv > 0:
        tickers = _filter_dollar_volume_from_data(
            tickers, daily_data, min_dv, market_open, today_et, len(tickers) == 1
        )
        logger.info(f"  {len(tickers)} after dollar volume filter (>= ${min_dv:,.0f})")
    if not tickers:
        return offset, []

    # Phase 4: Compute 20-day avg daily volume per ticker
    avg_days = config.get("avg_volume_days", 20)
    single = len(tickers) == 1
    avg_daily_volumes: dict[str, float] = {}
    for ticker in tickers:
        try:
            if single:
                volumes = daily_data["Volume"].dropna()
            else:
                volumes = daily_data[ticker]["Volume"].dropna()
            volumes = _trim_today(volumes, market_open, today_et)
            if len(volumes) < avg_days:
                continue
            avg_daily_volumes[ticker] = float(volumes.iloc[-avg_days:].mean())
        except (KeyError, TypeError):
            continue

    tickers = [t for t in tickers if t in avg_daily_volumes]
    logger.info(f"  {len(tickers)} have sufficient 20-day daily volume data")
    if not tickers:
        return offset, []

    # Phase 5: Pull intraday 1m data and apply cumulative volume filter
    intraday_data = _yf_download_with_retry(
        tickers, period="1d", interval="1m", progress=False,
        group_by="ticker", threads=False,
    )
    if intraday_data is None or intraday_data.empty:
        logger.warning("  Intraday yfinance download failed, exiting")
        return offset, []

    final = _filter_intraday_cumulative_volume(
        tickers, intraday_data, avg_daily_volumes, offset
    )
    logger.info(f"  {len(final)} after intraday cumulative volume filter (offset={offset}m)")

    return offset, final
```

- [ ] **Step 2: Verify function imports**

Run: `uv run python -c "from main import run_morning_gap; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add run_morning_gap orchestration function"
```

---

### Task 5: Add `--mode` argparse and dispatch in `main()`

**Files:**
- Modify: `main.py` — `main()` function (currently around line 610)

- [ ] **Step 1: Add `argparse` import**

In `main.py` top imports, add:
```python
import argparse
```

- [ ] **Step 2: Add argparse to `main()`**

At the very start of `main()`, before `logging.basicConfig(...)`:

```python
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["eod", "morning-gap"],
        default="eod",
        help="eod: run end-of-day scanners (Longs/Shorts/RS/HK Shorts). "
             "morning-gap: run intraday gap-up scanner.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    ...
```

(Keep the rest of the existing `main()` setup — `project_root`, `config_path`, `load_config`, `output_dir.mkdir`, etc. — unchanged.)

- [ ] **Step 3: Wrap existing EOD pipeline under `if args.mode == "eod":`**

After the existing config/output_dir setup (after the line that creates `hk_output_dir`), wrap all existing pipeline code (Longs / Shorts / RS / HK Shorts) under:

```python
    if args.mode == "eod":
        # --- Longs ---
        longs_tickers: set[str] = set()
        ...
        # ... (existing EOD pipeline unchanged, just indented one level) ...
        # --- HK Shorts ---
        hk_shorts_cfg = config.get("hk_shorts")
        if hk_shorts_cfg:
            ...

        logger.info("Done.")
        return 0
```

(Indent all existing EOD code one level. Move `logger.info("Done.")` and `return 0` inside the `if` block.)

- [ ] **Step 4: Add morning-gap branch**

Add after the EOD block, before the function ends:

```python
    if args.mode == "morning-gap":
        morning_cfg = config.get("morning_gap")
        if not morning_cfg:
            logger.error("[Morning Gap] No [morning_gap] config section found")
            return 1

        logger.info(f"[Morning Gap] Running: {morning_cfg['name']}")
        try:
            offset, tickers = run_morning_gap(morning_cfg)
        except Exception as e:
            logger.warning(f"[Morning Gap] Failed: {e}")
            return 1

        if offset == -1:
            return 0  # Outside scan window — not an error

        if tickers:
            sorted_tickers = sorted(set(tickers))
            if safe_write_watchlist(sorted_tickers, us_output_dir / "MorningGap.txt", fmt):
                logger.info(
                    f"[Morning Gap] +{offset}min: {len(sorted_tickers)} tickers "
                    f"-> output/US/MorningGap.txt"
                )
                if offset == morning_cfg.get("archive_offset", 30):
                    today_str = date.today().strftime("%Y_%m_%d")
                    safe_write_watchlist(
                        sorted_tickers,
                        us_output_dir / f"{today_str}_MorningGap.txt",
                        fmt,
                    )
        else:
            logger.warning("[Morning Gap] No tickers passed filters")

        logger.info("Done.")
        return 0

    return 0
```

(Note: this branch references `us_output_dir` and `fmt`, which are set in the shared setup code BEFORE the `if args.mode == "eod"` block. Make sure `us_output_dir` creation happens unconditionally — move it above the `if args.mode == "eod":` line if it's currently inside.)

- [ ] **Step 5: Verify `us_output_dir` is unconditional**

Read `main.py` and confirm `us_output_dir = output_dir / "US"` and `us_output_dir.mkdir(exist_ok=True)` happen BEFORE the `if args.mode == "eod":` line. If they're inside the EOD block, move them above.

- [ ] **Step 6: Smoke test EOD path still works (no behavior change)**

Run: `uv run python -c "
import argparse, sys
sys.argv = ['main.py']
from main import main
" 2>&1 | head -5`
Expected: argparse parses `--mode=eod` (default), no crash on entry.

(Don't run the full EOD pipeline — that hits Finviz. Just verify imports + arg parsing.)

- [ ] **Step 7: Smoke test morning-gap path off-hours**

Run on a weekend or outside 9:30-10:02 ET:
```bash
uv run main.py --mode morning-gap 2>&1 | head -20
```
Expected: log shows `[Morning Gap] Not in scan window, exiting`, exit code 0.

- [ ] **Step 8: Commit**

```bash
git add main.py
git commit -m "feat: add --mode CLI flag, dispatch eod vs morning-gap"
```

---

### Task 6: Create launchd plist for morning-gap

**Files:**
- Create: `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist`

- [ ] **Step 1: Determine `uv` binary path and project path**

Run:
```bash
which uv
echo "$HOME/finviz_to_tv"  # adjust to actual project root
```
Note both paths. Likely `uv` is at `/opt/homebrew/bin/uv` or `~/.local/bin/uv`.

- [ ] **Step 2: Create the plist file**

Write `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.xue.finviz-to-tv.morning-gap</string>

  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/uv</string>
    <string>run</string>
    <string>main.py</string>
    <string>--mode</string>
    <string>morning-gap</string>
  </array>

  <key>WorkingDirectory</key>
  <string>/Users/xue/finviz_to_tv</string>

  <key>StartCalendarInterval</key>
  <array>
    <!-- EDT: NY 9:30 = HKT 21:30 -->
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    <!-- EST: NY 9:30 = HKT 22:30 -->
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>40</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>45</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>50</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>55</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
  </array>

  <key>StandardOutPath</key>
  <string>/tmp/finviz-to-tv-morning-gap.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/finviz-to-tv-morning-gap.err</string>
</dict>
</plist>
```

**Note:** Adjust `<string>/opt/homebrew/bin/uv</string>` to the actual `uv` path from Step 1, and confirm `/Users/xue/finviz_to_tv` matches the project root.

- [ ] **Step 3: Validate plist syntax**

Run: `plutil -lint ~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist`
Expected: `OK`

- [ ] **Step 4: Load the plist**

Run:
```bash
launchctl unload ~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist
launchctl list | grep morning-gap
```
Expected: last command shows the job listed.

- [ ] **Step 5: Commit**

(plist lives outside the repo; no commit needed for plist itself, but if you want to track a copy in repo for documentation, add to a `launchd/` subdir. Skip otherwise.)

---

### Task 7: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current README**

Run: `cat README.md` and identify a logical place to add the intraday section (likely after the existing schedule explanation).

- [ ] **Step 2: Append intraday section**

Add a new section to `README.md`:

```markdown
## Intraday Morning Gap Scanner

Run the intraday gap-up scanner manually:
```bash
uv run main.py --mode morning-gap
```

The script auto-detects the current US ET time and runs the matching scan
(+10/+15/+20/+25/+30 minutes after market open, ±2 min tolerance).
Outside this window it logs and exits.

Output: `output/US/MorningGap.txt` (overwritten each scan), with a dated
archive `output/US/YYYY_MM_DD_MorningGap.txt` written only at the +30min scan.

### Schedule

Loaded via `~/Library/LaunchAgents/com.xue.finviz-to-tv.morning-gap.plist`,
50 calendar entries (Mon–Fri × 5 offsets × EDT/EST). DST is handled by the
script itself: each plist trigger validates current ET time and either runs
the matching scan or exits if outside the window.

### Wake-up

Existing `pmset repeat wake MTWRF 8:29` is unchanged. The intraday scanner
assumes Mac is awake during 21:00–23:00 HKT (typical evening use). If the
Mac sleeps during this window, scans will be missed — add a `pmset schedule`
entry per day if needed (not configured by default).
```
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add intraday morning-gap scanner section to README"
```

---

### Task 8: End-to-end verification

**Files:** none (runtime checks only)

- [ ] **Step 1: Off-hours dry run**

Run: `uv run main.py --mode morning-gap`
Expected: log line `[Morning Gap] Not in scan window, exiting`, no file written.

- [ ] **Step 2: In-hours dry run (during 21:38-22:02 HKT EDT, or 22:38-23:02 HKT EST, on a weekday)**

Run: `uv run main.py --mode morning-gap`
Expected:
- Log shows `[Morning Gap] Running for offset +Nmin` where N ∈ {10,15,20,25,30}
- Log shows Finviz screener count, dollar volume filter count, intraday cumulative volume filter count
- `output/US/MorningGap.txt` is written
- If offset == 30: `output/US/{today}_MorningGap.txt` also written

- [ ] **Step 3: Confirm EOD pipeline still works**

Run: `uv run main.py` (no flag)
Expected: existing EOD output (Longs / Shorts / RS / HK Shorts) unchanged. Spot-check `output/US/Longs.txt` and `output/US/Shorts.txt` are not corrupted.

- [ ] **Step 4: Verify launchd job has fired**

Wait for next scheduled trigger (next 21:40-22:00 HKT or 22:40-23:00 HKT weekday). Then:
```bash
tail -50 /tmp/finviz-to-tv-morning-gap.log
launchctl list | grep morning-gap
```
Expected: log shows recent run; `LastExitStatus` is 0.

- [ ] **Step 5: Verify safe_write guard during a market holiday or empty result**

If on a holiday: run `uv run main.py --mode morning-gap`. yfinance 1m should return empty → 0 final tickers → `safe_write_watchlist` triggers 50% guard → previous `MorningGap.txt` is preserved.

If can't wait for a holiday, simulate by temporarily setting `min_dollar_volume = 999_999_999_999` in config (impossible threshold) → 0 tickers pass → previous file preserved. Restore config after.

---

## Self-Review

**Spec coverage:**
- Filter standards (sh_avgvol/price/beta/gap/sma200) → Task 1 (config)
- Dollar volume filter → Task 4 (uses `_filter_dollar_volume_from_data`)
- Intraday cumulative volume filter → Task 3 + Task 4
- 5 scan offsets [10,15,20,25,30] → Task 1 + Task 2 (`_get_et_scan_offset`)
- ET time auto-detection → Task 2
- DST handled by ET self-validation → Task 2 + Task 6 (50 plist entries)
- Single output file overwrite → Task 5 (main dispatch)
- Dated archive at +30min only → Task 5 (`if offset == archive_offset`)
- safe_write_watchlist guard → Task 5 (uses existing function)
- Holiday graceful skip → Task 8 step 5
- `--mode` CLI flag → Task 5
- Backward compat (no-arg = EOD) → Task 5 (default `eod`)
- launchd plist with EDT+EST entries → Task 6
- pmset note → Task 7

**Placeholder scan:** No "TBD" / "TODO" / "implement later" remain. All code blocks are concrete.

**Type consistency:** `_get_et_scan_offset` returns `int | None` — used as `int` in `run_morning_gap` after None check. `run_morning_gap` returns `tuple[int, list[str]]` with `-1` sentinel for out-of-window — checked in main dispatch (Task 5 step 4). `_filter_intraday_cumulative_volume` takes `intraday_data` (yfinance DataFrame) and `avg_daily_volumes: dict[str, float]` — both populated in `run_morning_gap`. Consistent.
