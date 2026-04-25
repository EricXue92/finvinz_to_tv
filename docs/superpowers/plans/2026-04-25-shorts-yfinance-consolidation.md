# Shorts yfinance Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the three `yf.download` calls in the Shorts pipeline into a single download shared by performance, dollar-volume, and consecutive-up-days filters.

**Architecture:** Add two private helpers (`_filter_dollar_volume_from_data`, `_filter_consecutive_up_days_from_data`) that operate on a pre-downloaded DataFrame. Reshape `filter_shorts` to download once via `_yf_download_with_retry`, then chain all three filters on that shared data. Update `main()` to pass the new params.

**Tech Stack:** Python 3, yfinance, finviz, single-file `main.py`. No test framework — verification is end-to-end run + log inspection + diff against existing `output/US/Shorts.txt`.

**Spec:** `docs/superpowers/specs/2026-04-25-shorts-yfinance-consolidation-design.md`

---

## File Structure

Only `main.py` changes. No new files.

- **Modify** `main.py`:
  - Add `_filter_dollar_volume_from_data` (new helper, after `filter_dollar_volume_yf`)
  - Add `_filter_consecutive_up_days_from_data` (new helper, after `filter_consecutive_up_days`)
  - Refactor `filter_shorts` (current main.py:261-333): single download + inline all three filters
  - Update `main()` Shorts block (current main.py:566-597): pass new params, drop separate `filter_dollar_volume_yf` / `filter_consecutive_up_days` calls

`filter_dollar_volume_yf` and `filter_consecutive_up_days` are **kept** for Longs and RS paths.

---

### Task 1: Add `_filter_dollar_volume_from_data` helper

**Files:**
- Modify: `main.py` — add new function after `filter_dollar_volume_yf` (around line 423)

- [ ] **Step 1: Add the helper function**

Insert after `filter_dollar_volume_yf` (which ends around line 423):

```python
def _filter_dollar_volume_from_data(
    tickers: list[str],
    data,
    min_dollar_volume: float,
    market_open: bool,
    today_date,
    days: int = 20,
) -> list[str]:
    """Filter tickers by dollar volume using a pre-downloaded yfinance DataFrame.
    Dollar volume = latest close price * N-day average volume.
    Lenient: tickers with insufficient data are kept."""
    if not tickers:
        return []

    single = len(tickers) == 1
    result = []
    for ticker in tickers:
        try:
            closes, volumes = _get_closes_volumes(data, ticker, single)
            closes = _trim_today(closes, market_open, today_date)
            volumes = _trim_today(volumes, market_open, today_date)

            if len(volumes) < days or len(closes) < 1:
                logger.warning(f"  yfinance: insufficient data for {ticker}, keeping it")
                result.append(ticker)
                continue

            price = closes.iloc[-1]
            avg_vol = volumes.iloc[-days:].mean()

            if price * avg_vol >= min_dollar_volume:
                result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, keeping it")
            result.append(ticker)

    return result
```

Note: reuses existing `_get_closes_volumes` (main.py:69) and `_trim_today` (main.py:76) helpers.

- [ ] **Step 2: Verify no syntax / import errors**

Run: `uv run python -c "import main"`
Expected: no output, no error.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: add _filter_dollar_volume_from_data helper"
```

---

### Task 2: Add `_filter_consecutive_up_days_from_data` helper

**Files:**
- Modify: `main.py` — add new function after `filter_consecutive_up_days` (around line 379)

- [ ] **Step 1: Add the helper function**

Insert after `filter_consecutive_up_days` (which ends around line 379):

```python
def _filter_consecutive_up_days_from_data(
    tickers: list[str],
    data,
    min_days: int,
    market_open: bool,
    today_date,
) -> list[str]:
    """Filter tickers to those with >= min_days consecutive up days,
    using a pre-downloaded yfinance DataFrame.
    Lenient: tickers with no data are kept."""
    if not tickers:
        return []

    single = len(tickers) == 1
    result = []
    for ticker in tickers:
        try:
            if single:
                closes = data["Close"].dropna()
            else:
                closes = data[ticker]["Close"].dropna()
            closes = _trim_today(closes, market_open, today_date)

            if len(closes) < 2:
                logger.warning(f"  yfinance: no data for {ticker}, keeping it")
                result.append(ticker)
                continue

            consecutive = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes.iloc[i] > closes.iloc[i - 1]:
                    consecutive += 1
                else:
                    break

            if consecutive >= min_days:
                result.append(ticker)
        except (KeyError, TypeError):
            logger.warning(f"  yfinance: failed to process {ticker}, keeping it")
            result.append(ticker)

    return result
```

- [ ] **Step 2: Verify no syntax / import errors**

Run: `uv run python -c "import main"`
Expected: no output, no error.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: add _filter_consecutive_up_days_from_data helper"
```

---

### Task 3: Refactor `filter_shorts` to single download + chained filters

**Files:**
- Modify: `main.py:261-333` (the existing `filter_shorts` function)

- [ ] **Step 1: Replace `filter_shorts` body**

Replace the entire current `filter_shorts` function (main.py:261-333) with:

```python
def filter_shorts(
    filters: list[str],
    signal: str | None,
    perf_large_cap: float,
    perf_mid_cap: float,
    perf_small_cap: float,
    min_dollar_volume: float,
    min_consecutive_up_days: int,
) -> tuple[int, list[str]]:
    """Run shorts pipeline: finviz Ownership → single yfinance download →
    performance / dollar-volume / consecutive-up-days filters.
    Returns (total_found, filtered_tickers)."""
    kwargs_own = {"filters": filters, "table": "Ownership"}
    if signal:
        kwargs_own["signal"] = signal
    ownership = Screener(**kwargs_own)
    total = len(ownership.data)

    tickers = []
    market_caps: dict[str, float] = {}
    for stock in ownership.data:
        ticker = stock["Ticker"]
        try:
            cap = parse_number(stock["Market Cap"])
            tickers.append(ticker)
            market_caps[ticker] = cap
        except (KeyError, ValueError):
            continue

    if not tickers:
        return total, []

    # Single yfinance download — shared by all three filters
    data = _yf_download_with_retry(
        tickers, period="2mo", progress=False, group_by="ticker", threads=False
    )

    now_et = datetime.now(ZoneInfo("America/New_York"))
    market_open = 9 <= now_et.hour < 16 and now_et.weekday() < 5
    today_et = now_et.date()
    if market_open:
        logger.info("  US market still open, excluding today's incomplete data")

    single = len(tickers) == 1

    # 1. Performance filter (cap-conditional, 2/3/4-week windows)
    perf_weeks = [2, 3, 4]
    passed: set[str] = set()
    for weeks in perf_weeks:
        trading_days = weeks * 5 + (2 if weeks == 4 else 0)  # 10, 15, 22
        week_hits = 0
        for ticker in tickers:
            if ticker in passed:
                continue
            try:
                if single:
                    closes = data["Close"].dropna()
                else:
                    closes = data[ticker]["Close"].dropna()
                closes = _trim_today(closes, market_open, today_et)

                if len(closes) < trading_days + 1:
                    continue

                perf = (closes.iloc[-1] - closes.iloc[-trading_days]) / closes.iloc[-trading_days] * 100
                cap = market_caps[ticker]

                if cap >= 10e9:
                    threshold = perf_large_cap
                elif cap >= 2e9:
                    threshold = perf_mid_cap
                else:
                    threshold = perf_small_cap

                if perf >= threshold:
                    passed.add(ticker)
                    week_hits += 1
            except (KeyError, ValueError, ZeroDivisionError):
                continue
        logger.info(f"  {weeks}-week window: {week_hits} new hits")

    perf_passed = list(passed)
    logger.info(f"  {len(perf_passed)} after performance filter (2/3/4 week combined)")

    # 2. Dollar volume filter (uses same data)
    if min_dollar_volume > 0 and perf_passed:
        dv_passed = _filter_dollar_volume_from_data(
            perf_passed, data, min_dollar_volume, market_open, today_et
        )
        logger.info(f"  {len(dv_passed)} after dollar volume filter (20-day avg)")
    else:
        dv_passed = perf_passed

    # 3. Consecutive up days filter (uses same data)
    if min_consecutive_up_days > 0 and dv_passed:
        final = _filter_consecutive_up_days_from_data(
            dv_passed, data, min_consecutive_up_days, market_open, today_et
        )
        logger.info(f"  {len(final)} after consecutive up days filter (>= {min_consecutive_up_days})")
    else:
        final = dv_passed

    return total, final
```

Key changes vs old version:
- New params: `min_dollar_volume`, `min_consecutive_up_days`
- Uses `_yf_download_with_retry` instead of bare `yf.download`
- Uses `_trim_today` helper for consistency
- Inlines dollar volume and up days filters via the new `_from_data` helpers
- All logging that used to live in `main()` now lives here

- [ ] **Step 2: Verify no syntax / import errors**

Run: `uv run python -c "import main; import inspect; sig = inspect.signature(main.filter_shorts); print(list(sig.parameters))"`
Expected: `['filters', 'signal', 'perf_large_cap', 'perf_mid_cap', 'perf_small_cap', 'min_dollar_volume', 'min_consecutive_up_days']`

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: consolidate shorts yfinance downloads into one"
```

---

### Task 4: Update `main()` Shorts block to use new signature

**Files:**
- Modify: `main.py:566-597` (the `# --- Shorts ---` block in `main()`)

- [ ] **Step 1: Replace the Shorts block**

Find the block starting with `# --- Shorts ---` (around main.py:565). Currently it looks like:

```python
    # --- Shorts ---
    shorts_cfg = config.get("shorts")
    if shorts_cfg:
        logger.info(f"[Shorts] Running: {shorts_cfg['name']}")
        try:
            total, shorts_tickers = filter_shorts(
                shorts_cfg["filters"],
                shorts_cfg.get("signal"),
                perf_large_cap=shorts_cfg.get("perf_large_cap", 50),
                perf_mid_cap=shorts_cfg.get("perf_mid_cap", 200),
                perf_small_cap=shorts_cfg.get("perf_small_cap", 300),
            )
            logger.info(f"  Found {total} tickers, {len(shorts_tickers)} after performance filter")

            shorts_min_dv = shorts_cfg.get("min_dollar_volume", 100_000_000)
            if shorts_min_dv > 0 and shorts_tickers:
                shorts_tickers = filter_dollar_volume_yf(shorts_tickers, shorts_min_dv)
                logger.info(f"  {len(shorts_tickers)} after dollar volume filter (20-day avg)")

            min_up_days = shorts_cfg.get("min_consecutive_up_days", 3)
            if shorts_tickers and min_up_days > 0:
                shorts_tickers = filter_consecutive_up_days(shorts_tickers, min_up_days)
                logger.info(f"  {len(shorts_tickers)} after consecutive up days filter (>= {min_up_days})")

            if shorts_tickers:
                sorted_shorts = sorted(set(shorts_tickers))
                if safe_write_watchlist(sorted_shorts, us_output_dir / "Shorts.txt", fmt):
                    logger.info(f"[Shorts] Final: {len(sorted_shorts)} tickers -> output/US/Shorts.txt")
                    safe_write_watchlist(sorted_shorts, us_output_dir / f"{today}_Shorts.txt", fmt)
            else:
                logger.warning("[Shorts] No tickers found after all filters")
        except Exception as e:
            logger.warning(f"[Shorts] Failed: {e}")
```

Replace with:

```python
    # --- Shorts ---
    shorts_cfg = config.get("shorts")
    if shorts_cfg:
        logger.info(f"[Shorts] Running: {shorts_cfg['name']}")
        try:
            total, shorts_tickers = filter_shorts(
                shorts_cfg["filters"],
                shorts_cfg.get("signal"),
                perf_large_cap=shorts_cfg.get("perf_large_cap", 50),
                perf_mid_cap=shorts_cfg.get("perf_mid_cap", 200),
                perf_small_cap=shorts_cfg.get("perf_small_cap", 300),
                min_dollar_volume=shorts_cfg.get("min_dollar_volume", 100_000_000),
                min_consecutive_up_days=shorts_cfg.get("min_consecutive_up_days", 3),
            )
            logger.info(f"  Found {total} tickers from finviz Ownership screener")

            if shorts_tickers:
                sorted_shorts = sorted(set(shorts_tickers))
                if safe_write_watchlist(sorted_shorts, us_output_dir / "Shorts.txt", fmt):
                    logger.info(f"[Shorts] Final: {len(sorted_shorts)} tickers -> output/US/Shorts.txt")
                    safe_write_watchlist(sorted_shorts, us_output_dir / f"{today}_Shorts.txt", fmt)
            else:
                logger.warning("[Shorts] No tickers found after all filters")
        except Exception as e:
            logger.warning(f"[Shorts] Failed: {e}")
```

The intermediate "X after performance filter" / "X after dollar volume filter" / "X after consecutive up days filter" log lines are now produced inside `filter_shorts`, so they're not lost.

- [ ] **Step 2: Verify imports still resolve**

Run: `uv run python -c "import main"`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: simplify main() shorts block with new filter_shorts API"
```

---

### Task 5: End-to-end verification

**Files:** none modified — this is a smoke test.

- [ ] **Step 1: Snapshot current Shorts output**

```bash
cp output/US/Shorts.txt /tmp/Shorts.before.txt 2>/dev/null || echo "no prior file"
```

- [ ] **Step 2: Run the screener**

Run: `uv run main.py 2>&1 | tee /tmp/run.log`

Expected behaviors in the log:
- `[Shorts] Running: ...` appears
- Lines `2-week window: N new hits`, `3-week window: ...`, `4-week window: ...` appear (from `filter_shorts`)
- `N after performance filter (2/3/4 week combined)` appears
- `N after dollar volume filter (20-day avg)` appears
- `N after consecutive up days filter (>= 3)` appears
- `[Shorts] Final: N tickers -> output/US/Shorts.txt`
- The number of `yfinance: insufficient data` warnings should be **roughly 1/3 of before** (since we now download once instead of three times)
- No tracebacks or `[Shorts] Failed:` lines

- [ ] **Step 3: Compare output to previous run**

```bash
diff /tmp/Shorts.before.txt output/US/Shorts.txt || true
```

Expected: the diff should be small. Some tickers may have moved in/out of the list because:
- Tickers that previously slipped through dv filter via the lenient fallback (because batch 2 flaked) now get dropped at perf filter if data is missing — this is intended.
- Run-to-run variance from finviz Ownership screener results.

If the diff is huge (>50% change), the `safe_write_watchlist` guard should have blocked the write — check that the file wasn't overwritten with a degraded result.

- [ ] **Step 4: Verify Longs and RS still use old helpers**

Run: `uv run python -c "import main; print('filter_dollar_volume_yf:', main.filter_dollar_volume_yf); print('filter_consecutive_up_days:', main.filter_consecutive_up_days)"`

Expected: both print as `<function ...>` — they're still defined and exported.

Then grep:

```bash
grep -n "filter_dollar_volume_yf\|filter_consecutive_up_days" main.py
```

Expected: `filter_dollar_volume_yf` still called from Longs (around line 535) and RS (around line 612). `filter_consecutive_up_days` is no longer called from `main()` Shorts block but the function still exists (it's now unused; that's acceptable since the goal was non-disruptive — leaving it in place avoids breakage if other code relies on it).

- [ ] **Step 5: Optional cleanup — check if `filter_consecutive_up_days` is still referenced**

```bash
grep -n "filter_consecutive_up_days" main.py
```

If the only references are the `def` line and possibly its own internals (no callers), you can decide whether to remove it. **Recommendation: leave it.** It's small, named clearly, and removing it is out of scope for this refactor.

- [ ] **Step 6: Commit (if any cleanup or note added)**

If no cleanup, skip. Otherwise:

```bash
git add main.py
git commit -m "chore: remove unused filter_consecutive_up_days"
```

---

## Self-Review Checklist

Before declaring done, verify:

- [ ] All five tasks complete, each in its own commit
- [ ] `uv run main.py` runs without exceptions
- [ ] Shorts pipeline log shows exactly **one** download (no three separate "yf.download"-style request bursts visible in the log)
- [ ] `insufficient data` warnings reduced significantly vs. previous runs
- [ ] `output/US/Shorts.txt` produced and not blocked by `safe_write_watchlist` guard
- [ ] `output/US/Longs.txt` and (if applicable) `output/US/RS.txt` still produced normally — proves we didn't break Longs/RS by accident
