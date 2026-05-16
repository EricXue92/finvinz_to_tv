# RS Skip Dedup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make US RS and HK RS bypass both within-day priority dedup and cross-day master dedup, so the weak-market-triggered scan can re-surface tickers already collected in other long-side groups.

**Architecture:** Surgical edits to two call sites — `main.py` (US) and `hk_eod.py` (HK). The pure helper `dedup_by_priority` in `hk_eod.py` already supports a subset-priority arg, so no helper changes; the HK call site is changed to pass the 4-strategy subset and splice RS back unchanged. The cross-day-master loop in HK gets a `name == "RS"` short-circuit. On the US side, two lines are removed (within-day RS subtraction; cross-day `_dedup_seen` for RS) and one log line is rewritten.

**Tech Stack:** Python 3.13, pytest, existing `tests/test_hk_eod.py` for pure-logic coverage.

**Spec:** `docs/superpowers/specs/2026-05-16-rs-skip-dedup-design.md` (commit `a0b7b03`).

---

## File Map

| File | Why |
|---|---|
| `tests/test_hk_eod.py` | Add unit test pinning the new `dedup_by_priority` call shape (RS-excluded subset). |
| `hk_eod.py` | Carve RS out of (a) within-day priority dedup, (b) cross-day master dedup loop. |
| `main.py` | Carve RS out of (a) within-day cross-group dedup, (b) `_dedup_seen` call for RS write block. |
| `CLAUDE.md` | Doc update: move RS into the "Excluded" list under the cross-day-master-dedup paragraph; clarify within-day priority is now Longs > Leaders only. |

No new files. No config changes. No Futu config changes.

---

## Task 1: Unit test for `dedup_by_priority` RS-excluded subset

The existing helper already accepts `priority=[...]`. We add a test pinning the **exact call shape** the new HK code will use, so a future refactor can't silently break the carve-out.

**Files:**
- Modify: `tests/test_hk_eod.py` (append after line 115)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_hk_eod.py`:

```python
def test_dedup_by_priority_excludes_rs_when_priority_is_subset():
    """When the caller passes a 4-strategy priority subset (RS omitted),
    RS must be absent from the output dict — the caller is responsible
    for splicing it back in untouched. This pins the contract used by
    run_hk_eod to carve RS out of within-day priority dedup."""
    raw = {
        "EarningsGap": ["A"],
        "HighVolume":  ["B"],
        "GapUp":       ["C"],
        "Leaders":     ["D", "E"],
        "RS":          ["A", "D", "F"],  # would normally be dropped to ["F"]
    }
    out = dedup_by_priority(
        raw,
        priority=["EarningsGap", "HighVolume", "GapUp", "Leaders"],
    )
    assert "RS" not in out
    assert out == {
        "EarningsGap": ["A"],
        "HighVolume":  ["B"],
        "GapUp":       ["C"],
        "Leaders":     ["D", "E"],
    }
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run: `uv run pytest tests/test_hk_eod.py::test_dedup_by_priority_excludes_rs_when_priority_is_subset -v`

Expected: PASS. The helper already supports the subset priority — this test is a *pinning* test, not a TDD driver. (The TDD driver for the actual carve-out lives in Task 2, where we run the full suite after the HK code change.)

- [ ] **Step 3: Run the full HK test file to confirm no regression**

Run: `uv run pytest tests/test_hk_eod.py -v`

Expected: all green, including the existing `test_dedup_by_priority_strips_lower_priority_duplicates`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_hk_eod.py
git commit -m "test(hk_eod): pin dedup_by_priority RS-excluded subset contract"
```

---

## Task 2: HK RS — carve out of within-day priority dedup

**Files:**
- Modify: `hk_eod.py:886-891` (priority-dedup call + log line)

- [ ] **Step 1: Read the current block to confirm line numbers**

Run: `sed -n '883,915p' hk_eod.py`

Expected output (verify before editing):
```python
        + ", ".join(f"{n} {pre_rs_counts[n]}→{post_rs_counts[n]} (-{rs_drops[n]})" for n in HK_STRATEGY_PRIORITY)
    )

    # --- Within-day cross-strategy priority dedup ---
    dedup = dedup_by_priority(raw)
    logger.info(
        "[HK Longs] within-day priority dedup: "
        + ", ".join(f"{n} {post_rs_counts[n]}→{len(dedup[n])}" for n in HK_STRATEGY_PRIORITY)
    )
```

- [ ] **Step 2: Apply the carve-out edit**

In `hk_eod.py`, replace:

```python
    # --- Within-day cross-strategy priority dedup ---
    dedup = dedup_by_priority(raw)
    logger.info(
        "[HK Longs] within-day priority dedup: "
        + ", ".join(f"{n} {post_rs_counts[n]}→{len(dedup[n])}" for n in HK_STRATEGY_PRIORITY)
    )
```

with:

```python
    # --- Within-day cross-strategy priority dedup ---
    # RS is the conditional weak-market scan; the entire point is to
    # re-surface names already collected in other groups when HSI dumps.
    # So priority dedup is restricted to the 4 first-sighting strategies,
    # and RS is spliced back in untouched.
    dedup = dedup_by_priority(
        raw,
        priority=["EarningsGap", "HighVolume", "GapUp", "Leaders"],
    )
    dedup["RS"] = list(raw.get("RS", []))
    logger.info(
        "[HK Longs] within-day priority dedup: "
        + ", ".join(f"{n} {post_rs_counts[n]}→{len(dedup[n])}" for n in HK_STRATEGY_PRIORITY)
    )
```

- [ ] **Step 3: Run the HK unit tests**

Run: `uv run pytest tests/test_hk_eod.py -v`

Expected: all green. The Task-1 pinning test directly exercises this call shape.

- [ ] **Step 4: Static-check the imports/usages still resolve**

Run: `uv run python -c "import hk_eod; print(hk_eod.HK_STRATEGY_PRIORITY)"`

Expected: prints `['EarningsGap', 'HighVolume', 'GapUp', 'Leaders', 'RS']` — confirms the constant is still intact (the carve-out uses an inline list, not a constant change).

- [ ] **Step 5: Commit**

```bash
git add hk_eod.py
git commit -m "fix(hk_rs): exclude RS from within-day priority dedup

RS now bypasses Longs > Leaders > RS priority subtraction so a ticker
already collected in EarningsGap/HighVolume/GapUp/Leaders can still
appear in the weak-market RS scan on the same day."
```

---

## Task 3: HK RS — carve out of cross-day master dedup loop

**Files:**
- Modify: `hk_eod.py:893-902` (cross-day master loop)

- [ ] **Step 1: Read the current loop**

Run: `sed -n '893,905p' hk_eod.py`

Expected:
```python
    # --- Cross-day master dedup ---
    seen_path = eod_seen_path(output_dir, "HK")
    seen = load_seen(seen_path)
    final: dict[str, list[str]] = {}
    for name, codes_list in dedup.items():
        tag = f"[HK {name}]"
        # Convert to TV format for the seen file (matches write_watchlist input)
        tv = sorted(_to_tv(c) for c in codes_list)
        tv = dedup_seen(tag, tv, seen, seen_path)
        final[name] = tv
```

- [ ] **Step 2: Apply the carve-out edit**

Replace the loop body with:

```python
    # --- Cross-day master dedup ---
    # RS is excluded — see comment on within-day priority dedup above.
    # An RS hit must NOT consult or mutate the long-side master, because
    # the whole point of the conditional scan is to re-detect strong
    # names on weak-market days regardless of prior sightings.
    seen_path = eod_seen_path(output_dir, "HK")
    seen = load_seen(seen_path)
    final: dict[str, list[str]] = {}
    for name, codes_list in dedup.items():
        tv = sorted(_to_tv(c) for c in codes_list)
        if name == "RS":
            final[name] = tv
            continue
        tag = f"[HK {name}]"
        tv = dedup_seen(tag, tv, seen, seen_path)
        final[name] = tv
```

- [ ] **Step 3: Run all unit tests**

Run: `uv run pytest tests/ -v`

Expected: all green.

- [ ] **Step 4: Smoke-import to confirm no syntax error**

Run: `uv run python -c "from hk_eod import run_hk_eod; print('ok')"`

Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add hk_eod.py
git commit -m "fix(hk_rs): exclude RS from cross-day master dedup

RS now bypasses eod_seen_HK.txt and does not mutate the shared seen
set. Other HK long-side strategies (EarningsGap/HighVolume/GapUp/
Leaders) keep their existing master-dedup behavior."
```

---

## Task 4: US RS — carve out of within-day cross-group dedup

**Files:**
- Modify: `main.py:1626-1639` (cross-group dedup block + log line)

- [ ] **Step 1: Read the current block**

Run: `sed -n '1626,1640p' main.py`

Expected:
```python
        # --- Cross-group dedup: priority Longs > Leaders > RS ---
        # A ticker firing in multiple long-side groups is kept only in the
        # highest-priority one. Since each .txt and Futu group is rewritten
        # every run, this also prevents day-over-day cross-group duplication.
        before = (len(leaders_tickers), len(rs_tickers))
        leaders_tickers -= longs_tickers
        rs_tickers -= longs_tickers | leaders_tickers
        removed_le = before[0] - len(leaders_tickers)
        removed_rs = before[1] - len(rs_tickers)
        if removed_le or removed_rs:
            logger.info(
                f"[Dedup] Priority Longs > Leaders > RS: "
                f"removed {removed_le} from Leaders, {removed_rs} from RS"
            )
```

- [ ] **Step 2: Apply the edit**

Replace with:

```python
        # --- Cross-group dedup: priority Longs > Leaders ---
        # A ticker firing in both Longs and Leaders is kept only in Longs.
        # RS is intentionally NOT subtracted here — it's the conditional
        # weak-market scan, and re-surfacing a Longs/Leaders ticker that
        # still holds up on a market-down day is the entire signal.
        before_le = len(leaders_tickers)
        leaders_tickers -= longs_tickers
        removed_le = before_le - len(leaders_tickers)
        if removed_le:
            logger.info(
                f"[Dedup] Priority Longs > Leaders: "
                f"removed {removed_le} from Leaders"
            )
```

- [ ] **Step 3: Confirm imports + module load**

Run: `uv run python -c "import main; print('ok')"`

Expected: prints `ok`.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest tests/ -v`

Expected: all green. The US carve-out has no dedicated unit test (the dedup logic is inline in `main`, not extracted), but `test_smoke.py` and the report tests must still pass.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "fix(us_rs): exclude RS from within-day priority dedup

Within-day cross-group dedup now only enforces Longs > Leaders; RS is
left untouched so weak-market hits can re-surface tickers already in
Longs or Leaders on the same day."
```

---

## Task 5: US RS — carve out of cross-day master dedup

**Files:**
- Modify: `main.py:1665-1673` (RS write block)

- [ ] **Step 1: Read the current block**

Run: `sed -n '1665,1675p' main.py`

Expected:
```python
        # --- Write RS (only if it actually ran) ---
        if rs_ran:
            sorted_rs = sorted(rs_tickers)
            sorted_rs = _dedup_seen("[RS]", sorted_rs, us_seen, us_seen_path)
            dated = us_output_dir / f"{today}_RS.txt"
            write_watchlist(sorted_rs, dated, fmt)
            logger.info(f"[RS] Found {len(sorted_rs)} tickers -> {dated}")
            _write_webull(sorted_rs, dated, output_dir)
            _futu_sync(config, "rs", sorted_rs, "US")
```

- [ ] **Step 2: Apply the edit**

Replace with:

```python
        # --- Write RS (only if it actually ran) ---
        # RS bypasses cross-day master dedup (eod_seen_US.txt) — see the
        # within-day comment above. _dedup_seen is intentionally NOT called
        # here so an RS hit neither consults nor mutates us_seen.
        if rs_ran:
            sorted_rs = sorted(rs_tickers)
            dated = us_output_dir / f"{today}_RS.txt"
            write_watchlist(sorted_rs, dated, fmt)
            logger.info(f"[RS] Found {len(sorted_rs)} tickers -> {dated}")
            _write_webull(sorted_rs, dated, output_dir)
            _futu_sync(config, "rs", sorted_rs, "US")
```

- [ ] **Step 3: Confirm module loads**

Run: `uv run python -c "import main; print('ok')"`

Expected: prints `ok`.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest tests/ -v`

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "fix(us_rs): exclude RS from cross-day master dedup

US RS no longer consults or mutates eod_seen_US.txt. Longs/Leaders/IPO
cross-day master behavior is unchanged."
```

---

## Task 6: Docs — update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (two paragraphs — the "Cross-group dedup" bullet and the "Cross-day master dedup" paragraph)

- [ ] **Step 1: Locate the cross-group dedup bullet**

Run: `grep -n "Cross-group dedup\|Cross-day master dedup" CLAUDE.md`

Expected: two line numbers (one for each paragraph).

- [ ] **Step 2: Update the "Cross-group dedup" bullet**

Find the bullet starting with `**Cross-group dedup (Longs/Leaders/RS)**: Two layers.` and replace its body so it reflects the new behavior. The new text:

```
- **Cross-group dedup (Longs/Leaders)**: Two layers. (1) Within Longs, the 5 strategies are deduped by config-list order — earlier wins. (2) After Leaders has been collected, Longs union is deduped against Leaders with priority `Longs(union) > Leaders` so each ticker appears in exactly one of the 6 first-sighting long-side files (5 Longs splits + Leaders) per run. **RS is intentionally excluded from this priority dedup** — the conditional weak-market scan is meant to re-surface Longs/Leaders tickers that still hold up on a market-down day. The collection-then-write split means all Longs splits, Leaders, and RS files are written only after RS has finished. Shorts and HK Shorts are independent and written inline.
```

- [ ] **Step 3: Update the "Cross-day master dedup" paragraph**

Find the bullet starting with `**Cross-day master dedup** (`output/state/eod_seen_{US,HK,IPO}.txt`,` and update the "applied to" line and the "Excluded" line. New text:

```
- **Cross-day master dedup** (`output/state/eod_seen_{US,HK,IPO}.txt`, implemented in `_dedup_seen`): applied to first-sighting long-side EOD groups (5 US Longs splits + Leaders; 4 HK long-side groups: EarningsGap/HighVolume/GapUp/Leaders) AFTER within-day priority dedup. Each daily output = within-day survivors **minus** master; new survivors append to master. Net effect: every first-sighting long-side ticker enters exactly ONE of its market's groups on first sighting and never reappears in any of those `.txt` / Webull / Futu pushes. Reset by deleting the file (manual only). **Markets are independent**: `eod_seen_US.txt` and `eod_seen_HK.txt` never cross-contaminate.
```

And update the "Excluded" line to add US RS and HK RS:

```
  - **Excluded**: US Shorts, HK Shorts, **US RS, HK RS**, Morning Gap. Short setups are time-sensitive and meaningfully re-detectable. RS is conditional on weak-market days (SPY+QQQ ≤ −1.2% / HSI ≤ −1.2%); the whole point of the scan is to surface strong names regardless of whether they've been collected before, so it bypasses both within-day priority dedup and the cross-day master. An RS hit today does NOT suppress a future Longs/Leaders hit on the same ticker, and vice versa. Morning Gap uses its own per-day, per-phase seen files `output/state/morning_gap_seen_{pre,post}_<date>.txt`.
```

- [ ] **Step 4: Verify the file still parses (no broken markdown)**

Run: `grep -c "^##" CLAUDE.md`

Expected: same count as before (sanity check that no section header was accidentally deleted). If unsure, run `git diff CLAUDE.md` and eyeball the diff.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: clarify RS exclusion from within-day priority and cross-day master dedup"
```

---

## Task 7: Manual smoke test + push

The change cannot be fully exercised by unit tests (US dedup is inline in `main.py`, and the cross-day flow needs real state files). Verify by running the actual pipeline once and inspecting logs/outputs.

- [ ] **Step 1: Snapshot the current master files (so we can compare)**

Run:
```bash
wc -l output/state/eod_seen_US.txt output/state/eod_seen_HK.txt 2>/dev/null
```

Expected: line counts for each file (or "No such file" — fine either way). Note the numbers for comparison after the smoke run.

- [ ] **Step 2: Run the US EOD pipeline (ad-hoc)**

Run: `uv run main.py --mode us-eod 2>&1 | tee /tmp/rs-dedup-smoke-us.log`

Expected runtime: ~3–10 minutes (Finviz + yfinance + Futu). The exact ticker output is market-state dependent.

- [ ] **Step 3: Inspect the US log for the expected changes**

Run these greps against the log:

```bash
grep "Priority Longs" /tmp/rs-dedup-smoke-us.log
grep "\[RS\]" /tmp/rs-dedup-smoke-us.log
grep "cross-day dedup" /tmp/rs-dedup-smoke-us.log
```

Expected:
- `Priority Longs` line (if it appears) says `Priority Longs > Leaders` — **no longer** "Longs > Leaders > RS", and never says "removed N from RS"
- `[RS]` lines, if RS ran (depends on SPY/QQQ), do NOT show a `cross-day dedup: N already in master` line
- Other groups' `[Longs/<key>]` / `[Leaders]` lines still show their normal `cross-day dedup:` messages

If RS didn't run today (market wasn't down ≥1.2%), the `[RS]` log will say "Condition not met". That's fine — the negative path is unchanged.

- [ ] **Step 4: Confirm `eod_seen_US.txt` did NOT grow with RS tickers**

Run:
```bash
wc -l output/state/eod_seen_US.txt
```

The count may grow if Longs/Leaders had new hits. If RS ran, compare the new master against the RS dated output:

```bash
TODAY=$(date +%Y_%m_%d)
if [ -f "output/TV/US/${TODAY}_RS.txt" ]; then
    tr ',' '\n' < "output/TV/US/${TODAY}_RS.txt" | sort -u > /tmp/rs-out.txt
    comm -12 /tmp/rs-out.txt <(sort -u output/state/eod_seen_US.txt) > /tmp/rs-overlap.txt
    echo "RS tickers also in master: $(wc -l < /tmp/rs-overlap.txt)"
fi
```

Expected: RS tickers that ALSO landed in today's Longs/Leaders will appear in the master (because Longs/Leaders did add them). RS-only tickers should NOT be in the master. Spot-check: pick one RS-only ticker (in RS but not in today's Longs/Leaders files) and confirm it's NOT in `eod_seen_US.txt`. If it's missing from the master, the carve-out worked.

- [ ] **Step 5: (Optional) Run HK pipeline if outside the 20:00 HKT window**

If you want HK coverage too, run `uv run main.py --mode hk-eod 2>&1 | tee /tmp/rs-dedup-smoke-hk.log` and apply the analogous grep on `[HK RS]`. **Note:** outside the 20:00 HKT slot the run will use yesterday's close and skip the HSI-triggered RS group entirely, so the carve-out path may not exercise. That's the documented behavior; rely on the Task-1 unit test for HK correctness if the smoke window doesn't open.

- [ ] **Step 6: Push the branch**

```bash
git push
```

Expected: 5 commits pushed (Tasks 1, 2, 3, 4, 5, 6 — Task 7 has no commit of its own).

---

## Self-Review Checklist

- **Spec coverage:** Every section of the spec maps to a task:
  - US within-day → Task 4
  - US cross-day → Task 5
  - HK within-day → Task 2
  - HK cross-day → Task 3
  - Unit test → Task 1
  - CLAUDE.md update → Task 6
  - Manual verification → Task 7
- **Placeholder scan:** No TBDs, no "implement appropriately", no missing code blocks.
- **Type consistency:** `dedup_by_priority` signature unchanged. `_dedup_seen` signature unchanged. `dedup` dict shape preserved across HK code.
- **Risk:** Task 4 changes a public-ish log message format (`[Dedup] Priority Longs > Leaders > RS:` → `[Dedup] Priority Longs > Leaders:`). No downstream consumers grep for this string (verified: only `main.py` itself writes it), so the change is safe.

## Out of Scope

- No changes to `[rs]` / `[hk_rs]` Futu groups or `append_only_groups`.
- No changes to RS percentile filter (`min_rs_percentile_longs`) or trigger thresholds (those were adjusted in commit `99c37b0`, separate change).
- No changes to IPO / Shorts / Morning Gap behavior.
- No master-file migration. Existing `eod_seen_US.txt` / `eod_seen_HK.txt` contents are preserved as-is; the change is purely "RS stops consulting/updating them going forward".
