# RS Two-Tier Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second RS percentile tier (RS ≥ 80) that gates the 5 Longs splits and the conditional RS group, while Leaders keeps RS ≥ 90.

**Architecture:** New global setting `min_rs_percentile_longs` in `[settings]`. Two new call sites of the existing `filter_by_rs()` from `rs_rating.py` — one inside the Longs per-strategy loop, one inside the RS conditional branch — each placed right after `run_screener` and before any yfinance work. The existing Leaders gate at `main.py:1509-1512` is the template; nothing in `rs_rating.py` changes.

**Tech Stack:** Python 3.12, no test framework — verification is operational (syntax check + grep + smoke run).

**Spec:** `docs/superpowers/specs/2026-05-04-rs-longs-tier-design.md`

---

### Task 1: Wire the new config knob

**Files:**
- Modify: `config.toml` (under `[settings]`)
- Modify: `main.py` (around line 1410 — read setting; line 1437-1438 — fetch trigger)

- [ ] **Step 1: Add the new key to `config.toml`**

Find the existing `min_rs_percentile = 90` line under `[settings]` and add the new line directly below it:

```toml
min_rs_percentile = 90          # Leaders only — IBD RS percentile gate
min_rs_percentile_longs = 80    # Longs (5 splits) + RS group — IBD RS percentile gate
```

- [ ] **Step 2: Read the new setting in `main()`**

In `main.py`, find the block near line 1410 that reads `min_rs_percentile`:

```python
    min_rs_percentile = settings.get("min_rs_percentile", 0)
```

Add the new variable directly below it (preserve the existing comment block above):

```python
    min_rs_percentile = settings.get("min_rs_percentile", 0)
    min_rs_percentile_longs = settings.get("min_rs_percentile_longs", 0)
```

- [ ] **Step 3: Update the RS table fetch trigger**

In `main.py` find lines 1437-1438:

```python
        rs_table = (
            fetch_rs_table(output_dir, today) if min_rs_percentile > 0 else None
        )
```

Replace with:

```python
        rs_table = (
            fetch_rs_table(output_dir, today)
            if max(min_rs_percentile, min_rs_percentile_longs) > 0
            else None
        )
```

- [ ] **Step 4: Syntax check**

Run: `uv run python -c "import main; print('OK')"`
Expected: `OK` (no SyntaxError, no ImportError).

- [ ] **Step 5: Confirm the new key is wired**

Run: `grep -n "min_rs_percentile_longs" /Users/xue/finviz_to_tv/main.py /Users/xue/finviz_to_tv/config.toml`
Expected: 2 matches in `main.py` (the `settings.get` and the `max(...)`) + 1 match in `config.toml`.

- [ ] **Step 6: Commit**

```bash
git add config.toml main.py
git commit -m "feat(rs): add min_rs_percentile_longs setting (no behavior change yet)"
```

---

### Task 2: Apply RS gate inside the Longs per-strategy loop

**Files:**
- Modify: `main.py` (Longs loop body, between line 1462 — `run_screener` log — and line 1463 — dollar-volume filter)

- [ ] **Step 1: Add the RS gate call right after `run_screener` inside the Longs loop**

In `main.py`, find this block (around line 1456-1471 — inside `for i, screener_cfg in enumerate(longs_cfgs):`):

```python
            try:
                tickers = run_screener(screener_cfg["filters"], screener_cfg.get("signal"))
                logger.info(f"  Found {len(tickers)} tickers")
                if (min_dollar_volume > 0 or min_adr_percent > 0) and tickers:
                    tickers = filter_dollar_volume_and_adr_yf(
```

Insert the RS gate call between `logger.info(f"  Found {len(tickers)} tickers")` and the `if (min_dollar_volume > 0 ...)` line so the new block reads:

```python
            try:
                tickers = run_screener(screener_cfg["filters"], screener_cfg.get("signal"))
                logger.info(f"  Found {len(tickers)} tickers")
                if min_rs_percentile_longs > 0 and tickers:
                    tickers = filter_by_rs(
                        tickers, rs_table, min_rs_percentile_longs, f"  [Longs/{key}]"
                    )
                if (min_dollar_volume > 0 or min_adr_percent > 0) and tickers:
                    tickers = filter_dollar_volume_and_adr_yf(
```

The `key` variable is already in scope (assigned at line 1458 above the `try`).

Rationale: gate runs **before** `filter_dollar_volume_and_adr_yf`, matching Leaders. RS-rejected tickers never reach yfinance, so they cannot become IPO drops — same invariant as Leaders today.

- [ ] **Step 2: Syntax check**

Run: `uv run python -c "import main; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Confirm the new call site exists**

Run: `grep -n "Longs/{key}" /Users/xue/finviz_to_tv/main.py`
Expected: 1 match showing `f"  [Longs/{key}]"` inside the `filter_by_rs(...)` call.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(rs): apply RS>=min_rs_percentile_longs gate to all 5 Longs splits"
```

---

### Task 3: Apply RS gate inside the RS conditional branch

**Files:**
- Modify: `main.py` (RS branch body, between line 1572 — `run_screener` log — and line 1573 — dollar-volume filter)

- [ ] **Step 1: Add the RS gate call inside the RS branch**

In `main.py`, find this block (around line 1568-1577 — inside `if check_market_down():`):

```python
                if check_market_down():
                    logger.info("[RS] Condition met, running screener...")
                    time.sleep(delay)
                    found = run_screener(rs_cfg["filters"], rs_cfg.get("signal"))
                    logger.info(f"  Found {len(found)} tickers")
                    if (min_dollar_volume > 0 or min_adr_percent > 0) and found:
                        found = filter_dollar_volume_and_adr_yf(
```

Insert the RS gate call between `logger.info(f"  Found {len(found)} tickers")` and the `if (min_dollar_volume > 0 ...)` line so the new block reads:

```python
                if check_market_down():
                    logger.info("[RS] Condition met, running screener...")
                    time.sleep(delay)
                    found = run_screener(rs_cfg["filters"], rs_cfg.get("signal"))
                    logger.info(f"  Found {len(found)} tickers")
                    if min_rs_percentile_longs > 0 and found:
                        found = filter_by_rs(
                            found, rs_table, min_rs_percentile_longs, "  [RS]"
                        )
                    if (min_dollar_volume > 0 or min_adr_percent > 0) and found:
                        found = filter_dollar_volume_and_adr_yf(
```

Note: the RS group reuses `min_rs_percentile_longs` (per spec — same tier as Longs), NOT `min_rs_percentile`.

- [ ] **Step 2: Syntax check**

Run: `uv run python -c "import main; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Confirm the new call site exists**

Run: `grep -n '\"  \[RS\]\"' /Users/xue/finviz_to_tv/main.py`
Expected: 1 match showing `"  [RS]"` as the label argument inside `filter_by_rs(...)`.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(rs): apply RS>=min_rs_percentile_longs gate to RS conditional group"
```

---

### Task 4: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` ("IBD Relative Strength Rating" section, lines ~38-47)

- [ ] **Step 1: Rewrite the lead paragraph of the IBD RS section**

Find this paragraph in `CLAUDE.md`:

```
`rs_rating.py` pulls the daily IBD-style RS percentile table (0-99) from `Fred6725/rs-log/output/rs_stocks.csv` (the published artifact of the [Fred6725/relative-strength](https://github.com/Fred6725/relative-strength) GitHub Action) and exposes a filter applied to Leaders **right after `run_screener`** — placed before yfinance dollar-volume so a 90+ gate cuts ~80-90% of tickers before any expensive batch download. **Longs are intentionally NOT RS-gated**: the long-side strategies (EarningsGap, HighVolume, GapUp, NewHigh52W, TopGainers) target setups (gap-ups, earnings reactions, volume surges) where the catalyst itself qualifies the name, and a 90+ RS filter would prune fresh breakouts that haven't built a 12-month track record yet.
```

Replace with:

```
`rs_rating.py` pulls the daily IBD-style RS percentile table (0-99) from `Fred6725/rs-log/output/rs_stocks.csv` (the published artifact of the [Fred6725/relative-strength](https://github.com/Fred6725/relative-strength) GitHub Action) and exposes a filter applied to long-side groups **right after `run_screener`** — placed before yfinance dollar-volume so the gate cuts most tickers before any expensive batch download. Two tiers: **Leaders at RS ≥ 90** (top 10% — they're already designed to find leadership names) and **Longs (5 splits) + RS group at RS ≥ 80** (top 20% — softer because catalyst-driven setups need to allow fresh breakouts that don't yet have a 12-month track record). Shorts, HK Shorts, Morning Gap, and IPO are NOT RS-gated.
```

- [ ] **Step 2: Update the Config bullet**

Find the line:

```
- **Config**: `[settings] min_rs_percentile = 90`. Set to 0 to disable entirely (skips the fetch too).
```

Replace with:

```
- **Config**: `[settings] min_rs_percentile = 90` (Leaders) and `min_rs_percentile_longs = 80` (Longs + RS group). Set either to 0 to disable that tier independently; the GitHub fetch is skipped only when both are 0.
```

- [ ] **Step 3: Update the Scope bullet**

Find the line:

```
- **Scope**: Leaders only. Longs splits are not RS-gated (catalyst-driven setups). Shorts (parabolic blow-offs are by definition high-RS) and HK Shorts are not filtered. The conditional `[rs]` group is also unfiltered — its purpose is to find relative-strength names on a weak market day, which already overlaps the IBD definition.
```

Replace with:

```
- **Scope**: Two tiers. **Leaders ≥ 90** — the strictest gate for the trend-leadership pipeline. **Longs (5 splits) + RS group ≥ 80** — softer gate so catalyst-driven setups (EarningsGap, HighVolume, GapUp, NewHigh52W, TopGainers) and the conditional weak-market RS scan still admit recent breakouts without a full 12-month track record. **Shorts and HK Shorts** are unfiltered (parabolic blow-offs are by definition high-RS, and the Finviz pre-filter already does the work). **Morning Gap and IPO** are unfiltered (Morning Gap is intraday discovery; IPO is by definition pre-RS-rating).
```

- [ ] **Step 4: Confirm the file still parses as Markdown (no orphan code-fences)**

Run: `grep -c '^\`\`\`' /Users/xue/finviz_to_tv/CLAUDE.md`
Expected: an even number (every fence opens and closes).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): document two-tier RS gate (Leaders 90, Longs/RS 80)"
```

---

### Task 5: Update README.md

**Files:**
- Modify: `README.md` ("Global gates (long-side)" section, lines ~9-21)

- [ ] **Step 1: Split the IBD RS row in the Global gates table**

Find the table row in `README.md`:

```
| **IBD RS Percentile** | Leaders only | ≥ 90 (top 10% momentum names; missing tickers KEPT to avoid pruning recent IPOs) | [Fred6725/rs-log](https://github.com/Fred6725/relative-strength), `RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` normalised against SPY, refreshed weekday ~01:30 UTC |
```

Replace with two rows:

```
| **IBD RS Percentile (Leaders)** | Leaders | ≥ 90 (top 10%; missing tickers KEPT) | [Fred6725/rs-log](https://github.com/Fred6725/relative-strength), `RS = 0.4·P3 + 0.2·P6 + 0.2·P9 + 0.2·P12` normalised against SPY, refreshed weekday ~01:30 UTC |
| **IBD RS Percentile (Longs/RS)** | Longs (5 splits) + RS group | ≥ 80 (top 20%; missing tickers KEPT) | same source as above |
```

- [ ] **Step 2: Replace the "Why RS is Leaders-only" paragraph**

Find this paragraph (immediately after the table):

```
**Why RS is Leaders-only:** the Longs strategies are catalyst-driven (gap-ups, earnings reactions, volume surges) where the trigger itself qualifies the name. A 90+ RS gate would prune fresh breakouts that haven't built a 12-month track record yet.
```

Replace with:

```
**Why two RS tiers:** Leaders is a trend-leadership pipeline, so the strictest gate (top 10%) makes sense. Longs and the conditional RS group are catalyst-driven (gap-ups, earnings reactions, volume surges, weak-market relative-strength) where the trigger itself qualifies the name — a 90+ gate would prune fresh breakouts that haven't built a 12-month track record. RS ≥ 80 (top 20%) keeps that headroom while still cutting the tail.
```

- [ ] **Step 3: Update the "All 5 also pass…" line under the Longs section**

Find:

```
All 5 also pass the global Dollar Volume / ADR% gates above (RS is Leaders-only).
```

Replace with:

```
All 5 also pass the global Dollar Volume / ADR% gates and IBD RS ≥ 80.
```

- [ ] **Step 4: Update the RS group section**

Find the line under "RS — Relative Strength (conditional)":

```
Filters: Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200, Dollar Volume ≥ $100M, ADR% ≥ 4.0%. (Not gated by IBD RS — relative strength on a weak day already overlaps with the IBD definition.)
```

Replace with:

```
Filters: Small Cap+, Avg Vol > 500K, Price > $20, Day Up, Above SMA50 & SMA200, Dollar Volume ≥ $100M, ADR% ≥ 4.0%, IBD RS ≥ 80.
```

- [ ] **Step 5: Confirm Markdown structure**

Run: `grep -c '^\`\`\`' /Users/xue/finviz_to_tv/README.md`
Expected: an even number.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs(readme): document two-tier RS gate (Leaders 90, Longs/RS 80)"
```

---

### Task 6: Operational smoke test

**Files:**
- No file changes — verification only.

This task does NOT commit. It validates that the live pipeline behaves as designed.

- [ ] **Step 1: Smoke run with both gates active (default config)**

Run: `uv run main.py 2>&1 | tee /tmp/rs_tier_smoke.log`
Wait for the run to complete (8s delay between Finviz requests, full pipeline takes ~5-10 minutes).

- [ ] **Step 2: Verify the RS table fetch happened**

Run:

```bash
TODAY=$(date +%Y_%m_%d)
ls -la /Users/xue/finviz_to_tv/output/state/rs_rating_${TODAY}.csv
```

Expected: file exists, non-zero size.

- [ ] **Step 3: Verify Leaders gate logged at 90**

Run: `grep -E "\[Leaders/.*\] .* RS >= 90" /tmp/rs_tier_smoke.log | head -5`
Expected: at least one line per Leaders strategy that ran (5 lines at most).

- [ ] **Step 4: Verify Longs gate logged at 80 for all 5 splits**

Run: `grep -E "\[Longs/.*\] .* RS >= 80" /tmp/rs_tier_smoke.log | sort -u`
Expected: 5 distinct labels — one each for `EarningsGap`, `HighVolume`, `GapUp`, `NewHigh52W`, `TopGainers`.

- [ ] **Step 5: Verify RS group gate logged at 80 (only if SPY+QQQ both down >1.5%)**

Run: `grep -E "\[RS\] (Condition met|Condition not met)" /tmp/rs_tier_smoke.log`

If "Condition met" appears: also run `grep -E "\[RS\] .* RS >= 80" /tmp/rs_tier_smoke.log` and expect exactly 1 line.
If "Condition not met" appears: skip — the branch didn't run, which is fine.

- [ ] **Step 6: Ablation — disable Longs tier, keep Leaders**

Edit `config.toml`: temporarily change `min_rs_percentile_longs = 80` to `min_rs_percentile_longs = 0`.

Run: `uv run main.py 2>&1 | tee /tmp/rs_tier_ablation.log`

Verify:
- `grep -c "\[Longs/.*\] .* RS >= " /tmp/rs_tier_ablation.log` → expected: `0` (gate skipped).
- `grep -c "\[Leaders/.*\] .* RS >= 90" /tmp/rs_tier_ablation.log` → expected: ≥ 1 (Leaders gate still ran).

Restore `config.toml` (`min_rs_percentile_longs = 80`).

- [ ] **Step 7: Ablation — disable both tiers**

Edit `config.toml`: temporarily change BOTH to 0:

```toml
min_rs_percentile = 0
min_rs_percentile_longs = 0
```

Delete today's RS cache so we can verify the fetch is skipped:

```bash
rm /Users/xue/finviz_to_tv/output/state/rs_rating_$(date +%Y_%m_%d).csv
```

Run: `uv run main.py 2>&1 | tee /tmp/rs_tier_disabled.log`

Verify:
- `grep -cE "\[RS Rating\] (Loaded|Using cached)" /tmp/rs_tier_disabled.log` → expected: `0` (fetch never happened, no cache load).
- `grep -cE "RS >= " /tmp/rs_tier_disabled.log` → expected: `0` (no gate ran).
- `ls /Users/xue/finviz_to_tv/output/state/rs_rating_$(date +%Y_%m_%d).csv 2>&1` → expected: "No such file" (cache absent).

Restore `config.toml` to the production values:

```toml
min_rs_percentile = 90
min_rs_percentile_longs = 80
```

- [ ] **Step 8: Final sanity check**

Run: `git status` — expected: clean (config restored, no stray edits).
Run: `git log --oneline -7` — expected: 5 task commits + the spec commit + the prior CLAUDE.md commit.

---

## Self-review notes

- **Spec coverage**: every spec section is implemented — config (Task 1), code (Tasks 1-3), docs (Tasks 4-5), testing (Task 6). Out-of-scope items (per-strategy thresholds, gating Shorts/HK/MorningGap, changing missing-ticker behavior) are not addressed, as intended.
- **Placeholder scan**: no TBDs or vague "add error handling" steps. All code blocks are concrete.
- **Type/name consistency**: setting name `min_rs_percentile_longs` used identically across config, `main.py`, CLAUDE.md, README.md. Existing `filter_by_rs(tickers, rs_table, min_percentile, label)` signature reused unchanged.
- **Ordering**: each task depends only on the prior code/config edits (Task 2 depends on Task 1's variable; Task 3 depends on Task 1's variable). Doc tasks (4, 5) can run in any order after Task 1. Smoke test (6) requires all prior tasks committed.
