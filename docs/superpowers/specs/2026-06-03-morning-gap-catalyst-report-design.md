# Morning-Gap Catalyst Report — Design

**Status:** Approved
**Date:** 2026-06-03
**Scope:** US pre-market only (`morning-gap` mode, `offset < 0`)

## Problem

The pre-market morning-gap scan surfaces gap-up tickers via ntfy ("AAPL, NVDA,
TWLO …") but gives no signal *why* each one is gapping. The trader has to
hand-search every name in the 5–10 minutes before market open to decide
whether to enter. The existing EOD report is the wrong tool — it's a CANSLIM
fundamentals brief tuned for daily review, not a fast catalyst hunt for
intraday positioning.

We want a **catalyst report** that, for each freshly surfaced pre-market
gapper, names the likely catalyst (earnings, analyst upgrade, partnership,
M&A, FDA, policy, retail flow, etc.), cites English-source evidence, and
delivers a link via ntfy before the open.

## Non-goals

- Post-open (`offset > 0`) morning-gap windows. EOD will cover these names a
  few hours later.
- HK pre-market. Futu does not expose HK pre-auction fields.
- Replacing the existing "ticker list" ntfy push. The catalyst report
  *augments* that flow; it does not replace it.
- Fundamentals tables (5y annual YoY, 4q trajectory). That is EOD's job.

## Trigger

`main.py --mode morning-gap` already runs at -20 and -10 minutes ET. After
`notify_morning_gap(fresh, …)`, insert a hook:

1. Guarded by `is_pre and fresh and cfg["morning_gap_catalyst"]["enabled"]`.
2. Serialize a snapshot sidecar (per-ticker `gap_pct`, `last_price`,
   `market_cap`, `first_seen_offset`) to `tempfile.NamedTemporaryFile(
   delete=False)` — the snapshot is already in memory from Futu discovery,
   so we avoid a second snapshot call.
3. Spawn a **detached subprocess**: `nohup uv run python -m report.morning
   --snapshot <tmpfile> --date <today> --offset <-10|-20> &`. The
   subprocess is responsible for `unlink`ing the sidecar when done.
4. Return immediately from the morning-gap path — launchd's next wake
   (e.g. `-20` → `-10` ten minutes later) must not be blocked by report
   generation.

## Dedup

Reuse `_morning_gap_classify` from `main.py`. Only the `fresh` set
(tickers newly added in this scan, relative to the day's
`morning_gap_seen_pre_<date>.txt`) gets sent into the catalyst pipeline.
The `promoted` tuple value returned by the same call is always empty in
the pre-market branch (promotion is pre→post, not -20→-10) — ignore it
for catalyst-report routing.

Consequence: the -10 scan analyzes only tickers that were NOT in the -20
scan. No duplicate DeepSeek/Tavily spend.

## File output

`output/Reports/<date>_us_premarket.md` (and `.html`) — **one file per
trading day**, appended across the -20 and -10 scans. The -20 scan
creates the file; the -10 scan opens the same file in append mode and
writes a `## 增补 (-10min, HH:MM ET)` divider followed by the new
tickers' blocks.

Append is **byte-level append, no parsing of prior content**. The HTML
is regenerated from the full `.md` after each write. If the file is
missing when -10 runs (e.g. -20 had zero fresh and skipped, or -20
crashed), -10 writes a fresh header and starts the report itself.

Path naming intentionally mirrors EOD's `<date>_us.md` so both reports
sort together in `output/Reports/`.

## Report structure (per ticker)

```
## NVDA — NVIDIA Corp.  (Pre-market +12.4% · -20min)

| Gap | Price | Mkt Cap | First seen |
|---|---|---|---|
| +12.4% | $138.20 | $3.4T | 09:10 ET (-20m) |

### 主催化剂

（1–2 句中文摘要：最可能的 gap-up 触发事件。）

### 证据

- [WSJ: <headline>](url) — 1 句话摘要
- [Reuters: <headline>](url) — 1 句话摘要
（2–3 条最权威英文源。）

### 分类

**财报** · **评级**
（标签集合见下文。多标签以 ` · ` 分隔，未识别时只标"其他"。）

### 提示

（1 句话：观察点 / 催化剂强度 / 财报后第几个交易日 / 是否已经反映在盘前。）
```

**Tag set (closed):** `财报` · `评级` · `合作` · `收购` · `FDA` · `政策` ·
`股东` · `散户驱动` · `其他`. The system prompt enumerates these and
forbids ad-hoc labels — keeps downstream filtering / sorting cheap.

## Backend & search

- **Hardcoded** to DeepSeek (does NOT read `[report] backend`).
  Rationale: EOD users may flip to Anthropic for quality, but the
  pre-market report is explicitly a DeepSeek + Tavily product by user
  request. Independent config lets EOD and pre-market evolve separately.
- Reuse `report.llm.DeepSeekBackend` and `report.search.TavilyClient`
  unchanged.
- `max_search_calls = 3` per ticker (EOD is 2). Catalyst identification
  is more time-sensitive and benefits from one extra search query for
  cross-checking.
- Concurrency = 3 (same sweet spot EOD landed on).

## System prompt (`prompts/morning_gap_catalyst_system.md`)

Key constraints, distinct from `canslim_system.md`:

1. Response starts with `### 主催化剂` and contains nothing else — no
   preamble, no `## <ticker>` header (the renderer emits that), no
   snapshot table (renderer emits that).
2. **English sources only.** Acceptable: Bloomberg, Reuters, WSJ, FT,
   CNBC, Barron's, Yahoo Finance news, company IR / SEC 8-K. Chinese
   financial portals forbidden for US tickers (same rule as EOD prompt
   rule 8).
3. Suggested search query templates (the model picks 2–3):
   - `<ticker> news today`
   - `<ticker> pre-market <YYYY-MM-DD>`
   - `<ticker> earnings beat OR miss`
   - `<ticker> analyst upgrade OR downgrade`
   - `<ticker> partnership OR acquisition`
4. **No fabrication rule.** If 3 searches surface no clear catalyst,
   classify as `其他` and write `主催化剂` as `信息不足 — 可能为板块联动 /
   技术性突破 / 散户情绪`. Do NOT invent earnings dates or analyst names.
5. Qualitative prose in Simplified Chinese; numbers, headlines, source
   names stay in English.

## Module layout

```
report/
  morning.py             ← new entrypoint  (python -m report.morning)
  morning_renderer.py    ← new renderer    (snapshot table + 4 sections)
  llm.py                 ← unchanged       (reuse DeepSeekBackend)
  search.py              ← unchanged       (reuse TavilyClient)
  state.py               ← unchanged       (load_dotenv, paths)
prompts/
  morning_gap_catalyst_system.md   ← new
config.toml
  [morning_gap_catalyst]           ← new section
```

`report/morning.py` contract:
- CLI: `--snapshot <path> --date <YYYY-MM-DD> --offset <int>`
- Snapshot JSON shape: `{ticker: {gap_pct, last_price, market_cap,
  company_name, first_seen_offset_minutes}}`
- Fan-out: `asyncio.gather` over tickers via `Semaphore(3)`
- Output: append to `output/Reports/<date>_us_premarket.md`, regenerate
  `.html` from the full `.md`
- Cleanup: `os.unlink(snapshot_path)` in a `finally`
- ntfy push on success (separate from `notify_morning_gap` — new
  function `notify_morning_catalyst_ready` in `notify.py`)
- Soft-fail at the top level: any unhandled exception logged, exit 0
  (the morning-gap subprocess fanout must not pollute systemwide error
  exit codes)

## Notification (ntfy)

Two pushes per scan, same topic, different titles:

1. **Existing** `notify_morning_gap` — fires immediately when fresh
   tickers found. Title: `Morning Gap (-20m)`. Body: ticker list.
   Unchanged.
2. **New** `notify_morning_catalyst_ready` — fires from inside
   `report/morning.py` after the .md is written. Title:
   `Catalyst Report Ready (-20m, 5 tickers)`. Body: short link or file
   path. Click action navigates to the .md.

## Configuration block

```toml
[morning_gap_catalyst]
enabled = true
max_tickers_per_run = 10              # cap; pre-market typically 3-7
concurrency = 3
max_search_calls = 3
deepseek_model = "deepseek-v4-pro"
ntfy_topic = "xue-finviz-morning-gap-9f3k2"   # same topic as morning-gap
```

**Cap overflow behavior:** when `len(fresh) > max_tickers_per_run`, the
subprocess analyzes the top N by `gap_pct` desc (largest gaps first)
and writes a `> 截断: <skipped count> 票按 gap%排序后被丢弃` note in the
report header. The skipped tickers are still pushed in the immediate
ntfy ticker-list — they just don't get a catalyst analysis. Existing
EOD `MAX_TICKERS_PER_REPORT = 30` is a separate constant; the morning
cap is lower because pre-market scans are tighter and DeepSeek latency
matters more.

Env vars (read in `report.llm.build_backend`): `DEEPSEEK_API_KEY`,
`TAVILY_API_KEY`. Missing → log warning, the subprocess exits 0 without
writing.

## Failure modes

| Failure | Behavior |
|---|---|
| DeepSeek API key missing | Subprocess logs warning, exits 0. Main morning-gap unaffected. |
| Tavily API key missing | Same as above. |
| Tavily query failure (per call) | DeepSeekBackend's existing "force final no-tool turn" path emits text without that search context. Section may say `信息不足`. |
| DeepSeek 5xx / timeout | Existing `analyst.analyze_ticker` retry policy applies (kept by reusing the backend; we do NOT reuse `analyst.analyze_ticker` directly because its placeholder format is CANSLIM-shaped — see below). |
| Subprocess spawn failure | `main.py` catches, logs warning, morning-gap continues normally. |
| `.md` write failure | Logged. ntfy push skipped. |
| -20 file missing when -10 runs | -10 subprocess detects absence, creates the file fresh (treat all -10 fresh tickers as the report's first section). |

## Per-ticker retry / placeholder

Rather than reusing `analyst.analyze_ticker` (its placeholder strings —
`[配置错误]` / `[分析失败]` — match the CANSLIM `## ticker` header
layout), `report/morning.py` ships a thin local equivalent with the same
retry shape but emits catalyst-report-shaped placeholders:

```
### 主催化剂

[分析失败: <exception type>]

### 证据

—

### 分类

**其他**

### 提示

—
```

Renderer still gets a valid 4-section block, so the .md never has gaps.

## Renderer (`report/morning_renderer.py`)

- Header: `# Pre-market Catalyst Report — <date> (US)`
- Per-ticker block: snapshot table + LLM-emitted 4 sections
- Footer: model label (`deepseek-v4-pro (DeepSeek)`), generated-at HKT,
  Tavily search count (sum across tickers, for spend monitoring)
- Append path: if `<date>_us_premarket.md` exists, parse the trailing
  `## ` blocks, append after a `## 增补 (-10min, HH:MM ET)` divider.
- HTML: regenerate from full .md each run (mirror EOD's approach — no
  partial HTML append).

## Cleanup

`cleanup_old_outputs` is glob-driven and already covers `output/Reports/`
via the existing `*.md` / `*.html` patterns — no change needed. Premarket
reports age out with EOD reports.

## Testing

- Unit: `report/morning.py` argument parsing; snapshot JSON round-trip;
  append-vs-create file logic.
- Unit: renderer fixture comparing rendered .md against a golden file
  with 2 tickers.
- Integration (manual, gated by env vars): run
  `python -m report.morning --snapshot <fixture.json> --date 2026-06-03
  --offset -20` against real DeepSeek + Tavily, eyeball the .md.

## Open invariants (don't break)

- The morning-gap path's two ntfy pushes (existing + new) must stay
  independent — the catalyst report MUST NOT delay the ticker-list
  push.
- The catalyst report subprocess MUST NOT call Futu / yfinance. All
  data it needs is in the snapshot sidecar.
- `[morning_gap_catalyst] enabled = false` MUST cleanly disable the
  feature (no subprocess spawn, no behavior change vs. today).
- DeepSeek's catalyst prompt is **fixed-tag**. Adding a new tag (e.g.
  `回购`) requires updating both the prompt and any downstream consumer
  that filters by tag.
