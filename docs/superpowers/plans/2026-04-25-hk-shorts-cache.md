# HK Shorts Local Price Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace HK Shorts' daily 5-minute yfinance batch download with an SQLite-backed local cache that fetches only newly-released bars incrementally.

**Architecture:** New `cache.py` module owns SQLite schema + CRUD + a `refresh_cache()` orchestrator with batch/per-ticker retry and stale-data degradation. `filter_hk_shorts` in `main.py` is rewritten to call `refresh_cache` then read OHLCV from the cache instead of yfinance directly.

**Tech Stack:** Python 3, SQLite (stdlib `sqlite3`), pandas (already pulled in by yfinance), yfinance.

**Spec:** `docs/superpowers/specs/2026-04-25-hk-shorts-cache-design.md`

---

## File Structure

- **Create** `cache.py` — new module, all SQLite logic + cache refresh orchestration
- **Modify** `main.py`:
  - Refactor `filter_hk_shorts` to call `refresh_cache` and read bars from cache
  - Update `main()` to open the cache connection and pass it through
- **Modify** `.gitignore` — add `cache/`
- **Create** `cache/` directory (auto-created at runtime; no commit)

---

### Task 1: Add cache directory to gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Check current .gitignore**

Run: `cat .gitignore`

- [ ] **Step 2: Append cache directory**

Add the line `cache/` to the end of `.gitignore` (preserve existing content).

If `.gitignore` doesn't exist, create it with content:
```
__pycache__/
*.pyc
.venv/
output/
cache/
```

If it exists, just append `cache/` if not already present.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore cache directory"
```

---

### Task 2: Create cache.py with schema and bar CRUD

**Files:**
- Create: `cache.py`

- [ ] **Step 1: Write cache.py with init + bar read/write**

```python
"""SQLite-backed local cache for HK price data."""

import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def open_cache(db_path: Path) -> sqlite3.Connection:
    """Open (and initialize if needed) the SQLite cache database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bars (
            ticker  TEXT NOT NULL,
            date    TEXT NOT NULL,
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            PRIMARY KEY (ticker, date)
        );
        CREATE INDEX IF NOT EXISTS idx_bars_ticker_date ON bars(ticker, date DESC);

        CREATE TABLE IF NOT EXISTS market_cap (
            ticker      TEXT PRIMARY KEY,
            cap         REAL,
            updated_at  TEXT
        );
    """)
    conn.commit()
    return conn


def get_last_bar_dates(conn: sqlite3.Connection, tickers: list[str]) -> dict[str, date | None]:
    """For each ticker, return its latest cached bar date, or None if no cached data."""
    result: dict[str, date | None] = {t: None for t in tickers}
    if not tickers:
        return result
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, MAX(date) FROM bars WHERE ticker IN ({placeholders}) GROUP BY ticker",
        tickers,
    ).fetchall()
    for ticker, max_date in rows:
        if max_date:
            result[ticker] = date.fromisoformat(max_date)
    return result


def upsert_bars(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> int:
    """Insert or replace bars for a ticker from a yfinance single-ticker DataFrame.
    DataFrame must have a DatetimeIndex and columns Open/High/Low/Close/Volume.
    Returns number of rows written."""
    if df is None or df.empty:
        return 0
    rows = []
    for ts, row in df.iterrows():
        if pd.isna(row.get("Close")):
            continue
        rows.append((
            ticker,
            ts.strftime("%Y-%m-%d"),
            float(row["Open"]) if pd.notna(row.get("Open")) else None,
            float(row["High"]) if pd.notna(row.get("High")) else None,
            float(row["Low"]) if pd.notna(row.get("Low")) else None,
            float(row["Close"]),
            int(row["Volume"]) if pd.notna(row.get("Volume")) else None,
        ))
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO bars(ticker, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def read_ticker_bars(conn: sqlite3.Connection, ticker: str) -> tuple[pd.Series, pd.Series] | None:
    """Read all cached bars for a ticker. Returns (closes, volumes) as pandas Series indexed by date,
    or None if no cached data."""
    rows = conn.execute(
        "SELECT date, close, volume FROM bars WHERE ticker = ? ORDER BY date ASC",
        (ticker,),
    ).fetchall()
    if not rows:
        return None
    dates = pd.to_datetime([r[0] for r in rows])
    closes = pd.Series([r[1] for r in rows], index=dates)
    volumes = pd.Series([r[2] for r in rows], index=dates)
    return closes, volumes
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "import cache; print(cache.open_cache.__doc__)"`
Expected: prints the docstring of `open_cache`, no errors.

- [ ] **Step 3: Smoke test the schema and CRUD**

Run:
```bash
uv run python -c "
from pathlib import Path
import pandas as pd
from datetime import date
import cache

db = Path('/tmp/test_cache.db')
db.unlink(missing_ok=True)
conn = cache.open_cache(db)

# Insert
df = pd.DataFrame({
    'Open': [100.0, 101.0],
    'High': [102.0, 103.0],
    'Low':  [99.0, 100.0],
    'Close': [101.0, 102.0],
    'Volume': [1000, 1100],
}, index=pd.to_datetime(['2026-04-23', '2026-04-24']))
n = cache.upsert_bars(conn, '0700.HK', df)
assert n == 2, f'expected 2 rows, got {n}'

# Last date
last = cache.get_last_bar_dates(conn, ['0700.HK', '0001.HK'])
assert last['0700.HK'] == date(2026, 4, 24), f'got {last}'
assert last['0001.HK'] is None

# Read
result = cache.read_ticker_bars(conn, '0700.HK')
assert result is not None
closes, volumes = result
assert len(closes) == 2
assert closes.iloc[-1] == 102.0
assert volumes.iloc[-1] == 1100

print('OK')
db.unlink()
"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add cache.py
git commit -m "feat: add cache.py with SQLite schema and bar CRUD"
```

---

### Task 3: Add market cap CRUD and prune to cache.py

**Files:**
- Modify: `cache.py` — append new functions

- [ ] **Step 1: Append market_cap and prune functions**

Append at the end of `cache.py`:

```python
def get_market_cap_cached(conn: sqlite3.Connection, ticker: str, max_age_days: int = 7) -> float | None:
    """Return cached market cap if fresher than max_age_days, else None."""
    row = conn.execute(
        "SELECT cap, updated_at FROM market_cap WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    if not row:
        return None
    cap, updated_at = row
    if cap is None or not updated_at:
        return None
    age = (date.today() - date.fromisoformat(updated_at)).days
    if age > max_age_days:
        return None
    return float(cap)


def upsert_market_cap(conn: sqlite3.Connection, ticker: str, cap: float) -> None:
    """Insert or replace cached market cap for a ticker, stamped with today's date."""
    conn.execute(
        "INSERT OR REPLACE INTO market_cap(ticker, cap, updated_at) VALUES (?, ?, ?)",
        (ticker, cap, date.today().isoformat()),
    )
    conn.commit()


def prune_old_bars(conn: sqlite3.Connection, days: int = 90) -> int:
    """Delete bars older than `days` calendar days. Returns number of rows deleted."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM bars WHERE date < ?", (cutoff,))
    conn.commit()
    return cur.rowcount
```

- [ ] **Step 2: Smoke test**

Run:
```bash
uv run python -c "
from pathlib import Path
from datetime import date, timedelta
import cache

db = Path('/tmp/test_cache.db')
db.unlink(missing_ok=True)
conn = cache.open_cache(db)

# market cap
cache.upsert_market_cap(conn, '0700.HK', 4.5e12)
assert cache.get_market_cap_cached(conn, '0700.HK') == 4.5e12
assert cache.get_market_cap_cached(conn, '9999.HK') is None

# stale market cap
old_date = (date.today() - timedelta(days=10)).isoformat()
conn.execute('UPDATE market_cap SET updated_at = ? WHERE ticker = ?', (old_date, '0700.HK'))
conn.commit()
assert cache.get_market_cap_cached(conn, '0700.HK', max_age_days=7) is None
assert cache.get_market_cap_cached(conn, '0700.HK', max_age_days=30) == 4.5e12

# prune
old_bar_date = (date.today() - timedelta(days=200)).isoformat()
conn.execute('INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)', ('XX.HK', old_bar_date, 1.0, 1.0, 1.0, 1.0, 100))
recent_bar_date = date.today().isoformat()
conn.execute('INSERT OR REPLACE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?)', ('XX.HK', recent_bar_date, 2.0, 2.0, 2.0, 2.0, 200))
conn.commit()
deleted = cache.prune_old_bars(conn, days=90)
assert deleted == 1, f'expected 1, got {deleted}'

print('OK')
db.unlink()
"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cache.py
git commit -m "feat: add market_cap and prune functions to cache.py"
```

---

### Task 4: Add refresh_cache orchestrator

**Files:**
- Modify: `cache.py` — append the orchestrator + helpers

- [ ] **Step 1: Append `refresh_cache` and supporting code**

Append at the end of `cache.py`:

```python
def _classify_tickers(
    last_dates: dict[str, date | None],
    expected_latest: date,
) -> tuple[list[str], dict[int, list[str]], list[str]]:
    """Split tickers into (bootstrap, stale_by_missing_days, fresh).
    bootstrap: never seen
    stale_by_missing_days: dict[missing_days_count -> tickers]
    fresh: already up to expected_latest
    """
    bootstrap: list[str] = []
    stale: dict[int, list[str]] = {}
    fresh: list[str] = []
    for ticker, last in last_dates.items():
        if last is None:
            bootstrap.append(ticker)
        elif last >= expected_latest:
            fresh.append(ticker)
        else:
            missing = (expected_latest - last).days
            stale.setdefault(missing, []).append(ticker)
    return bootstrap, stale, fresh


def _missing_to_period(missing_days: int) -> str:
    """Convert calendar days missing to a yfinance period string with safety margin."""
    if missing_days <= 5:
        return "10d"
    if missing_days <= 15:
        return "1mo"
    if missing_days <= 45:
        return "2mo"
    return "3mo"


def _split_multi_ticker_data(data, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Split a yfinance multi-ticker DataFrame (group_by='ticker') into per-ticker DataFrames.
    Returns dict ticker -> DataFrame; missing tickers absent or empty."""
    out: dict[str, pd.DataFrame] = {}
    if data is None or data.empty:
        return out
    if len(tickers) == 1:
        df = data.dropna(subset=["Close"]) if "Close" in data.columns else pd.DataFrame()
        if not df.empty:
            out[tickers[0]] = df
        return out
    for t in tickers:
        try:
            sub = data[t].dropna(subset=["Close"])
            if not sub.empty:
                out[t] = sub
        except (KeyError, TypeError):
            continue
    return out


def refresh_cache(
    conn: sqlite3.Connection,
    tickers: list[str],
    expected_latest: date,
    yf_download_fn,
    batch_size: int = 500,
    inter_batch_sleep: float = 5.0,
) -> dict:
    """Bring cache up to date for tickers. yf_download_fn(tickers, period=...) is the injected
    yfinance download function (so callers can plug in retry wrappers).
    Returns stats dict with counts."""
    last_dates = get_last_bar_dates(conn, tickers)
    bootstrap, stale_by_days, fresh = _classify_tickers(last_dates, expected_latest)

    stats = {
        "fresh": len(fresh),
        "bootstrap_attempted": len(bootstrap),
        "stale_attempted": sum(len(v) for v in stale_by_days.values()),
        "bars_written": 0,
        "single_retry_recovered": 0,
        "still_failing": 0,
    }
    logger.info(
        f"  cache: fresh={stats['fresh']}, bootstrap={stats['bootstrap_attempted']}, "
        f"stale={stats['stale_attempted']}"
    )

    # Build work groups: (period, tickers)
    groups: list[tuple[str, list[str]]] = []
    if bootstrap:
        groups.append(("3mo", bootstrap))
    for missing_days, group_tickers in stale_by_days.items():
        groups.append((_missing_to_period(missing_days), group_tickers))

    import time as _time

    for period, group_tickers in groups:
        for batch_start in range(0, len(group_tickers), batch_size):
            batch = group_tickers[batch_start : batch_start + batch_size]
            data = yf_download_fn(batch, period=period)
            per_ticker = _split_multi_ticker_data(data, batch)

            recovered_in_batch = 0
            for ticker in batch:
                df = per_ticker.get(ticker)
                if df is not None and len(df) > 0:
                    stats["bars_written"] += upsert_bars(conn, ticker, df)
                else:
                    # Single-ticker retry
                    retry_data = yf_download_fn([ticker], period=period)
                    retry_per = _split_multi_ticker_data(retry_data, [ticker])
                    retry_df = retry_per.get(ticker)
                    if retry_df is not None and len(retry_df) > 0:
                        stats["bars_written"] += upsert_bars(conn, ticker, retry_df)
                        stats["single_retry_recovered"] += 1
                        recovered_in_batch += 1
                    else:
                        stats["still_failing"] += 1
                        if last_dates.get(ticker) is None:
                            logger.warning(f"  cache: {ticker} has no data and yfinance returned nothing")

            if recovered_in_batch:
                logger.info(f"  cache: recovered {recovered_in_batch} tickers via single retry in this batch")

            if batch_start + batch_size < len(group_tickers):
                _time.sleep(inter_batch_sleep)

    return stats
```

- [ ] **Step 2: Smoke test the classifier**

Run:
```bash
uv run python -c "
from datetime import date
import cache

last = {
    'A.HK': None,
    'B.HK': date(2026, 4, 24),
    'C.HK': date(2026, 4, 25),
    'D.HK': date(2026, 4, 10),
}
expected = date(2026, 4, 25)
boot, stale, fresh = cache._classify_tickers(last, expected)
assert boot == ['A.HK'], boot
assert fresh == ['C.HK'], fresh
# B is 1 day stale, D is 15 days stale
assert stale == {1: ['B.HK'], 15: ['D.HK']}, stale

# period mapping
assert cache._missing_to_period(1) == '10d'
assert cache._missing_to_period(15) == '1mo'
assert cache._missing_to_period(40) == '2mo'
assert cache._missing_to_period(90) == '3mo'

print('OK')
"
```
Expected: `OK`

- [ ] **Step 3: Smoke test refresh_cache with a fake downloader**

Run:
```bash
uv run python -c "
from pathlib import Path
from datetime import date
import pandas as pd
import cache

db = Path('/tmp/test_cache.db')
db.unlink(missing_ok=True)
conn = cache.open_cache(db)

# Fake yfinance: returns multi-index DataFrame for batch, flat for single
def fake_download(tickers, period='2mo'):
    idx = pd.to_datetime(['2026-04-23', '2026-04-24', '2026-04-25'])
    if len(tickers) == 1:
        return pd.DataFrame({
            'Open': [100, 101, 102], 'High': [101, 102, 103],
            'Low': [99, 100, 101], 'Close': [100, 101, 102],
            'Volume': [1000, 1100, 1200],
        }, index=idx)
    cols = pd.MultiIndex.from_product([tickers, ['Open', 'High', 'Low', 'Close', 'Volume']])
    rows = []
    for _ in idx:
        rows.append([100, 101, 99, 100, 1000] * len(tickers))
    return pd.DataFrame(rows, index=idx, columns=cols)

stats = cache.refresh_cache(
    conn, ['A.HK', 'B.HK'], date(2026, 4, 25),
    yf_download_fn=fake_download,
    batch_size=500, inter_batch_sleep=0,
)
assert stats['bootstrap_attempted'] == 2, stats
assert stats['bars_written'] >= 6, stats  # 3 bars * 2 tickers, idempotent on retry

result = cache.read_ticker_bars(conn, 'A.HK')
assert result is not None
closes, _ = result
assert len(closes) == 3

print('OK')
db.unlink()
"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add cache.py
git commit -m "feat: add refresh_cache orchestrator with retry and degradation"
```

---

### Task 5: Refactor filter_hk_shorts to use the cache

**Files:**
- Modify: `main.py` — refactor `filter_hk_shorts` (currently lines 83-235)
- Modify: `main.py` — update `main()` to open the cache connection

- [ ] **Step 1: Add cache import and helper at top of main.py**

Find the import section near the top of `main.py` (after the existing imports). Add:

```python
import cache
```

after the existing `from finviz.screener import Screener` line, in the same import block.

Also add this helper function near the top of `main.py` (right after the `_yf_download_with_retry` function, around line 53):

```python
def _make_yf_downloader():
    """Return a download function with the project's standard kwargs preset,
    suitable for passing to cache.refresh_cache."""
    def download(tickers, period: str):
        return _yf_download_with_retry(
            tickers, period=period, progress=False, group_by="ticker", threads=True
        )
    return download
```

- [ ] **Step 2: Replace `filter_hk_shorts`**

Replace the entire current `filter_hk_shorts` function (lines 83-235) with:

```python
def filter_hk_shorts(config: dict, conn) -> tuple[int, list[str]]:
    """Run HK shorts pipeline using local SQLite cache for OHLCV.
    Returns (universe_size, filtered_tickers_in_tv_format)."""
    logger.info("[HK Shorts] Fetching HKEX equity universe...")
    codes = fetch_hkex_equities()
    logger.info(f"  Found {len(codes)} Main Board equities")

    yf_tickers = [code + ".HK" for code in codes]

    now_hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    market_open = 9 <= now_hk.hour < 16 and now_hk.weekday() < 5
    today = now_hk.date()
    if market_open:
        logger.info("  HK market still open, excluding today's incomplete data")

    # Expected latest bar date: previous weekday if market still open or weekend, else today.
    expected_latest = today
    if market_open:
        expected_latest = today - timedelta(days=1)
    while expected_latest.weekday() >= 5:
        expected_latest = expected_latest - timedelta(days=1)

    logger.info(f"[HK Shorts] Refreshing cache (expected latest bar: {expected_latest})...")
    stats = cache.refresh_cache(
        conn, yf_tickers, expected_latest,
        yf_download_fn=_make_yf_downloader(),
    )
    logger.info(
        f"  cache stats: {stats['bars_written']} bars written, "
        f"{stats['single_retry_recovered']} recovered via retry, "
        f"{stats['still_failing']} still failing"
    )

    # Read all ticker series from cache (one pass)
    ticker_closes: dict[str, object] = {}
    ticker_volumes: dict[str, object] = {}
    for ticker in yf_tickers:
        result = cache.read_ticker_bars(conn, ticker)
        if result is None:
            continue
        closes, volumes = result
        closes = _trim_today(closes, market_open, today)
        volumes = _trim_today(volumes, market_open, today)
        if len(closes) >= 20 and len(volumes) >= 20:
            ticker_closes[ticker] = closes
            ticker_volumes[ticker] = volumes

    # Phase 1: SMA20 +20% and avg volume
    min_avg_volume = config.get("min_avg_volume", 1_000_000)
    phase1 = []
    for ticker in yf_tickers:
        if ticker not in ticker_closes:
            continue
        closes = ticker_closes[ticker]
        volumes = ticker_volumes[ticker]
        sma20 = closes.iloc[-20:].mean()
        if closes.iloc[-1] > sma20 * 1.2 and volumes.iloc[-20:].mean() >= min_avg_volume:
            phase1.append(ticker)

    logger.info(f"  {len(phase1)} after SMA20 +20% and volume filter")
    if not phase1:
        return len(codes), []

    # Phase 2: market cap (use cache if fresh, else fetch and store)
    min_market_cap = config.get("min_market_cap", 2_000_000_000)
    phase2 = []
    market_caps: dict[str, float] = {}
    for ticker in phase1:
        cap = cache.get_market_cap_cached(conn, ticker)
        if cap is None:
            cap = _get_market_cap(ticker)
            if cap is not None:
                cache.upsert_market_cap(conn, ticker, cap)
            time.sleep(0.5)
        if cap is not None and cap >= min_market_cap:
            phase2.append(ticker)
            market_caps[ticker] = cap

    logger.info(f"  {len(phase2)} after market cap filter (>= {min_market_cap:,.0f} HKD)")
    if not phase2:
        return len(codes), []

    # Phase 3: dollar volume
    min_dv = config.get("min_dollar_volume", 100_000_000)
    phase3 = []
    for ticker in phase2:
        try:
            closes = ticker_closes[ticker]
            volumes = ticker_volumes[ticker]
            if closes.iloc[-1] * volumes.iloc[-20:].mean() >= min_dv:
                phase3.append(ticker)
        except (KeyError, TypeError):
            continue

    logger.info(f"  {len(phase3)} after dollar volume filter (>= {min_dv:,.0f} HKD)")
    if not phase3:
        return len(codes), []

    # Phase 4: cap-conditional performance
    large_cap_thr = config.get("large_cap_threshold", 80_000_000_000)
    mid_cap_thr = config.get("mid_cap_threshold", 16_000_000_000)
    perf_large = config.get("perf_large_cap", 50)
    perf_mid = config.get("perf_mid_cap", 200)
    perf_small = config.get("perf_small_cap", 300)
    perf_weeks = [2, 3, 4]

    phase4: set[str] = set()
    for weeks in perf_weeks:
        trading_days = weeks * 5 + (2 if weeks == 4 else 0)
        week_hits = 0
        for ticker in phase3:
            if ticker in phase4:
                continue
            try:
                closes = ticker_closes[ticker]
                if len(closes) < trading_days + 1:
                    continue
                perf = (closes.iloc[-1] - closes.iloc[-trading_days]) / closes.iloc[-trading_days] * 100
                cap = market_caps[ticker]
                if cap >= large_cap_thr:
                    threshold = perf_large
                elif cap >= mid_cap_thr:
                    threshold = perf_mid
                else:
                    threshold = perf_small
                if perf >= threshold:
                    phase4.add(ticker)
                    week_hits += 1
            except (KeyError, TypeError, ZeroDivisionError):
                continue
        logger.info(f"  {weeks}-week window: {week_hits} new hits")

    logger.info(f"  {len(phase4)} after performance filter (2/3/4 week combined)")
    if not phase4:
        return len(codes), []

    # Phase 5: consecutive up days
    min_up_days = config.get("min_consecutive_up_days", 3)
    phase5 = []
    for ticker in phase4:
        try:
            closes = ticker_closes[ticker]
            if len(closes) < 2:
                continue
            consecutive = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes.iloc[i] > closes.iloc[i - 1]:
                    consecutive += 1
                else:
                    break
            if consecutive >= min_up_days:
                phase5.append(ticker)
        except (KeyError, TypeError):
            continue

    logger.info(f"  {len(phase5)} after consecutive up days filter (>= {min_up_days})")

    # Prune old bars at end
    deleted = cache.prune_old_bars(conn, days=90)
    if deleted:
        logger.info(f"  cache: pruned {deleted} bars older than 90 days")

    # Convert to TradingView format: 0700.HK → HKEX:0700
    tv_tickers = ["HKEX:" + t.replace(".HK", "") for t in phase5]
    return len(codes), tv_tickers
```

- [ ] **Step 3: Add `timedelta` to imports**

If `timedelta` is not already imported, add it. Find the line `from datetime import date, datetime` and change it to:

```python
from datetime import date, datetime, timedelta
```

(Verify with `grep -n "from datetime" main.py` first; if `timedelta` is already there, skip this step.)

- [ ] **Step 4: Update `main()` to open cache connection and pass to `filter_hk_shorts`**

Find the HK Shorts block in `main()` (search for `# --- HK Shorts ---`). The current call is something like `total_hk, hk_tickers = filter_hk_shorts(hk_cfg)`. Update the surrounding code so that:

1. Right after `min_dollar_volume = settings.get("min_dollar_volume", 0)` (the existing line near the top of `main()`), add:

```python
    cache_db_path = project_root / "cache" / "hk_prices.db"
    hk_cache_conn = cache.open_cache(cache_db_path)
```

2. Change the call site from `filter_hk_shorts(hk_cfg)` to `filter_hk_shorts(hk_cfg, hk_cache_conn)`.

3. At the very end of `main()`, before `return 0` (or wherever the function ends), add:

```python
    hk_cache_conn.close()
```

Use `grep -n "filter_hk_shorts\|return 0" main.py` to find the exact lines.

- [ ] **Step 5: Verify imports and signature**

Run: `uv run python -c "import main; import inspect; print(list(inspect.signature(main.filter_hk_shorts).parameters))"`
Expected: `['config', 'conn']`

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "refactor: HK Shorts uses local SQLite cache for OHLCV"
```

---

### Task 6: End-to-end verification

**Files:** none modified.

- [ ] **Step 1: Snapshot current HK Shorts output**

```bash
cp output/HK/Shorts.txt /tmp/hk_shorts.before.txt 2>/dev/null || echo "no prior file"
```

- [ ] **Step 2: First run (cold cache, will bootstrap all 2,400 tickers)**

```bash
rm -f cache/hk_prices.db
time uv run main.py 2>&1 | tee /tmp/run_cold.log | tail -60
```

Expected:
- Log line: `[HK Shorts] Refreshing cache (expected latest bar: ...)`
- Log line: `cache: fresh=0, bootstrap=2400+, stale=0`
- Log line: `cache stats: <N> bars written, ...`
- All five filter phases run as before
- `output/HK/Shorts.txt` produced
- Wall time: roughly comparable to current (5-7 min)

- [ ] **Step 3: Inspect cache contents**

```bash
sqlite3 cache/hk_prices.db "SELECT COUNT(*) FROM bars; SELECT COUNT(DISTINCT ticker) FROM bars; SELECT MIN(date), MAX(date) FROM bars; SELECT COUNT(*) FROM market_cap;"
```

Expected:
- bars: 50,000-100,000+ rows (2,400 tickers × ~40 trading days, minus failures)
- distinct ticker count: close to 2,400 (some may be missing)
- date range spans ~3 months
- market_cap rows: roughly equal to phase1 size (whatever passed the SMA20 filter)

- [ ] **Step 4: Second run (warm cache, should be much faster)**

```bash
time uv run main.py 2>&1 | tee /tmp/run_warm.log | tail -60
```

Expected:
- Log line: `cache: fresh=2400+, bootstrap=0, stale=<small or 0>` (fresh because we just populated)
- HK Shorts phase completes in seconds, not minutes
- `output/HK/Shorts.txt` produced

- [ ] **Step 5: Compare HK Shorts output**

```bash
diff /tmp/hk_shorts.before.txt output/HK/Shorts.txt || true
```

Expected: small or no diff. Major differences should be investigated:
- Missing tickers in new output → check `still_failing` count and warnings
- Extra tickers → could be that previously-flaky tickers are now properly evaluated

- [ ] **Step 6: Verify US flows still untouched**

Run: `grep -n "filter_dollar_volume_yf\|filter_consecutive_up_days" main.py`
Expected: callers in US Longs and US Shorts paths unchanged.

Also check Longs.txt and Shorts.txt were produced normally:
```bash
ls -la output/US/Longs.txt output/US/Shorts.txt
```

- [ ] **Step 7: Optional — simulate yfinance failure to verify degradation**

This step is optional but proves the resilience claim. With cache populated, temporarily break network or use a mock. For a quick check, just observe that warm-cache runs finish even if yfinance has flaky moments by re-running a couple of times and checking the `still_failing` count is small.

- [ ] **Step 8: Inspect log for any unexpected errors or warnings**

```bash
grep -E "WARNING|ERROR|Traceback" /tmp/run_warm.log
```

Expected: only known/expected warnings. No tracebacks. No new error patterns.

---

## Self-Review Checklist

After all tasks complete, verify:

- [ ] First-run wall time ≈ current behavior; second-run wall time ≪ first
- [ ] `cache/hk_prices.db` exists and is gitignored
- [ ] `output/HK/Shorts.txt` produced on both runs, content reasonable
- [ ] US Longs / US Shorts pipelines unchanged in behavior and code
- [ ] Cache refresh logs show fresh→bootstrap→stale classification working
- [ ] No tracebacks in either log
