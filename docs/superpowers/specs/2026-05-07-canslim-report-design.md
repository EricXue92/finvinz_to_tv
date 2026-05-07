# Daily CANSLIM Report — Design Spec

**Status:** Draft
**Date:** 2026-05-07
**Scope:** After every long-side EOD run, analyze the day's newly-detected tickers (US + HK long-side groups + IPO sidecars) with Claude Opus 4.7 and write a CANSLIM-style fundamentals + outlook report to disk in both Markdown and standalone HTML.

## Goal

Turn the daily `.txt` watchlist hits into a same-day research brief. For each ticker that crosses the cross-day master dedup gate (i.e., genuinely new for that ticker in that market's long-side universe), produce a structured CANSLIM-flavored writeup covering snapshot fundamentals, recent earnings, competitive position, government/policy support, new products & catalysts, key risks, and a bottom-line take. Bundle all writeups into one Markdown file and one self-contained HTML file per market per day, both saved under `output/Reports/`. The `.txt` files remain the primary EOD artifact; the report is a **soft-fail side effect** (mirrors the existing Futu sync contract). No email, no network delivery — the user opens the local files.

## In Scope

| Source | Files read |
|---|---|
| US long-side | `output/TV/US/<date>_{EarningsGap,HighVolume,GapUp,NewHigh52W,TopGainers,Leaders,RS,IPO}.txt` |
| HK long-side | `output/TV/HK/<date>_{EarningsGap,HighVolume,GapUp,Leaders,RS,IPO}.txt` |

US and HK reports run independently (different `--market` arg), each capped at **50 tickers/day**.

## Out of Scope

- **US Shorts, HK Shorts, Morning Gap** — short setups are technical/parabolic-blow-off plays where fundamentals don't drive entries; Morning Gap is a real-time signal where a multi-minute LLM analysis would arrive too late.
- **Historical report search / cross-day comparison** — files archive themselves under `output/Reports/`; grep is sufficient when needed.
- **Cross-day report dedup** — if a ticker re-enters a different group later (e.g., IPO → Leaders weeks later), it gets a new report; the angle changes and the marginal cost is small.
- **Charting / image attachments** — text only. TradingView is the chart venue.

## Trigger & Integration

- New CLI mode: `main.py --mode report --market {us,hk}`
- `scripts/run_eod.sh` appends one call to `--mode report --market us` after `--mode us-eod` succeeds
- `scripts/run_hk_eod.sh` appends one call to `--mode report --market hk` after `--mode hk-eod` succeeds
- Wrappers wrap the report call in a `set +e` block — a non-zero exit code from the report step does NOT fail the wrapper. The `.txt` and Futu sync are already done by that point; the report failing must not turn a successful EOD run into a launchd-flagged failure.
- The report mode itself never raises out of `main()` — top-level `try/except Exception` logs and exits 0 to belt-and-suspenders the wrapper.

## Data Sources

### Per-ticker enrichment (local, before any LLM call)

For each ticker, build a structured `dict` to inject into the LLM user prompt:

| Field | Source |
|---|---|
| ticker, exchange, group, company_name | `.txt` filename + group inference + yfinance `info.longName` |
| market_cap, last_price, prev_close, gap_pct | yfinance `info` (US) / existing HK metrics frame already in memory (HK) |
| pe_ratio, roe, institutional_holdings_pct | yfinance `info` |
| eps_latest_q, eps_latest_q_yoy_pct | yfinance `Ticker.quarterly_income_stmt` (`DilutedEPS` / `BasicEPS` row) |
| revenue_latest_q, revenue_latest_q_yoy_pct | yfinance `Ticker.quarterly_income_stmt` (`TotalRevenue` row) |
| **annual_eps_yoy_3y** (FY-3, FY-2, FY-1) | yfinance `Ticker.income_stmt` annual — compute YoY across 4 fiscal years |
| **annual_revenue_yoy_3y** (FY-3, FY-2, FY-1) | yfinance `Ticker.income_stmt` annual — same shape |
| latest_earnings_date | yfinance `info.lastFiscalYearEnd` / `Ticker.calendar` |
| rs_percentile | from US `rs_rating.py` table or HK `hk_rs.py` table (already in memory during EOD; for `--mode report` re-load the cached `output/state/{rs,hk_rs}_rating_<date>.csv`) |

Missing fields → write `null` in the dict; the LLM is instructed to handle nulls gracefully ("data not available — focus on what's present").

### LLM-side: Anthropic web_search

The LLM gets the structured dict above plus the `web_search_20250305` tool with `max_uses=3`. It uses the searches for the **qualitative** legs of CANSLIM that aren't in yfinance: competitive moat, government/policy support, new products, recent catalysts, risk landscape. The searches are model-driven — we don't pre-compute queries.

## LLM Configuration

- **Model:** `claude-opus-4-7`
- **Max tokens:** 1500 (output) — enough for the structured Markdown section without over-budgeting
- **System prompt:** static CANSLIM framework + output format spec, loaded from `prompts/canslim_system.md`. Marked with `cache_control: {type: "ephemeral"}` so subsequent calls within the 5-minute TTL reuse it (~70% input cost saving on the system block).
- **Tools:** `[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]`
- **Concurrency:** `asyncio.Semaphore(5)` over `anthropic.AsyncAnthropic`
- **Retry:** on `anthropic.APIStatusError` (5xx), `anthropic.APIConnectionError`, `asyncio.TimeoutError` — 1 retry with 5s backoff. On second failure, write `[分析失败: <reason>]` placeholder section and continue.
- **API key:** read from `ANTHROPIC_API_KEY` env var. Missing → log warning, skip the entire report step (wrapper's `.txt` artifacts unaffected).
- **Per-call timeout:** 90 seconds (web_search adds latency; default is enough).

### Cost envelope

Opus 4.7 pricing: $15/M input, $75/M output, web_search $10/1k searches.

- Input: ~3k tokens/ticker (system cached → ~1k effective billable on hits)
- Output: ~800 tokens/ticker
- Web search: ~2 searches/ticker average

Per ticker ≈ **$0.05 input + $0.06 output + $0.02 search ≈ $0.13–0.25** depending on cache hit rate and search count.

Daily cap: 50 × 2 markets × $0.25 = **$25/day max**. Typical: $10–15/day.

## Ranking & 50-Ticker Cap

Per market, walk the input files in this **priority order** (user-specified):

```
EarningsGap > HighVolume > Leaders > GapUp > NewHigh52W > IPO > TopGainers > RS
```

(US: 8 groups. HK: 6 groups — no NewHigh52W, no TopGainers.)

Build an ordered list of `(ticker, group)` pairs. Within each group, preserve the file's existing line order (which itself reflects per-strategy ranking). De-duplicate across groups: a ticker appearing in two groups (rare — within-day priority dedup already runs in EOD) lands under the higher-priority group only.

Take the first 50. The remaining tickers (if any) are recorded in a "Truncated" section at the end of the report — `TICKER (Group)` lines, no analysis.

## Output

Two files per market per day, both written to `output/Reports/`:

| Path | Purpose |
|---|---|
| `output/Reports/<date>_{us,hk}.md` | Source-of-truth Markdown. Easy to grep, diff, and re-render. |
| `output/Reports/<date>_{us,hk}.html` | Self-contained standalone HTML — single file, inline CSS, no external assets. Double-click to open in any browser. |

The HTML file is rendered from the Markdown via the `markdown` Python package (extensions: `tables`, `fenced_code`, `sane_lists`) and wrapped in a minimal HTML5 template with inline CSS (system font, max-width 900px, dark-text-on-white, slightly larger code/snapshot blocks). The template lives in `report/renderer.py` as a Python string constant — no Jinja, no external CSS file, intentionally trivial.

### Markdown structure

```markdown
# Scan Report — YYYY-MM-DD ({US,HK})

**Total new tickers:** N (analyzed M, truncated K)
**Generated:** YYYY-MM-DD HH:MM:SS HKT

---

## $TICKER — Company Name (Exchange · Group)

**Snapshot**
- Market Cap: $X.XB | Price: $XX.XX | RS: 95
- EPS (latest Q): $X.XX (YoY +XX%) | Revenue (latest Q): $X.XB (YoY +XX%)
- Annual EPS YoY: FY-3 +XX% / FY-2 +XX% / FY-1 +XX%
- Annual Revenue YoY: FY-3 +XX% / FY-2 +XX% / FY-1 +XX%
- PE: XX | ROE: XX% | Inst. Hold: XX%
- Latest earnings date: YYYY-MM-DD

**公司速览**: 1-2 sentence business description (Chinese)

**基本面 / 财报**: trend across the 3 annual YoY datapoints + most recent quarter — accelerating? decelerating? (Chinese)

**竞争力**: moat, industry position, key competitors (Chinese)

**政策 / 政府支持**: subsidies, regulatory tailwinds/headwinds — especially for HK / China-exposed names (Chinese)

**新产品 / 催化剂**: recent or upcoming launches, contracts, partnerships (Chinese)

**风险点**: 2-3 highest-conviction risks (Chinese)

**综合判断**: 1-2 sentence CANSLIM-flavored long/short bias (Chinese)

---

(...repeat per ticker...)

## Truncated (cap = 50)

- TICKER1 (Group)
- TICKER2 (Group)
...
```

The system prompt enforces this template strictly so all sections present even when data is partial (LLM writes "信息不足" for empty quals rather than omitting headers).

### HTML rendering

The `.html` file is generated from the same in-memory Markdown string via:

```python
import markdown
html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists"])
```

…then wrapped in a static HTML5 shell:

```html
<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>Scan Report — {date} {market}</title>
  <style>{INLINE_CSS}</style>
</head>
<body>{html_body}</body>
</html>
```

`INLINE_CSS` is a ~30-line constant: system font stack, max-width 900px centered, slightly muted background for `<code>`/`<pre>`, table borders, headings spaced. No JavaScript, no external requests — the file is self-contained and can be archived or shared as one file.

## Module Layout

```
main.py                      # CLI: add --mode report --market {us,hk} branch
report/
  __init__.py
  __main__.py                # entry point invoked by main.py mode dispatch
  ranker.py                  # read dated .txt → priority sort → cap 50 → list[(ticker, group)]
  enrich.py                  # yfinance fetch + RS table lookup → dict per ticker
  analyst.py                 # async Anthropic + web_search + retry → markdown section per ticker
  renderer.py                # sections → full markdown doc → standalone HTML doc (writes both files)
  state.py                   # paths, env-var reading, soft-fail helpers
prompts/
  canslim_system.md          # static system prompt (cached)
scripts/
  run_eod.sh                 # append: uv run main.py --mode report --market us || true
  run_hk_eod.sh              # append: uv run main.py --mode report --market hk || true
docs/superpowers/specs/
  2026-05-07-canslim-report-design.md   # this file
```

### Dependencies (added to `pyproject.toml`)

- `anthropic` — official Anthropic Python SDK (web_search is built-in to the API; no extras needed at the package level)
- `markdown` — Markdown → HTML conversion for the `.html` output

(yfinance is already a dependency.)

## CLAUDE.md Updates

After implementation, append a new section under "Architecture" describing:
- The new artifact pair: per-day Markdown + standalone HTML reports under `output/Reports/`
- The `--mode report` flag and its wrapper-script integration
- The `ANTHROPIC_API_KEY` env var contract (only env var needed)
- The "report is soft-fail like Futu sync" guarantee
- The 50-ticker per-market cap and its priority order

## Configuration

No new `config.toml` block. The report mode is fully env-var-driven (keys + flag). Rationale: the report is a peripheral feature and adding TOML knobs invites scope creep (tunable cap, tunable model, tunable priority). If we need a knob later, add it then.

The 50-ticker cap and priority order are hardcoded in `report/ranker.py` as module-level constants — easy to grep, change requires a code edit + commit (intentional friction so it doesn't drift).

## Testing

Pure-logic units (no network):
- `report.ranker.rank_and_cap()` — given a `dict[group_name, list[ticker]]`, returns the correct prioritized truncated list. Test with empty inputs, single-group, cross-group dedup, over-cap truncation.
- `report.renderer.render_markdown()` — given mock section markdowns, produces the full document with header/separators/truncation block.
- `report.renderer.markdown_to_html()` — given a markdown string, produces a self-contained HTML document containing the expected `<style>` block and `<body>` content (no external links).

Network-dependent units mocked at the boundary:
- `report.enrich.fetch_ticker_data()` — patch `yfinance.Ticker` with a fixture, assert dict shape and YoY computation correctness (especially the FY-3/FY-2/FY-1 math).
- `report.analyst.analyze_ticker()` — patch `anthropic.AsyncAnthropic`, verify retry logic, missing-key short-circuit, timeout handling.

End-to-end: `uv run main.py --mode report --market us --date 2026-05-07` re-reads the dated `.txt` files for the given date and writes both `.md` and `.html`. Useful for back-filling a missed day or iterating on the system prompt.

## Operational Notes

- The report runs **after** the EOD pipeline finishes and after Futu sync. Total wall clock per market: 2–4 minutes (5-way concurrency over ~20–40 tickers, each ~30–60s with 2 web_search calls).
- US EOD slot at 10:00 HKT → both files on disk by ~10:08 HKT.
- HK EOD slot at 20:00 HKT → both files on disk by ~20:08 HKT.
- The user opens `output/Reports/<date>_{us,hk}.html` directly in a browser, or reads the `.md` in a text editor. Sharing a single day's report = send one self-contained `.html` file.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Anthropic API outage during EOD slot | Soft-fail: warning logged, `.txt` artifacts unaffected, no report files that day |
| yfinance schema drift breaks enrichment | Per-ticker `try/except` in `enrich.py`; missing fields → `null`, LLM handles gracefully |
| Opus 4.7 model rename / deprecation | Constant in `analyst.py`; one-line change |
| Cost spike from runaway web_search use | `max_uses=3` per call enforced by Anthropic API |
| HK ticker coverage gaps in yfinance | Already a known limitation; the dict will have nulls; LLM is told to focus on what's available |
| `markdown` package output diverges from expected HTML | Renderer test compares against snapshot; failures are loud at unit-test time, not runtime |

## Migration / Rollout

1. Implement modules + tests on a feature branch.
2. Manual end-to-end test: `uv run main.py --mode report --market us --date <recent>` against a recent day's `.txt` files; visually inspect both the `.md` and the `.html`.
3. Verify content quality on a known-fresh day across a handful of tickers.
4. Add `ANTHROPIC_API_KEY` to wrapper scripts.
5. Merge; next EOD slot starts producing reports.
6. Watch first 3 days' reports; tune the system prompt if a section consistently underdelivers.
