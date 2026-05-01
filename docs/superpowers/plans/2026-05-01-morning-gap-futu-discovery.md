# Morning-Gap Futu-Snapshot Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken Finviz `ta_topgainers` candidate-discovery path in `run_morning_gap()` with a Futu snapshot-based bulk scan, so today's pre-market / post-open gappers (e.g. TWLO 2026-05-01 +19.5%) actually surface.

**Architecture:** New helper `discover_morning_gap_candidates()` in `futu_sync.py` calls `get_stock_basicinfo` once for the US universe, filters to NASDAQ/NYSE/AMEX, batch-snapshots in chunks of 400, and returns tickers passing cap/price/gap thresholds. `run_morning_gap()` Phase 1 swaps the Finviz call for this helper; the rest of the pipeline gains explicit yfinance-side SMA50/SMA200 and 20-day-avg-volume gates (previously inherited from Finviz filters), and extends the daily download window to 1y so SMA200 has enough samples.

**Tech Stack:** Python 3.13, futu-api SDK, yfinance, TOML config, no test framework — verification via `uv run python` ad-hoc invocations and end-to-end runs of `main.py --mode morning-gap`.

**Spec:** `docs/superpowers/specs/2026-05-01-morning-gap-futu-discovery-design.md`

---

## File Structure

| File | Role |
|---|---|
| `futu_sync.py` | New `discover_morning_gap_candidates()` helper. Owns universe selection + bulk snapshot logic. |
| `main.py` | Replace `run_morning_gap()` Phase 1; extend daily download window to 1y; add `_filter_sma_trend()` and `_filter_avg_volume()` helpers; remove now-redundant pre-market gap re-validation. |
| `config.toml` | Replace `[morning_gap]` filter keys with new threshold-based keys. |

No new test files (project has none). Verification is inline `uv run python -c '...'` snippets and end-to-end runs.

---

## Task 0: Pre-flight schema sanity check

**Goal:** Confirm the Futu snapshot DataFrame has the field names this plan assumes (`change_rate`, `pre_change_rate`, `pre_volume`, `total_market_val`, `last_price`). If a name differs we adjust here, before writing code.

**Files:** none (verification only)

- [ ] **Step 1: Probe Futu snapshot schema with a known liquid ticker**

```bash
uv run python -c "
from futu import OpenQuoteContext, RET_OK
ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ret, data = ctx.get_market_snapshot(['US.AAPL', 'US.TWLO'])
print('ret:', ret)
print('cols:', list(data.columns))
needed = ['code', 'last_price', 'total_market_val', 'pre_change_rate', 'pre_volume', 'change_rate']
missing = [c for c in needed if c not in data.columns]
print('missing required fields:', missing)
ctx.close()
"
```

Expected: `ret: 0`, `missing required fields: []`.

If any field is missing, stop and reconcile the plan. The snapshot field reference: https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html

- [ ] **Step 2: Probe `get_stock_basicinfo` exchange_type values**

```bash
uv run python -c "
from futu import OpenQuoteContext, Market, SecurityType
ctx = OpenQuoteContext(host='127.0.0.1', port=11111)
ret, data = ctx.get_stock_basicinfo(market=Market.US, stock_type=SecurityType.STOCK)
print('total:', len(data))
print('exchange_type counts:')
print(data['exchange_type'].value_counts())
filt = data[data['exchange_type'].isin(['US_NASDAQ','US_NYSE','US_AMEX'])
            & (data['suspension'] == False) & (data['delisting'] == False)]
print('after filter:', len(filt))
ctx.close()
"
```

Expected: ~12k total; ~5–7k after the NASDAQ/NYSE/AMEX + suspension/delisting filter.

- [ ] **Step 3: No commit (read-only probes).**

---

## Task 1: Add `discover_morning_gap_candidates()` to `futu_sync.py`

**Files:**
- Modify: `futu_sync.py` (append a new function, no existing function changes)

- [ ] **Step 1: Append the helper to `futu_sync.py`**

Add at the end of `futu_sync.py`:

```python
def discover_morning_gap_candidates(
    min_gap_pct: float,
    min_market_cap: float,
    min_price: float,
    pre_market: bool,
    exchanges: list[str],
    host: str = "127.0.0.1",
    port: int = 11111,
) -> list[str] | None:
    """Discover US morning-gap candidates via Futu snapshot.

    Pipeline:
      1. ``get_stock_basicinfo(market=US, stock_type=STOCK)``.
      2. Filter rows to ``exchange_type in exchanges`` and not suspended/delisted.
      3. ``get_market_snapshot`` in batches of 400.
      4. Keep tickers with ``total_market_val >= min_market_cap``,
         ``last_price >= min_price``, and gap above ``min_gap_pct`` —
         pre-market uses ``pre_change_rate`` (and ``pre_volume > 0``);
         post-open uses ``change_rate``.

    Returns plain US tickers (e.g. ``"TWLO"``, not ``"US.TWLO"``).
    Returns ``None`` on any failure so callers can decide whether to fall
    back. Logs a single warning per failure mode.
    """
    try:
        from futu import OpenQuoteContext, RET_OK, Market, SecurityType
    except ImportError:
        logger.warning("  Futu discovery: futu-api not installed")
        return None

    if not _opend_reachable(host, port):
        logger.warning(f"  Futu discovery: OpenD not reachable at {host}:{port}")
        return None

    exchanges_set = set(exchanges)
    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        ret, basic = ctx.get_stock_basicinfo(
            market=Market.US, stock_type=SecurityType.STOCK
        )
        if ret != RET_OK:
            logger.warning(f"  Futu discovery: get_stock_basicinfo failed — {basic}")
            return None
        if basic is None or len(basic) == 0:
            logger.warning("  Futu discovery: empty basicinfo result")
            return None

        mask = (
            basic["exchange_type"].isin(exchanges_set)
            & (basic["suspension"] == False)  # noqa: E712
            & (basic["delisting"] == False)   # noqa: E712
        )
        codes = basic.loc[mask, "code"].tolist()
        logger.info(
            f"  Futu discovery: basicinfo={len(basic)} "
            f"after exchange/suspension/delisting filter={len(codes)}"
        )
        if not codes:
            return []

        survivors: list[str] = []
        BATCH = 400
        for i in range(0, len(codes), BATCH):
            batch = codes[i:i + BATCH]
            ret, snap = ctx.get_market_snapshot(batch)
            if ret != RET_OK:
                logger.warning(
                    f"  Futu discovery: snapshot batch {i}-{i + len(batch)} "
                    f"failed — {snap}"
                )
                return None
            for _, row in snap.iterrows():
                code = row.get("code")
                if not code or not code.startswith("US."):
                    continue
                try:
                    cap = float(row.get("total_market_val", 0) or 0)
                    price = float(row.get("last_price", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if cap < min_market_cap or price < min_price:
                    continue
                if pre_market:
                    try:
                        pre_vol = float(row.get("pre_volume", 0) or 0)
                        gap = float(row.get("pre_change_rate"))
                    except (TypeError, ValueError):
                        continue
                    if pre_vol <= 0 or gap < min_gap_pct:
                        continue
                else:
                    try:
                        gap = float(row.get("change_rate"))
                    except (TypeError, ValueError):
                        continue
                    if gap < min_gap_pct:
                        continue
                survivors.append(code[len("US."):])

        logger.info(
            f"  Futu discovery: {len(survivors)} candidates "
            f"({'pre-market' if pre_market else 'post-open'}, "
            f"gap>={min_gap_pct}%, cap>=${min_market_cap:,.0f}, "
            f"price>=${min_price:.2f})"
        )
        return survivors
    except Exception as e:
        logger.warning(f"  Futu discovery: unexpected error — {e}")
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
```

- [ ] **Step 2: Smoke-test the helper**

```bash
uv run python -c "
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from futu_sync import discover_morning_gap_candidates
out = discover_morning_gap_candidates(
    min_gap_pct=5.0,
    min_market_cap=300_000_000,
    min_price=10.0,
    pre_market=True,
    exchanges=['US_NASDAQ', 'US_NYSE', 'US_AMEX'],
)
print('result type:', type(out))
print('count:', None if out is None else len(out))
if out is not None:
    print('sample:', out[:20])
    print('TWLO in result?', 'TWLO' in out)
"
```

Expected (assuming pre-market hours and OpenD reachable): non-`None` list, the log lines from the helper appear, candidate count in the 0–200 range. **If run during US pre-market on 2026-05-01 with TWLO gapping +19.5%, TWLO should be in the output.**

If run outside pre-market hours, `pre_market=True` may produce 0 candidates — that's expected (no current pre-market trades). Re-run with `pre_market=False` to verify the post-open path returns sensible counts.

- [ ] **Step 3: OpenD-down test**

```bash
uv run python -c "
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from futu_sync import discover_morning_gap_candidates
out = discover_morning_gap_candidates(
    min_gap_pct=5.0, min_market_cap=300_000_000, min_price=10.0,
    pre_market=True, exchanges=['US_NASDAQ'],
    port=11112,  # closed port
)
print('result:', out)
"
```

Expected: `result: None` and a single warning line `Futu discovery: OpenD not reachable at 127.0.0.1:11112`.

- [ ] **Step 4: Commit**

```bash
git add futu_sync.py
git commit -m "$(cat <<'EOF'
feat(futu): add discover_morning_gap_candidates helper

Bulk-snapshot scan over US NASDAQ/NYSE/AMEX universe. Returns tickers
passing market-cap, price, and gap thresholds — pre-market uses
pre_change_rate (with pre_volume>0); post-open uses change_rate.
Returns None on any failure so callers can degrade gracefully.

Refs: docs/superpowers/specs/2026-05-01-morning-gap-futu-discovery-design.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `_filter_sma_trend()` helper to `main.py`

**Files:**
- Modify: `main.py` — add helper next to the other filter helpers (before/after `_filter_adr_percent`).

- [ ] **Step 1: Add helper after `_filter_adr_percent` in `main.py` (around line 1020, end of that function)**

```python
def _filter_sma_trend(
    tickers: list[str],
    daily_data,
    today_date,
    sma_short: int = 50,
    sma_long: int = 200,
    market_open: bool = True,
    single: bool | None = None,
) -> list[str]:
    """Keep tickers whose latest completed daily close is above both
    SMA50 and SMA200. Replaces Finviz `ta_sma50_pa`/`ta_sma200_pa` for the
    Futu-discovery path. Strict: tickers with insufficient history
    (< sma_long bars after trimming today's partial bar) are dropped."""
    if not tickers:
        return []
    if single is None:
        single = len(tickers) == 1

    result = []
    for ticker in tickers:
        try:
            if single:
                closes = daily_data["Close"].dropna()
            else:
                closes = daily_data[ticker]["Close"].dropna()
            closes = _trim_today(closes, market_open, today_date)
            if len(closes) < sma_long:
                logger.warning(
                    f"  {ticker}: insufficient daily bars for SMA{sma_long} "
                    f"({len(closes)}<{sma_long}), dropping"
                )
                continue
            last = float(closes.iloc[-1])
            sma_s = float(closes.iloc[-sma_short:].mean())
            sma_l = float(closes.iloc[-sma_long:].mean())
            if last >= sma_s and last >= sma_l:
                result.append(ticker)
            else:
                logger.info(
                    f"  {ticker}: close {last:.2f} below SMA{sma_short}={sma_s:.2f} "
                    f"or SMA{sma_long}={sma_l:.2f}, dropping"
                )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"  {ticker}: SMA trend check failed ({e}), dropping")

    return result
```

- [ ] **Step 2: Smoke-test the helper against TWLO using a 1y daily download**

```bash
uv run python -c "
import yfinance as yf
from datetime import date
import sys
sys.path.insert(0, '.')
from main import _filter_sma_trend
data = yf.download(['TWLO','AAPL'], period='1y', interval='1d',
                   group_by='ticker', threads=False, progress=False)
out = _filter_sma_trend(['TWLO','AAPL'], data, date.today(), market_open=False)
print('survivors:', out)
"
```

Expected: `survivors: ['TWLO', 'AAPL']` (both above SMA50/SMA200 today). If TWLO isn't included it's a regression — investigate before continuing.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
feat(main): add _filter_sma_trend helper

Drops tickers whose latest close is below SMA50 or SMA200, computed from
the existing daily data DataFrame. Replaces the Finviz ta_sma50_pa /
ta_sma200_pa filters that we lose when switching morning_gap discovery
off Finviz.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `_filter_avg_volume()` helper to `main.py`

**Files:**
- Modify: `main.py` — add helper next to `_filter_sma_trend`.

- [ ] **Step 1: Add helper after `_filter_sma_trend`**

```python
def _filter_avg_volume(
    tickers: list[str],
    daily_data,
    min_avg_vol: float,
    days: int,
    today_date,
    market_open: bool = True,
    single: bool | None = None,
) -> list[str]:
    """Keep tickers whose N-day average daily volume is >= min_avg_vol.
    Replaces Finviz `sh_avgvol_o500` for the Futu-discovery path. Strict:
    tickers with fewer than `days` completed bars are dropped."""
    if not tickers:
        return []
    if single is None:
        single = len(tickers) == 1

    result = []
    for ticker in tickers:
        try:
            if single:
                volumes = daily_data["Volume"].dropna()
            else:
                volumes = daily_data[ticker]["Volume"].dropna()
            volumes = _trim_today(volumes, market_open, today_date)
            if len(volumes) < days:
                logger.warning(
                    f"  {ticker}: insufficient daily bars for avg vol "
                    f"({len(volumes)}<{days}), dropping"
                )
                continue
            avg = float(volumes.iloc[-days:].mean())
            if avg >= min_avg_vol:
                result.append(ticker)
            else:
                logger.info(
                    f"  {ticker}: 20d avg vol {avg:,.0f} < {min_avg_vol:,.0f}, "
                    f"dropping"
                )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"  {ticker}: avg vol check failed ({e}), dropping")

    return result
```

- [ ] **Step 2: Smoke-test with a deliberately-illiquid ticker**

```bash
uv run python -c "
import yfinance as yf
from datetime import date
import sys
sys.path.insert(0, '.')
from main import _filter_avg_volume
data = yf.download(['AAPL','TWLO'], period='2mo', interval='1d',
                   group_by='ticker', threads=False, progress=False)
out = _filter_avg_volume(['AAPL','TWLO'], data, min_avg_vol=500_000,
                         days=20, today_date=date.today(), market_open=False)
print('survivors (>=500K):', out)
out2 = _filter_avg_volume(['AAPL','TWLO'], data, min_avg_vol=100_000_000,
                          days=20, today_date=date.today(), market_open=False)
print('survivors (>=100M, expect AAPL only or none):', out2)
"
```

Expected: First call returns both. Second call returns AAPL or `[]` depending on AAPL's 20d avg.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
feat(main): add _filter_avg_volume helper

Drops tickers whose N-day average daily volume is below the threshold,
computed from the existing daily data DataFrame. Replaces the Finviz
sh_avgvol_o500 filter for the Futu-discovery path.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Rewire `run_morning_gap()` Phase 1 + insert new gates + drop pre-market revalidation

**Files:**
- Modify: `main.py:609-705` (the `run_morning_gap` function).
- Modify: `main.py:24-29` (imports — add `discover_morning_gap_candidates`).

- [ ] **Step 1: Add the import**

In `main.py` lines 24-29, replace:

```python
from futu_sync import (
    get_market_caps_futu,
    intraday_cumulative_volume_futu,
    pre_market_gap_futu,
    sync_to_futu,
)
```

with:

```python
from futu_sync import (
    discover_morning_gap_candidates,
    get_market_caps_futu,
    intraday_cumulative_volume_futu,
    pre_market_gap_futu,
    sync_to_futu,
)
```

- [ ] **Step 2: Replace Phase 1 of `run_morning_gap` (`main.py:622-641`)**

Replace lines 622-641 (from `sign = "+"` through the `if not tickers: return offset, []` for Phase 1) with:

```python
    sign = "+" if offset >= 0 else ""
    logger.info(f"[Morning Gap] Running for offset {sign}{offset}min")

    # Phase 1: Futu snapshot-based discovery. Replaces Finviz screener +
    # ta_topgainers signal — that signal does not actually surface today's
    # pre-market gappers (it ranks by recent regular-session perf), so big
    # earnings gappers like TWLO 2026-05-01 +19.5% pre-market never entered
    # the candidate set. We now scan NASDAQ/NYSE/AMEX directly via Futu's
    # bulk snapshot and filter by pre_change_rate / change_rate at the source.
    futu_host = (futu_cfg or {}).get("host", "127.0.0.1")
    futu_port = (futu_cfg or {}).get("port", 11111)
    discovery = discover_morning_gap_candidates(
        min_gap_pct=config.get("min_gap_percent", 5.0),
        min_market_cap=config.get("min_market_cap", 300_000_000),
        min_price=config.get("min_price", 10.0),
        pre_market=(offset < 0),
        exchanges=config.get(
            "exchanges", ["US_NASDAQ", "US_NYSE", "US_AMEX"]
        ),
        host=futu_host,
        port=futu_port,
    )
    if discovery is None:
        logger.warning(
            "[Morning Gap] Futu discovery failed (OpenD unreachable or API error), "
            "skipping run"
        )
        return offset, []
    tickers = discovery
    logger.info(f"  Found {len(tickers)} tickers from Futu snapshot discovery")
    if not tickers:
        return offset, []
```

- [ ] **Step 3: Extend the daily download window from `2mo` to `1y` (`main.py:644-647`)**

Replace:

```python
    daily_data = _yf_download_with_retry(
        tickers, period="2mo", interval="1d", progress=False,
        group_by="ticker", threads=False,
    )
```

with:

```python
    # 1y window is required for SMA200 (needs >=200 trading days). Used by
    # _filter_sma_trend below; older-window filters (dollar volume, ADR%,
    # avg volume) only need the trailing 20-30 bars and ignore the rest.
    daily_data = _yf_download_with_retry(
        tickers, period="1y", interval="1d", progress=False,
        group_by="ticker", threads=False,
    )
```

- [ ] **Step 4: Insert SMA + avg-volume gates after the ADR% block, before the pre-market revalidation**

After the ADR% block (after line 673 in the current file: the block ending `if not tickers: return offset, []`), and **before** the pre-market revalidation block (`if offset < 0:` at line 681), insert:

```python
    # Phase 3c: SMA50/SMA200 trend gate (replaces Finviz ta_sma50_pa / ta_sma200_pa).
    tickers = _filter_sma_trend(tickers, daily_data, today_et)
    logger.info(f"  {len(tickers)} after SMA50/SMA200 trend filter")
    if not tickers:
        return offset, []

    # Phase 3d: 20-day average volume gate (replaces Finviz sh_avgvol_o500).
    min_avg_vol = config.get("min_avg_volume", 500_000)
    avg_days = config.get("avg_volume_days", 20)
    tickers = _filter_avg_volume(
        tickers, daily_data, min_avg_vol, avg_days, today_et
    )
    logger.info(
        f"  {len(tickers)} after 20d avg volume filter (>= {min_avg_vol:,.0f})"
    )
    if not tickers:
        return offset, []
```

- [ ] **Step 5: Replace the pre-market revalidation block with a simple early return**

The existing block (`main.py:675-705`, the comment + `if offset < 0:` block that calls `pre_market_gap_futu` / `_filter_pre_market_gap` and returns):

```python
    # Pre-market: skip intraday cumulative volume filter (no meaningful
    # accumulated session volume yet) and revalidate the gap. Prefer Futu
    # OpenAPI (real-time, single snapshot call) when [futu] is enabled;
    # fall back to yfinance 1m prepost bars when OpenD is unreachable or the
    # snapshot call errors. Either gate is needed because Finviz's Gap field
    # is yesterday's gap during pre-market hours.
    if offset < 0:
        min_pm_gap = config.get("min_pre_market_gap_percent", 5.0)
        if min_pm_gap > 0 and tickers:
            futu_result = None
            if futu_cfg and futu_cfg.get("enabled"):
                futu_result = pre_market_gap_futu(
                    tickers, min_pm_gap,
                    host=futu_cfg.get("host", "127.0.0.1"),
                    port=futu_cfg.get("port", 11111),
                )
            if futu_result is not None:
                tickers = futu_result
                logger.info(
                    f"  {len(tickers)} after pre-market gap revalidation "
                    f"(Futu, >= +{min_pm_gap}%)"
                )
            else:
                tickers = _filter_pre_market_gap(
                    tickers, daily_data, min_pm_gap, today_et
                )
                logger.info(
                    f"  {len(tickers)} after pre-market gap revalidation "
                    f"(yfinance, >= +{min_pm_gap}%)"
                )
        return offset, tickers
```

is replaced with:

```python
    # Pre-market path: discovery already enforced pre_change_rate >= min_gap_pct
    # from the same Futu snapshot, so no re-validation is needed. Skip the
    # cumulative volume gate (no accumulated session volume yet pre-open).
    if offset < 0:
        return offset, tickers
```

- [ ] **Step 6: Verify the file parses and lints**

```bash
uv run python -c "import main; print('import OK')"
```

Expected: `import OK` and no syntax errors.

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "$(cat <<'EOF'
feat(morning-gap): switch discovery to Futu snapshot

Phase 1 now calls discover_morning_gap_candidates() instead of running a
Finviz screener with the broken ta_topgainers signal. Daily download
extended to 1y (required for SMA200). New SMA50/SMA200 trend gate and
20d avg volume gate replace the lost Finviz filters. Pre-market gap
re-validation removed — discovery enforces it from the same snapshot.

If OpenD is unreachable or any Futu API call errors, the run logs a
warning and returns empty. There is no Finviz fallback (the broken
ta_topgainers signal is the bug we are removing).

Refs: docs/superpowers/specs/2026-05-01-morning-gap-futu-discovery-design.md

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Update `[morning_gap]` config keys

**Files:**
- Modify: `config.toml:150-171`

- [ ] **Step 1: Replace the `[morning_gap]` block**

Replace the current `[morning_gap]` section (`config.toml:150-172`):

```toml
[morning_gap]
name = "Morning Gap Up"
filters = ["ind_stocksonly","cap_smallover","sh_avgvol_o500","sh_price_o10","ta_gap_u5","ta_sma50_pa","ta_sma200_pa"]
# Pre-market candidate discovery. Finviz's `ta_gap_u5` is computed off today's
# regular-session open vs prior close — before 9:30 ET that's stale (yesterday's
# gap winners). `ta_topgainers` reflects today's percentage change including
# pre-market activity. We drop `ta_gap_u5` here and let `_filter_pre_market_gap`
# (yfinance pre-market price vs prev close >= min_pre_market_gap_percent)
# enforce the actual gap threshold downstream. Used only when offset < 0;
# post-open scans keep `filters`/`signal` above.
pre_market_filters = ["ind_stocksonly","cap_smallover","sh_avgvol_o500","sh_price_o10","ta_sma50_pa","ta_sma200_pa"]
pre_market_signal = "ta_topgainers"
avg_volume_days = 20
min_dollar_volume = 100_000_000
scan_offsets = [-20, -10, 10, 15, 20, 25, 30]
offset_tolerance_minutes = 2
# Pre-market gap threshold (yfinance pre-market last vs prev close). With
# pre_market_signal=ta_topgainers the candidate set already reflects today's
# movement, so this filters down from "any gainer" to "true gap-ups". Set
# to 0 to disable.
min_pre_market_gap_percent = 5.0
# ADR% filter is inherited from [settings] (min_adr_percent / adr_days).
# Override here if a different threshold is desired for morning-gap only.
```

with:

```toml
[morning_gap]
name = "Morning Gap Up"
# Discovery via Futu snapshot over a curated US universe (replaces the
# Finviz `ta_topgainers` signal — empirically that surfaced last week's
# regular-session gainers, not today's pre-market gappers, so earnings
# gaps like TWLO 2026-05-01 +19.5% never entered the candidate set).
# Pre-market scans use `pre_change_rate`; post-open scans use `change_rate`.
exchanges = ["US_NASDAQ", "US_NYSE", "US_AMEX"]
min_gap_percent = 5.0           # gap threshold for both pre & post discovery
min_market_cap = 300_000_000    # USD; mirrors Finviz cap_smallover
min_price = 10.0                # USD; mirrors Finviz sh_price_o10
min_avg_volume = 500_000        # 20d shares/day; mirrors Finviz sh_avgvol_o500
avg_volume_days = 20
min_dollar_volume = 100_000_000
scan_offsets = [-20, -10, 10, 15, 20, 25, 30]
offset_tolerance_minutes = 2
# ADR% filter is inherited from [settings] (min_adr_percent / adr_days).
# Override here if a different threshold is desired for morning-gap only.
```

- [ ] **Step 2: Verify the config parses**

```bash
uv run python -c "
import tomllib
with open('config.toml', 'rb') as f:
    c = tomllib.load(f)
mg = c['morning_gap']
print('keys:', sorted(mg.keys()))
for k in ('exchanges','min_gap_percent','min_market_cap','min_price','min_avg_volume'):
    assert k in mg, f'missing {k}'
for k in ('filters','signal','pre_market_filters','pre_market_signal','min_pre_market_gap_percent'):
    assert k not in mg, f'leftover {k}'
print('config OK')
"
```

Expected: `config OK`.

- [ ] **Step 3: Commit**

```bash
git add config.toml
git commit -m "$(cat <<'EOF'
config(morning-gap): switch to Futu-discovery thresholds

Replace filters/signal/pre_market_filters/pre_market_signal/
min_pre_market_gap_percent with threshold-based keys consumed by
discover_morning_gap_candidates: exchanges, min_gap_percent,
min_market_cap, min_price, min_avg_volume.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: End-to-end verification

**Goal:** Confirm the wired-up pipeline runs cleanly and produces sensible output. No commit at the end of this task.

**Files:** none (run-only)

- [ ] **Step 1: Force-run morning_gap at a known offset**

The scan-window check in `_get_et_scan_offset` will return `None` outside the configured offsets. To exercise the pipeline regardless of clock, temporarily widen the tolerance and pick a single offset. Easiest way: invoke `run_morning_gap` directly with a stubbed offset.

```bash
uv run python -c "
import logging, tomllib
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
import main
with open('config.toml','rb') as f:
    cfg = tomllib.load(f)
mg = cfg['morning_gap']
mg.setdefault('min_adr_percent', cfg['settings']['min_adr_percent'])
mg.setdefault('adr_days', cfg['settings'].get('adr_days', 20))

# Monkey-patch the scan-window check to force a specific offset.
main._get_et_scan_offset = lambda offsets, tol: -10  # pretend pre-market -10m
offset, tickers = main.run_morning_gap(mg, futu_cfg=cfg.get('futu') or {})
print('offset:', offset)
print('tickers:', tickers)
print('TWLO present?', 'TWLO' in tickers)
"
```

Expected (run during pre-market hours on 2026-05-01): `offset: -10`, a non-empty ticker list, **`TWLO present? True`**, full per-phase log lines visible (`Found N tickers from Futu snapshot discovery`, `N after dollar volume filter`, `N after ADR% filter`, `N after SMA50/SMA200 trend filter`, `N after 20d avg volume filter`).

If run outside pre-market hours, swap to `lambda offsets, tol: 15` to exercise the post-open path. The candidate count may legitimately be 0 if no live gap exists — in that case the goal is to verify the pipeline runs without exception and logs each phase.

- [ ] **Step 2: OpenD-down end-to-end test**

```bash
uv run python -c "
import logging, tomllib, copy
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
import main
with open('config.toml','rb') as f:
    cfg = tomllib.load(f)
mg = cfg['morning_gap']
mg.setdefault('min_adr_percent', cfg['settings']['min_adr_percent'])
mg.setdefault('adr_days', cfg['settings'].get('adr_days', 20))

futu_cfg = copy.deepcopy(cfg.get('futu') or {})
futu_cfg['port'] = 11112  # closed port

main._get_et_scan_offset = lambda offsets, tol: -10
offset, tickers = main.run_morning_gap(mg, futu_cfg=futu_cfg)
print('offset:', offset, 'tickers:', tickers)
"
```

Expected: `offset: -10 tickers: []`, with `Futu discovery: OpenD not reachable` and `[Morning Gap] Futu discovery failed ... skipping run` warnings in the log. No exception raised.

- [ ] **Step 3: Full mode invocation (only if currently inside an actual scan window)**

```bash
uv run python main.py --mode morning-gap 2>&1 | tail -80
```

Expected: either `Not in scan window, exiting` (run outside the configured offsets) or a full pipeline run that writes `output/TV/US/<today>_MorningGap{,Pre}.txt`, mirrors to `output/Webull/US/`, and syncs to the `EarningsGap` Futu group. No exceptions, no `ta_topgainers` references in the logs.

- [ ] **Step 4: Inspect the dated output**

```bash
ls -la output/TV/US/$(date +%Y_%m_%d)_MorningGap*.txt 2>/dev/null
cat output/TV/US/$(date +%Y_%m_%d)_MorningGap*.txt 2>/dev/null
```

If a file exists with content, sanity-check the tickers (no penny stocks, no OTC names, all roughly recognizable mid-large caps). If `TWLO` is in there on 2026-05-01, the bug is fixed.

- [ ] **Step 5: No commit (verification only).**

---

## Task 7: Update CLAUDE.md to document the new dependency

**Files:**
- Modify: `CLAUDE.md` — the morning_gap section / Futu section.

- [ ] **Step 1: In the Futu section's "Prerequisites" subsection (around the line that lists the 9 manually-created groups), append a sentence**

Add at the end of the prerequisites subsection:

```markdown
3. The morning-gap scan now **requires** OpenD running — discovery is
   Futu-snapshot based (it no longer depends on Finviz). With OpenD down,
   `--mode morning-gap` writes empty `.txt` files and skips the Futu sync,
   logging a single warning per run.
```

- [ ] **Step 2: Update the morning-gap-related architecture line in CLAUDE.md**

Find the line(s) describing `[morning_gap]` (search for "morning_gap" or "MorningGap"). Adjust any reference to Finviz `ta_topgainers` / `pre_market_filters` / `min_pre_market_gap_percent` to instead describe the Futu-snapshot discovery and the new keys (`min_gap_percent`, `min_market_cap`, `min_price`, `min_avg_volume`, `exchanges`).

If no such line exists yet (the current CLAUDE.md doesn't have an explicit `[morning_gap]` description block), skip — the spec doc is the canonical reference.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude.md): note OpenD prereq for morning-gap discovery

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review checklist (run after all tasks complete)

- [ ] Spec coverage:
  - Goal (replace ta_topgainers) → Task 1 + Task 4.
  - Architecture flow (basicinfo → exchange filter → snapshot → thresholds) → Task 1.
  - SMA50/SMA200 + 20d avg vol gates → Tasks 2, 3, 4.
  - 1y daily window → Task 4 step 3.
  - Config rename → Task 5.
  - Failure modes (OpenD down, snapshot err, basicinfo empty) → Task 1 (returns None) + Task 4 step 2 (warns + returns empty).
  - Verification (TWLO regression, OpenD-down) → Task 6.
  - CLAUDE.md note → Task 7.
- [ ] No placeholders, no "similar to above", every code block complete.
- [ ] Type/name consistency: `discover_morning_gap_candidates` matches across Tasks 1, 4. Config keys match across Tasks 4 and 5.
