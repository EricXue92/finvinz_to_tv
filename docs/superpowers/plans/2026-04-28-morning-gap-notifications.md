# Morning Gap Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push an ntfy notification (phone + Mac browser) whenever a Morning Gap scan surfaces tickers that have not appeared in any earlier morning-gap scan today.

**Architecture:** A new `notify.py` module owns the HTTP POST to ntfy.sh. `main.py` gains a small helper `_morning_gap_new_tickers` that maintains a per-day state file (`output/state/morning_gap_seen_<date>.txt`) and computes the diff. The morning-gap branch in `main.py` calls the helper after a successful `safe_write_watchlist` and forwards any new tickers to `notify_morning_gap`. All notification failures are caught and logged as warnings — they never break the scan, matching the `_futu_sync` contract.

**Tech Stack:** Python stdlib only (`urllib.request`, `pathlib`). No new dependency.

**Spec:** `docs/superpowers/specs/2026-04-28-morning-gap-notifications-design.md`

**Project conventions (read before starting):**
- This repo has no `tests/` directory and no test framework. Verification is manual: run a real or simulated morning-gap invocation and check log output / observe the ntfy push.
- All cross-cutting "soft side-effect" calls (Futu sync, future notifications) follow the contract: catch every exception inside the helper, log a warning, return. Never raise into the scan pipeline.
- File paths are absolute or anchored at `output_dir` from `[settings]`. The morning-gap branch already has `output_dir`, `us_output_dir`, `today`, `offset`, `tickers` in scope around line 1093-1103 of `main.py`.

---

## Task 1: Add `[notify]` config section

**Files:**
- Modify: `/Users/xue/finviz_to_tv/config.toml` (append at end of file)

- [ ] **Step 1: Append the new section**

Open `/Users/xue/finviz_to_tv/config.toml` and append the following block at the end of the file (after the last existing section):

```toml

# --- 推送通知 (ntfy.sh) ---
# Morning-gap 扫描发现"新"票时推送到 phone + Mac 浏览器。
# 同一交易日内已出现过的票不会重复推送(state 文件: output/state/morning_gap_seen_<date>.txt)。
# Topic 名后缀是随机串 = 私人频道,谁知道 topic 名谁就能订阅。
[notify]
enabled = true
ntfy_topic = "xue-finviz-morning-gap-9f3k2"
ntfy_server = "https://ntfy.sh"
max_tickers_in_body = 10
```

- [ ] **Step 2: Verify the file still parses**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python -c "import tomllib; tomllib.load(open('config.toml','rb')); print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
cd /Users/xue/finviz_to_tv
git add config.toml
git commit -m "$(cat <<'EOF'
feat: add [notify] config section for ntfy push

Topic, server, and ticker truncation cap. Default enabled.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `notify.py` module

**Files:**
- Create: `/Users/xue/finviz_to_tv/notify.py`

- [ ] **Step 1: Create the module with the full implementation**

Create `/Users/xue/finviz_to_tv/notify.py` with these contents:

```python
"""ntfy.sh push notifications. Single side-effect: HTTP POST.

Failures are swallowed and logged. Never raises — same contract as futu_sync.
"""

import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 5.0


def notify_morning_gap(
    new_tickers: list[str],
    offset_min: int,
    total: int,
    config: dict,
) -> None:
    """Push one ntfy notification for a morning-gap scan with new tickers.

    Args:
        new_tickers: tickers that did not appear in any earlier morning-gap
            scan today. Caller computes this diff. Empty list -> no-op.
        offset_min: minutes from market open. Negative = pre-market.
        total: total ticker count for this scan (incl. repeats).
        config: full parsed config.toml dict. Reads [notify] section.
    """
    if not new_tickers:
        return

    notify_cfg = config.get("notify") or {}
    if not notify_cfg.get("enabled", False):
        return

    topic = notify_cfg.get("ntfy_topic")
    if not topic:
        logger.warning("[Notify] ntfy_topic missing in [notify] config")
        return

    server = notify_cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    max_in_body = int(notify_cfg.get("max_tickers_in_body", 10))

    sign = "" if offset_min < 0 else "+"
    title = f"Morning Gap {sign}{offset_min}min · {len(new_tickers)} new"

    shown = new_tickers[:max_in_body]
    extra = len(new_tickers) - len(shown)
    body_parts = [", ".join(shown)]
    if extra > 0:
        body_parts.append(f"(+{extra} more)")
    body_parts.append(f" · total: {total}")
    body = " ".join(body_parts)

    url = f"{server}/{topic}"
    req = Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "default",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            if resp.status >= 400:
                logger.warning(f"[Notify] ntfy POST returned {resp.status}")
            else:
                logger.info(f"[Notify] pushed: {title}")
    except (URLError, TimeoutError, OSError) as e:
        logger.warning(f"[Notify] ntfy POST failed: {e}")
    except Exception as e:
        logger.warning(f"[Notify] unexpected error: {e}")
```

- [ ] **Step 2: Verify it imports cleanly**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python -c "from notify import notify_morning_gap; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Smoke-test against the live ntfy topic**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python -c "
import tomllib
from notify import notify_morning_gap
cfg = tomllib.load(open('config.toml','rb'))
notify_morning_gap(['AAPL','NVDA','TSLA'], -30, 5, cfg)
"
```
Expected: log line `[Notify] pushed: Morning Gap -30min · 3 new`. Confirm with the user that they received a phone notification with that title and body `AAPL, NVDA, TSLA  · total: 5`.

- [ ] **Step 4: Commit**

```bash
cd /Users/xue/finviz_to_tv
git add notify.py
git commit -m "$(cat <<'EOF'
feat: add notify.py with ntfy push for morning-gap scans

Single function notify_morning_gap formats title/body, POSTs to
<ntfy_server>/<ntfy_topic>, swallows all errors. Stdlib only.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `_morning_gap_new_tickers` helper to `main.py`

**Files:**
- Modify: `/Users/xue/finviz_to_tv/main.py` (add helper near other private helpers, e.g. after `_futu_sync` ending around line 49)

- [ ] **Step 1: Add the helper**

Locate `_futu_sync` in `main.py` (currently lines 33-49). Immediately after it, add a blank line and the following helper:

```python
def _morning_gap_new_tickers(
    today: str,
    tickers: list[str],
    output_dir: Path,
) -> list[str]:
    """Return tickers in `tickers` that have not appeared in any earlier
    morning-gap scan today. Side effect: appends `tickers` to the per-day
    state file so subsequent scans see them.

    State lives at `<output_dir>/state/morning_gap_seen_<today>.txt`,
    one ticker per line. File auto-resets each day (filename includes date).

    On any IO error, returns the input tickers unchanged (treats seen-set
    as empty) — better to over-notify than silently swallow new tickers.
    """
    state_dir = output_dir / "state"
    state_path = state_dir / f"morning_gap_seen_{today}.txt"

    seen: set[str] = set()
    try:
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as f:
                seen = {line.strip() for line in f if line.strip()}
    except OSError as e:
        logger.warning(f"[Morning Gap] could not read seen-state {state_path}: {e}")

    current = set(tickers)
    new = sorted(current - seen)

    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        union = sorted(seen | current)
        with state_path.open("w", encoding="utf-8") as f:
            for t in union:
                f.write(f"{t}\n")
    except OSError as e:
        logger.warning(f"[Morning Gap] could not write seen-state {state_path}: {e}")

    return new
```

- [ ] **Step 2: Add the import**

Open `main.py` and locate the `from futu_sync import sync_to_futu` line (currently line 21). Immediately after it, add:

```python
from notify import notify_morning_gap
```

The top imports should now end:
```python
from futu_sync import sync_to_futu
from notify import notify_morning_gap

logger = logging.getLogger(__name__)
```

- [ ] **Step 3: Verify the module still imports**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python -c "import main; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Smoke-test the helper in isolation**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python -c "
from pathlib import Path
import tempfile, shutil
from main import _morning_gap_new_tickers

tmp = Path(tempfile.mkdtemp())
try:
    # First call: everything is new.
    a = _morning_gap_new_tickers('2099_01_01', ['AAPL','NVDA'], tmp)
    assert a == ['AAPL','NVDA'], a

    # Second call with overlap: only the genuinely-new ticker comes back.
    b = _morning_gap_new_tickers('2099_01_01', ['AAPL','TSLA'], tmp)
    assert b == ['TSLA'], b

    # Third call with no new tickers.
    c = _morning_gap_new_tickers('2099_01_01', ['AAPL','NVDA'], tmp)
    assert c == [], c

    # Different date = fresh state file.
    d = _morning_gap_new_tickers('2099_01_02', ['AAPL'], tmp)
    assert d == ['AAPL'], d

    print('ok')
finally:
    shutil.rmtree(tmp)
"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
cd /Users/xue/finviz_to_tv
git add main.py
git commit -m "$(cat <<'EOF'
feat: add _morning_gap_new_tickers helper + notify import

Per-day seen-set file under output/state/. Returns sorted list of tickers
not yet seen today and unions the new ones into the state file. IO errors
fall back to "treat seen-set as empty" so we over-notify rather than miss.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire notification into the morning-gap branch

**Files:**
- Modify: `/Users/xue/finviz_to_tv/main.py` lines 1093-1103 (morning-gap success path)

- [ ] **Step 1: Replace the success block**

Locate the morning-gap branch in `main.py`. The current block reads (around lines 1093-1103):

```python
        if tickers:
            sorted_tickers = sorted(set(tickers))
            dated = us_output_dir / f"{today}_{stem}.txt"
            if safe_write_watchlist(sorted_tickers, dated, fmt):
                logger.info(
                    f"[Morning Gap] {sign}{offset}min: {len(sorted_tickers)} tickers -> {dated}"
                )
                _write_webull(sorted_tickers, dated, output_dir)
                _futu_sync(config, futu_key, sorted_tickers, "US")
        else:
            logger.warning(f"[Morning Gap] {sign}{offset}min: no tickers passed filters")
```

Replace it with:

```python
        if tickers:
            sorted_tickers = sorted(set(tickers))
            dated = us_output_dir / f"{today}_{stem}.txt"
            if safe_write_watchlist(sorted_tickers, dated, fmt):
                logger.info(
                    f"[Morning Gap] {sign}{offset}min: {len(sorted_tickers)} tickers -> {dated}"
                )
                _write_webull(sorted_tickers, dated, output_dir)
                _futu_sync(config, futu_key, sorted_tickers, "US")
                new = _morning_gap_new_tickers(today, sorted_tickers, output_dir)
                if new:
                    notify_morning_gap(new, offset, len(sorted_tickers), config)
        else:
            logger.warning(f"[Morning Gap] {sign}{offset}min: no tickers passed filters")
```

The two added lines call the helper and forward any new tickers to ntfy. Both pre-market (`stem="MorningGapPre"`) and post-open (`stem="MorningGap"`) hit this code path and share the same state file, so a ticker that appeared at -30min won't re-ping at +15min — matching the design.

- [ ] **Step 2: Verify the file still imports**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python -c "import main; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Verify the morning-gap CLI mode is still wired**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python main.py --help 2>&1 | head -20
```
Expected: usage line and a `--mode` arg listing `morning-gap` among the choices. No traceback.

- [ ] **Step 4: Commit**

```bash
cd /Users/xue/finviz_to_tv
git add main.py
git commit -m "$(cat <<'EOF'
feat: push ntfy notification on new morning-gap tickers

After a successful dated .txt write, diff today's seen-state and forward
genuinely-new tickers to notify_morning_gap. Pre-market and post-open
share the same state file so tickers don't re-ping across scans.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: End-to-end verification

**Files:** none (manual run)

- [ ] **Step 1: Run a real morning-gap scan**

The morning-gap mode uses an internal scan-window check (`run_morning_gap` returns `(None, [])` outside the window) so an out-of-window run is a clean no-op. Inside the window, it'll fetch live data and may write `output/TV/US/<today>_MorningGap.txt` or `<today>_MorningGapPre.txt` and trigger a notification.

If we are currently inside a scan window (US pre-market or first hour after open), run:

```bash
cd /Users/xue/finviz_to_tv && uv run python main.py --mode morning-gap
```

Expected log lines (when tickers are found):
- `[Morning Gap] {sign}{offset}min: N tickers -> output/TV/US/<today>_MorningGap*.txt`
- `[Notify] pushed: Morning Gap {sign}{offset}min · K new`

Confirm with the user that they received a phone notification with matching title/body. Confirm `output/state/morning_gap_seen_<today>.txt` was created with the ticker list.

If outside the scan window, the run will finish with `Done.` and no notification — which is correct. In that case, fall back to step 2.

- [ ] **Step 2: Force a mock scan to exercise the full notify path**

Run this script to simulate the morning-gap success path end-to-end without depending on the scan window:

```bash
cd /Users/xue/finviz_to_tv && uv run python -c "
import logging, tomllib, tempfile, shutil
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

from main import _morning_gap_new_tickers
from notify import notify_morning_gap

cfg = tomllib.load(open('config.toml','rb'))
tmp = Path(tempfile.mkdtemp())
try:
    today = date.today().strftime('%Y_%m_%d') + '_PLAN_TEST'
    tickers = sorted(['AAPL','NVDA','TSLA','AMD','GOOG'])
    new = _morning_gap_new_tickers(today, tickers, tmp)
    print('new on first call:', new)
    notify_morning_gap(new, -30, len(tickers), cfg)

    new2 = _morning_gap_new_tickers(today, tickers + ['META'], tmp)
    print('new on second call:', new2)
    notify_morning_gap(new2, 15, len(tickers)+1, cfg)
finally:
    shutil.rmtree(tmp)
"
```

Expected:
- First print: `new on first call: ['AAPL', 'AMD', 'GOOG', 'NVDA', 'TSLA']`
- Log: `[Notify] pushed: Morning Gap -30min · 5 new`
- Second print: `new on second call: ['META']`
- Log: `[Notify] pushed: Morning Gap +15min · 1 new`
- Two phone notifications:
  1. Title `Morning Gap -30min · 5 new`, body `AAPL, AMD, GOOG, NVDA, TSLA  · total: 5`
  2. Title `Morning Gap +15min · 1 new`, body `META  · total: 6`

Confirm with the user that both notifications arrived.

- [ ] **Step 3: Test the disabled-flag short-circuit**

Run:
```bash
cd /Users/xue/finviz_to_tv && uv run python -c "
from notify import notify_morning_gap
notify_morning_gap(['AAPL'], -10, 1, {'notify': {'enabled': False}})
print('ok')
"
```
Expected: `ok` with no log output and **no** phone notification (this confirms the disable flag works).

- [ ] **Step 4: Final commit (only if any small fix-ups were needed)**

If steps 1-3 all passed without modification, no commit is needed. If any tweak was required, commit it:

```bash
cd /Users/xue/finviz_to_tv
git add -p
git commit -m "$(cat <<'EOF'
fix: <describe specific fix found during e2e>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```
