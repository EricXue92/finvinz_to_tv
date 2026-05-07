# Daily CANSLIM Report — Design Spec

**Status:** Draft
**Date:** 2026-05-07
**Scope:** After every long-side EOD run, analyze the day's newly-detected tickers (US + HK long-side groups + IPO sidecars) with Claude Opus 4.7 and email a CANSLIM-style fundamentals + outlook report to the user.

## Goal

Turn the daily `.txt` watchlist hits into a same-day research brief. For each ticker that crosses the cross-day master dedup gate (i.e., genuinely new for that ticker in that market's long-side universe), produce a structured CANSLIM-flavored writeup covering snapshot fundamentals, recent earnings, competitive position, government/policy support, new products & catalysts, key risks, and a bottom-line take. Bundle all writeups into a single Markdown file per market per day, and email it to the user. The `.txt` files remain the primary artifact; the report is a **soft-fail side effect** (mirrors the existing Futu sync contract).

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

### Markdown file

Path: `output/Reports/<date>_{us,hk}.md`

Top-level structure:

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

### Email delivery (Gmail SMTP)

**Why SMTP, not Gmail MCP:** the Gmail MCP tool (`mcp__claude_ai_Gmail__create_draft`) is only callable inside a Claude Code session. `main.py --mode report` is a launchd-driven Python script with no MCP runtime, so it must call Gmail directly. Standard path: `smtplib.SMTP_SSL("smtp.gmail.com", 465)` with a Gmail **App Password** (not the account password — Google blocks plain-password SMTP since 2022).

**Setup (one-time, by user):**
1. Enable 2-Step Verification on the Google account (xuelong0208@gmail.com)
2. Generate an App Password at https://myaccount.google.com/apppasswords (16 chars, no spaces)
3. Add to `scripts/run_eod.sh` and `scripts/run_hk_eod.sh`:
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   export SMTP_PASSWORD="xxxxxxxxxxxxxxxx"
   ```

**Email contents:**
- From / To: `xuelong0208@gmail.com` (sending to self)
- Subject: `[Scan Report] YYYY-MM-DD US — M analyzed` (or `HK`); where M is the post-cap analyzed count
- HTML body: rendered from the Markdown file via `markdown` Python package (added as a `uv` dependency); GitHub-flavored extension for tables and fenced blocks
- Plain-text body: the raw Markdown (multipart/alternative; clients without HTML get the .md)
- No attachment — the Markdown file lives on disk; if the user wants it elsewhere they grab it from `output/Reports/`

**Failure mode:** SMTP errors logged as warnings, do not raise. The .md file is already on disk; missing email is a soft failure.

## Module Layout

```
main.py                      # CLI: add --mode report --market {us,hk} branch
report/
  __init__.py
  __main__.py                # entry point invoked by main.py mode dispatch
  ranker.py                  # read dated .txt → priority sort → cap 50 → list[(ticker, group)]
  enrich.py                  # yfinance fetch + RS table lookup → dict per ticker
  analyst.py                 # async Anthropic + web_search + retry → markdown section per ticker
  renderer.py                # list of sections → full markdown document
  mailer.py                  # markdown → HTML (markdown pkg) → smtplib send
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

- `anthropic[web_search]` — official Anthropic SDK with web_search tool support (already may be aliased)
- `markdown` — Markdown → HTML for the email body

(yfinance, smtplib are already available — smtplib is stdlib.)

## CLAUDE.md Updates

After implementation, append a new section under "Architecture" describing:
- The 14th group of artifacts: per-day Markdown reports under `output/Reports/`
- The `--mode report` flag and its wrapper-script integration
- The `ANTHROPIC_API_KEY` and `SMTP_PASSWORD` env var contract
- The "report is soft-fail like Futu sync" guarantee
- The 50-ticker per-market cap and its priority order

## Configuration

No new `config.toml` block. The report mode is fully env-var-driven (keys + flag). Rationale: the report is a peripheral feature and adding TOML knobs invites scope creep (tunable cap, tunable model, tunable priority). If we need a knob later, add it then.

The 50-ticker cap and priority order are hardcoded in `report/ranker.py` as module-level constants — easy to grep, change requires a code edit + commit (intentional friction so it doesn't drift).

## Testing

Pure-logic units (no network):
- `report.ranker.rank_and_cap()` — given a `dict[group_name, list[ticker]]`, returns the correct prioritized truncated list. Test with empty inputs, single-group, cross-group dedup, over-cap truncation.
- `report.renderer.render_document()` — given mock section markdowns, produces the full document with header/separators/truncation block.

Network-dependent units mocked at the boundary:
- `report.enrich.fetch_ticker_data()` — patch `yfinance.Ticker` with a fixture, assert dict shape and YoY computation correctness (especially the FY-3/FY-2/FY-1 math).
- `report.analyst.analyze_ticker()` — patch `anthropic.AsyncAnthropic`, verify retry logic, missing-key short-circuit, timeout handling.

End-to-end: `uv run main.py --mode report --market us --date 2026-05-07 --dry-run` reads existing dated .txt files, runs the full pipeline EXCEPT the SMTP send (writes the .md but skips email). Useful for verifying a report after-the-fact.

## Operational Notes

- The report runs **after** the EOD pipeline finishes and after Futu sync. Total wall clock per market: 2–4 minutes (5-way concurrency over ~20–40 tickers, each ~30–60s with 2 web_search calls).
- US EOD slot at 10:00 HKT → report likely emails by 10:08 HKT.
- HK EOD slot at 20:00 HKT → report likely emails by 20:08 HKT.
- The Markdown file is the source of truth; if email is lost (spam filter, SMTP quota), `output/Reports/<date>_{us,hk}.md` is always there.
- Gmail's daily SMTP send limit for personal accounts is 500/day. We send 2/day. Non-issue.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Anthropic API outage during EOD slot | Soft-fail: warning logged, .txt artifacts unaffected, no email that day |
| yfinance schema drift breaks enrichment | Per-ticker `try/except` in `enrich.py`; missing fields → `null`, LLM handles gracefully |
| Opus 4.7 model rename / deprecation | Constant in `analyst.py`; one-line change |
| Cost spike from runaway web_search use | `max_uses=3` per call enforced by Anthropic API |
| HK ticker coverage gaps in yfinance | Already a known limitation; the dict will have nulls; LLM is told to focus on what's available |
| App Password leaked via shell history | Stored only in launchd wrapper scripts which are git-ignored; documented in CLAUDE.md not to commit |
| Email goes to spam | First-send: user adds self-from-self filter to never spam; not a code issue |

## Migration / Rollout

1. Implement modules + tests on a feature branch.
2. Manual end-to-end test: `--dry-run` against a recent day's `.txt` files.
3. Verify `.md` content quality on a known-fresh day.
4. Add `SMTP_PASSWORD` to wrapper scripts; trigger a real email send on a sample.
5. Merge; next EOD slot starts mailing.
6. Watch first 3 days' reports; tune system prompt if a section consistently underdelivers.
