# RS Line Direction Audit (Phase B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only `--mode rs-line-audit` tool that scores every ticker already in `output/state/eod_seen_{US,HK}.txt` by its RS-line 21EMA 5-bar direction and prints the full distribution (weakest-first, with the tolerance cut line marked), so the operator can judge whether "RS-line direction up" is a good screening rule before any hard-drop gate is built.

**Architecture:** Pure compute lives in `rs_line.py` (new `compute_rs_direction`, mirroring the existing `compute_rs_line_features`). A new `rs_line_audit.py` orchestrates: load the seen-master, fetch ~6mo klines + benchmark via the existing yfinance fetchers, call `compute_rs_direction`, render a text report, and write a dated file under `output/`. `main.py` gets a `rs-line-audit` mode that dispatches early (before EOD machinery) and returns. **Nothing is mutated** — no `.txt`, no master, no Futu.

**Tech Stack:** Python 3, pandas, yfinance (existing `fetch_us_klines_yf` / `fetch_hk_klines_yf` / `fetch_hsi_kline_yf`), pytest, `uv`.

**Scope:** Phase B only (the audit). Phase A (the cloud column + hard-drop entry gate) is a separate plan, built only after the user validates the criterion with this tool. Spec: `docs/superpowers/specs/2026-05-28-rs-line-direction-audit-design.md`.

---

## File structure

| File | Responsibility | Action |
|------|----------------|--------|
| `rs_line.py` | add `compute_rs_direction` (pure: per-id `rs_ema` + `rs_ema_chg_5d`); add `lookback`/`tolerance` to `params_from_config` | Modify |
| `config.toml` | add `lookback` + `tolerance` to `[rs_line]` | Modify |
| `rs_line_audit.py` | orchestration: load master, normalize tickers, fetch, compute, `render_report`, write dated file | Create |
| `main.py` | `rs-line-audit` mode in argparse + early dispatch; `both` added to `--market` | Modify |
| `tests/test_rs_line.py` | tests for `compute_rs_direction` | Modify |
| `tests/test_rs_line_audit.py` | tests for `render_report` + HK ticker normalization (pure, no network) | Create |

---

## Task 0: Sync local `main` (prerequisite)

Local `main` carries exactly one local-only commit (the spec, `3ab11fe`); the merged v1 `rs_line.py` (PR #22) is on `origin/main` (`d720339`). Rebase the single commit on top so the working tree has `rs_line.py` to extend.

- [ ] **Step 1: Fetch and rebase**

Run:
```bash
git fetch origin main
git rebase origin/main
```
Expected: `Successfully rebased and updated refs/heads/main.` (the lone `3ab11fe` spec commit replays cleanly — it only adds a new file under `docs/superpowers/specs/`).

- [ ] **Step 2: Verify the v1 module is now present**

Run: `ls rs_line.py tests/test_rs_line.py && uv run pytest tests/test_rs_line.py -q`
Expected: both files exist; existing v1 tests pass.

---

## Task 1: `compute_rs_direction` in `rs_line.py`

**Files:**
- Modify: `rs_line.py` (add constant `DEFAULT_LOOKBACK`, function `compute_rs_direction`)
- Test: `tests/test_rs_line.py`

Mirrors the existing `compute_rs_line_features`: same date-aligned `close/benchmark` ratio and `_moving_average`, but instead of position it returns the EMA's current value and its `lookback`-bar percentage change. Ids with `< min_history` MA-valid bars are omitted (⇒ unknown ⇒ kept downstream). Never raises.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_rs_line.py` (the existing `_kline(closes, end=...)` helper builds `DataFrame[time_key, close]`; reuse it):

```python
from rs_line import compute_rs_direction


def _const_bench(n, end="2026-05-28"):
    return _kline([100.0] * n, end=end)


def test_direction_positive_on_rising_ratio():
    n = 80
    closes = [10.0 * (1.01 ** i) for i in range(n)]  # steadily rising vs flat bench
    feats = compute_rs_direction({"UP": _kline(closes)}, _const_bench(n))
    assert "UP" in feats.index
    assert feats.loc["UP", "rs_ema_chg_5d"] > 0


def test_direction_negative_on_falling_ratio():
    n = 80
    closes = [10.0 * (1.01 ** i) for i in range(n - 10)] + [
        10.0 * (1.01 ** (n - 10)) * (0.97 ** j) for j in range(1, 11)
    ]  # rises then rolls over in the last ~2 weeks
    feats = compute_rs_direction({"DN": _kline(closes)}, _const_bench(n))
    assert feats.loc["DN", "rs_ema_chg_5d"] < 0


def test_direction_flat_is_near_zero():
    n = 80
    feats = compute_rs_direction({"FLAT": _kline([10.0] * n)}, _const_bench(n))
    assert abs(feats.loc["FLAT", "rs_ema_chg_5d"]) < 1e-9


def test_direction_scale_invariant_to_benchmark():
    n = 80
    closes = [10.0 + 0.05 * i for i in range(n)]
    a = compute_rs_direction({"T": _kline(closes)}, _kline([100.0] * n))
    b = compute_rs_direction({"T": _kline(closes)}, _kline([1000.0] * n))  # ×10
    assert abs(a.loc["T", "rs_ema_chg_5d"] - b.loc["T", "rs_ema_chg_5d"]) < 1e-12


def test_direction_short_history_excluded():
    feats = compute_rs_direction({"SHORT": _kline([10.0] * 20)}, _const_bench(20))
    assert "SHORT" not in feats.index  # < min_history ⇒ unknown ⇒ omitted


def test_direction_lookback_measures_five_bars():
    # EMA of a ramp is monotone; chg over 5 bars must be strictly positive and
    # larger than chg over 1 bar would be — sanity that lookback indexes back 5.
    n = 80
    closes = [10.0 + 0.1 * i for i in range(n)]
    feats = compute_rs_direction({"R": _kline(closes)}, _const_bench(n), lookback=5)
    assert feats.loc["R", "rs_ema_chg_5d"] > 0
    assert "rs_ema" in feats.columns and feats.loc["R", "rs_ema"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rs_line.py -k direction -q`
Expected: FAIL — `ImportError: cannot import name 'compute_rs_direction'`.

- [ ] **Step 3: Implement `compute_rs_direction`**

In `rs_line.py`, add the constant near the other defaults:

```python
DEFAULT_LOOKBACK = 5
```

and add the function after `compute_rs_line_features`:

```python
def compute_rs_direction(
    klines: dict[str, pd.DataFrame],
    benchmark_kline: pd.DataFrame | None,
    ma_length: int = DEFAULT_MA_LENGTH,
    ma_type: str = DEFAULT_MA_TYPE,
    lookback: int = DEFAULT_LOOKBACK,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> pd.DataFrame:
    """Per-id RS-line 21EMA direction, indexed by the ``klines`` dict key.

    rs_line = close / benchmark_close (date-aligned inner join); ma = EMA/SMA.
    Columns:
      rs_ema         float  latest MA value of the RS line (descriptive only;
                            scale-dependent — NOT comparable to a TV chart level)
      rs_ema_chg_5d  float  (ma[-1] - ma[-1-lookback]) / ma[-1-lookback]
    Ids with < ``min_history`` MA-valid bars are EXCLUDED (unknown). Scale-
    invariant: scaling the benchmark by a constant leaves rs_ema_chg_5d
    unchanged. ``min_history`` must exceed ``lookback``. Never raises.
    """
    cols = ["rs_ema", "rs_ema_chg_5d"]
    if benchmark_kline is None or getattr(benchmark_kline, "empty", True):
        return pd.DataFrame(columns=cols)
    bench = (
        benchmark_kline[["time_key", "close"]]
        .rename(columns={"close": "_bench"})
        .dropna()
    )

    rows: dict[str, tuple[float, float]] = {}
    for tid, df in klines.items():
        if df is None or df.empty or "close" not in df or "time_key" not in df:
            continue
        m = (
            df[["time_key", "close"]]
            .dropna()
            .merge(bench, on="time_key", how="inner")
            .sort_values("time_key")
        )
        rs = m["close"].astype(float) / m["_bench"].astype(float)
        ma = _moving_average(rs, ma_length, ma_type)
        ma = ma[ma.notna()]
        if len(ma) < min_history:  # min_history >= lookback+1 by config, so index is safe
            continue
        ema_now = float(ma.iloc[-1])
        ema_prior = float(ma.iloc[-1 - lookback])
        rows[tid] = (round(ema_now, 6), round((ema_now - ema_prior) / ema_prior, 6))

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame.from_dict(rows, orient="index", columns=cols)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rs_line.py -k direction -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add rs_line.py tests/test_rs_line.py
git commit -m "feat(rs_line): compute_rs_direction — 21EMA 5-bar slope of the RS line"
```

---

## Task 2: `[rs_line]` config knobs + `params_from_config`

**Files:**
- Modify: `config.toml` (`[rs_line]` section)
- Modify: `rs_line.py` (`params_from_config`, add `DEFAULT_TOLERANCE`)
- Test: `tests/test_rs_line.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_rs_line.py`:

```python
from rs_line import params_from_config


def test_params_from_config_includes_lookback_and_tolerance():
    cfg = {"rs_line": {"lookback": 7, "tolerance": 0.003}}
    p = params_from_config(cfg)
    assert p["lookback"] == 7
    assert p["tolerance"] == 0.003


def test_params_from_config_defaults_lookback_tolerance():
    p = params_from_config({})
    assert p["lookback"] == 5
    assert p["tolerance"] == 0.005
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rs_line.py -k params_from_config -q`
Expected: FAIL — `KeyError: 'lookback'`.

- [ ] **Step 3: Extend `params_from_config` and add the default**

In `rs_line.py` add near the other defaults:

```python
DEFAULT_TOLERANCE = 0.005
```

and add the two keys to the dict returned by `params_from_config`:

```python
        "lookback": int(cfg.get("lookback", DEFAULT_LOOKBACK)),
        "tolerance": float(cfg.get("tolerance", DEFAULT_TOLERANCE)),
```

- [ ] **Step 4: Update `config.toml`**

In the `[rs_line]` section, add after `min_history`:

```toml
lookback  = 5           # 方向斜率的回看交易日数 (5 bar)
tolerance = 0.005       # 0.5% 噪声带 — 工作假设, 由 --mode rs-line-audit 验证后再定死
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_rs_line.py -k params_from_config -q`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add rs_line.py config.toml tests/test_rs_line.py
git commit -m "feat(rs_line): [rs_line] lookback + tolerance config knobs"
```

---

## Task 3: `render_report` — pure text rendering

**Files:**
- Create: `rs_line_audit.py` (with `render_report` first)
- Test: `tests/test_rs_line_audit.py`

`render_report` takes the full id list, the direction frame, and the tolerance; returns text listing every scored id sorted weakest-first with a `CUT` flag for `chg < -tolerance`, a separate unknown list, and a summary line. No network, no I/O.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rs_line_audit.py`:

```python
import pandas as pd

from rs_line_audit import render_report


def _frame(d):
    return pd.DataFrame.from_dict(
        d, orient="index", columns=["rs_ema", "rs_ema_chg_5d"]
    )


def test_render_sorts_weakest_first_and_flags_cuts():
    feats = _frame({
        "AAA": (0.01, 0.06),    # +6%  strong
        "BBB": (0.02, -0.032),  # -3.2% cut
        "CCC": (0.03, -0.004),  # -0.4% within band
        "DDD": (0.04, -0.009),  # -0.9% cut
    })
    text = render_report(["AAA", "BBB", "CCC", "DDD"], feats, tolerance=0.005,
                         market="US", as_of="2026-05-28")
    # weakest first: BBB(-3.2) DDD(-0.9) CCC(-0.4) AAA(+6)
    assert text.index("BBB") < text.index("DDD") < text.index("CCC") < text.index("AAA")
    # cut flag only on chg < -0.5%
    bbb_line = next(l for l in text.splitlines() if "BBB" in l)
    ccc_line = next(l for l in text.splitlines() if "CCC" in l)
    assert "CUT" in bbb_line
    assert "CUT" not in ccc_line


def test_render_lists_unknowns_and_counts():
    feats = _frame({"AAA": (0.01, 0.06), "BBB": (0.02, -0.032)})
    text = render_report(["AAA", "BBB", "ZZZ", "YYY"], feats, tolerance=0.005,
                         market="US", as_of="2026-05-28")
    assert "ZZZ" in text and "YYY" in text          # unknown names shown
    assert "scanned: 4" in text
    assert "scored: 2" in text
    assert "unknown: 2" in text
    assert "would-cut: 1" in text


def test_render_handles_all_unknown_without_crash():
    text = render_report(["AAA", "BBB"], _frame({}), tolerance=0.005,
                         market="HK", as_of="2026-05-28")
    assert "scored: 0" in text and "unknown: 2" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rs_line_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rs_line_audit'`.

- [ ] **Step 3: Implement `render_report` in a new `rs_line_audit.py`**

```python
"""Read-only RS-line direction audit over the cross-day 'seen' masters.

Scores every already-surfaced ticker (output/state/eod_seen_{US,HK}.txt) by its
RS-line 21EMA 5-bar direction and prints the full distribution weakest-first with
the tolerance cut line marked, so the operator can judge whether 'direction up'
is a good screening rule before it gates any output. Touches nothing: no .txt, no
master, no Futu. yfinance is acceptable here (manual, one-off, bounded list).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def render_report(
    ids: list[str],
    direction: pd.DataFrame,
    tolerance: float,
    market: str,
    as_of: str,
) -> str:
    """Build the audit text. ``direction`` is indexed by id with columns
    rs_ema, rs_ema_chg_5d. Ids absent from it are 'unknown' (kept downstream)."""
    scored = [i for i in ids if i in direction.index
              and pd.notna(direction.loc[i, "rs_ema_chg_5d"])]
    unknown = [i for i in ids if i not in scored]
    scored.sort(key=lambda i: float(direction.loc[i, "rs_ema_chg_5d"]))
    would_cut = sum(
        1 for i in scored if float(direction.loc[i, "rs_ema_chg_5d"]) < -tolerance
    )

    cut_pct = f"-{tolerance * 100:.2f}%"
    lines = [
        f"RS-line direction audit — {market} — {as_of}",
        f"signal: RS-line 21EMA 5-bar slope  |  cut line: chg < {cut_pct}",
        "",
        f"  {'rank':>4}  {'ticker':<14} {'chg_5d':>9}  {'EMA21':>12}  flag",
    ]
    for rank, i in enumerate(scored, 1):
        chg = float(direction.loc[i, "rs_ema_chg_5d"])
        ema = float(direction.loc[i, "rs_ema"])
        flag = "CUT" if chg < -tolerance else ""
        lines.append(f"  {rank:>4}  {i:<14} {chg * 100:>8.2f}%  {ema:>12.6f}  {flag}")

    lines.append("")
    if unknown:
        lines.append(f"unknown (insufficient history, KEPT): {', '.join(unknown)}")
    lines.append(
        f"scanned: {len(ids)} | scored: {len(scored)} | "
        f"unknown: {len(unknown)} | would-cut: {would_cut}"
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rs_line_audit.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add rs_line_audit.py tests/test_rs_line_audit.py
git commit -m "feat(rs_line): render_report for the direction audit"
```

---

## Task 4: HK ticker normalization helper

**Files:**
- Modify: `rs_line_audit.py` (add `_hk_master_to_futu`)
- Test: `tests/test_rs_line_audit.py`

The US master stores plain symbols (`NVDA`); the HK master stores TV format with `HKEX:` prefix and stripped leading zeros (`HKEX:522`). `fetch_hk_klines_yf` wants 4-digit codes and returns Futu keys (`HK.00522`). This helper maps a master entry to the Futu key the fetch output will use, so results re-key back cleanly.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_rs_line_audit.py`:

```python
from rs_line_audit import _hk_master_to_futu


def test_hk_master_to_futu_pads_and_prefixes():
    assert _hk_master_to_futu("HKEX:522") == "HK.00522"
    assert _hk_master_to_futu("HKEX:1304") == "HK.01304"
    assert _hk_master_to_futu("148") == "HK.00148"   # tolerate bare code
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rs_line_audit.py -k hk_master -q`
Expected: FAIL — `ImportError: cannot import name '_hk_master_to_futu'`.

- [ ] **Step 3: Implement the helper**

Add to `rs_line_audit.py`:

```python
def _hk_master_to_futu(entry: str) -> str:
    """'HKEX:522' / '522' -> Futu key 'HK.00522' (5-digit, zero-padded)."""
    code = entry.split(":", 1)[-1].strip()
    return f"HK.{int(code):05d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_rs_line_audit.py -k hk_master -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add rs_line_audit.py tests/test_rs_line_audit.py
git commit -m "feat(rs_line): HK master->Futu key normalization for the audit"
```

---

## Task 5: `run_audit` orchestration (network; manual verification)

**Files:**
- Modify: `rs_line_audit.py` (add `_load_seen`, `_audit_market`, `run_audit`)

Loads the seen-master, fetches klines + benchmark, computes direction (keyed by the **master entry** so results map straight back), renders, prints, and writes a dated file. Network-bound — verified by a real run in Task 7, not a unit test.

- [ ] **Step 1: Implement the orchestration**

Add to `rs_line_audit.py` (imports at top: `from datetime import date`):

```python
import rs_line


def _load_seen(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


def _audit_market(market: str, config: dict, output_dir: Path) -> str | None:
    """Return the report text for one market, or None if its master is empty."""
    seen_path = output_dir / "state" / f"eod_seen_{market.upper()}.txt"
    ids = _load_seen(seen_path)
    if not ids:
        logger.info(f"[rs-line-audit] {market.upper()} master empty — skipping")
        return None

    params = rs_line.params_from_config(config)
    tolerance = params["tolerance"]
    direction_kwargs = {k: params[k] for k in ("ma_length", "ma_type", "lookback", "min_history")}

    if market == "us":
        from us_rs_3m import fetch_us_klines_yf
        bench = fetch_us_klines_yf(["SPY"], period="6mo", batch_size=1).get("SPY")
        klines = fetch_us_klines_yf(ids, period="6mo")  # keyed by symbol == master entry
    else:  # hk
        from hk_eod import fetch_hk_klines_yf, fetch_hsi_kline_yf
        bench = fetch_hsi_kline_yf(period="2y")
        codes = {e: e.split(":", 1)[-1].strip().zfill(4) for e in ids}
        fetched = fetch_hk_klines_yf(list(codes.values()), period="2y")
        klines = {e: fetched.get(_hk_master_to_futu(e)) for e in ids}

    direction = rs_line.compute_rs_direction(klines, bench, **direction_kwargs)
    as_of = date.today().strftime("%Y-%m-%d")
    text = render_report(ids, direction, tolerance, market.upper(), as_of)

    out_file = output_dir / f"rs_line_audit_{market.upper()}_{as_of}.txt"
    out_file.write_text(text + "\n")
    logger.info(f"[rs-line-audit] wrote {out_file}")
    return text


def run_audit(config: dict, output_dir: Path, market: str = "both") -> int:
    """Read-only audit entry point. Never mutates .txt / master / Futu."""
    markets = ["us", "hk"] if market == "both" else [market]
    for m in markets:
        text = _audit_market(m, config, output_dir)
        if text is not None:
            print("\n" + text + "\n")
    return 0
```

- [ ] **Step 2: Verify the module imports and the test suite still passes**

Run: `uv run python -c "import rs_line_audit" && uv run pytest tests/test_rs_line_audit.py tests/test_rs_line.py -q`
Expected: import OK; all tests pass (orchestration has no new unit test — covered by Task 7's live run).

- [ ] **Step 3: Commit**

```bash
git add rs_line_audit.py
git commit -m "feat(rs_line): run_audit orchestration (read-only, US+HK)"
```

---

## Task 6: `--mode rs-line-audit` CLI dispatch

**Files:**
- Modify: `main.py` (argparse `--mode` choices ~line 1396; `--market` choices ~line 1406; dispatch after the `report` branch ~line 1481)

- [ ] **Step 1: Add the mode to argparse**

In `main.py`, add `"rs-line-audit"` to the `--mode` `choices` list (around line 1396) and `"both"` to the `--market` `choices` list (around line 1406):

```python
        choices=["eod", "us-eod", "hk-eod", "morning-gap", "hk-morning-gap", "report", "rs-line-audit"],
```
```python
        "--market", choices=["us", "hk", "both"],
```

- [ ] **Step 2: Add the early dispatch branch**

Immediately after the `report` dispatch block (the `if args.mode == "report":` block ending with `return run_report(...)`, ~line 1481) and **before** `if args.mode == "hk-eod":`, insert:

```python
    if args.mode == "rs-line-audit":
        from rs_line_audit import run_audit
        return run_audit(config, output_dir, args.market or "both")
```

(`config` and `output_dir` are already in scope at this point; the audit returns before any EOD machinery runs.)

- [ ] **Step 3: Verify the CLI parses and dispatches**

Run: `uv run main.py --mode rs-line-audit --market us 2>&1 | head -5`
Expected: the run banner prints with `mode=rs-line-audit`, then yfinance fetch logs begin (no argparse error, no EOD setup).

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(rs_line): --mode rs-line-audit CLI dispatch"
```

---

## Task 7: Live end-to-end run + no-mutation verification

**Files:** none (verification only)

- [ ] **Step 1: Snapshot the state masters (to prove they are untouched)**

Run:
```bash
md5 output/state/eod_seen_US.txt output/state/eod_seen_HK.txt
```
Record the two checksums.

- [ ] **Step 2: Run the full audit**

Run: `uv run main.py --mode rs-line-audit`
Expected: a US table (~132 rows, weakest-first, `CUT` flags on `chg < -0.50%`), an unknown list, a summary line; then the HK table (~8 rows). Two files written: `output/rs_line_audit_US_<date>.txt`, `output/rs_line_audit_HK_<date>.txt`.

- [ ] **Step 3: Confirm nothing was mutated**

Run:
```bash
md5 output/state/eod_seen_US.txt output/state/eod_seen_HK.txt
git status --short output/state/
```
Expected: checksums **identical** to Step 1; no `.txt`/master changes staged or unstaged. (The new `output/rs_line_audit_*.txt` files may appear as untracked — that is the only output.)

- [ ] **Step 4: Sanity-check the distribution**

Read `output/rs_line_audit_US_<date>.txt`. Confirm recognizable strong names sit well above 0 and the `would-cut` count is plausible. **This is the artifact the user reviews to judge the 0.5% criterion** — hand it over for that decision before any Phase A work.

- [ ] **Step 5: Full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass (no regressions in the existing v1 `rs_line` tests).

---

## Self-review notes

- **Spec coverage:** read-only audit over `eod_seen_{US,HK}` ✓ (Tasks 5,7); full sorted distribution + cut-line marker ✓ (Task 3); unknown⇒keep/shown ✓ (Tasks 1,3); 5 trading bars ✓ (Task 1); EMA21 surfaced, scale-note ✓ (Tasks 1,3); yfinance one-off, bounded ✓ (Task 5); dated report file + `--mode rs-line-audit` ✓ (Tasks 5,6); `[rs_line]` lookback+tolerance ✓ (Task 2); no `.txt`/master/Futu mutation ✓ (Task 7). Phase A intentionally **out of scope** (separate plan).
- **Type consistency:** `compute_rs_direction` returns columns `rs_ema`, `rs_ema_chg_5d` — same names consumed by `render_report` and the tests throughout. `_hk_master_to_futu` matches the `HK.{:05d}` key produced by `fetch_hk_klines_yf`. `params_from_config` keys (`ma_length`, `ma_type`, `lookback`, `min_history`, `tolerance`) match `_audit_market`'s usage.
- **No placeholders:** every code/test/command step is concrete.
```
