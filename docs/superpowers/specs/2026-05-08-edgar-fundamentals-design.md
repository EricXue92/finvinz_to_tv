# SEC EDGAR US Fundamentals — Design

**Date:** 2026-05-08
**Scope:** US daily report only (`--mode report --market us`). HK report unchanged.

## Goal

Replace yfinance as the source of US-report fundamental history with SEC EDGAR's XBRL `companyfacts` API. EDGAR is authoritative (the same numbers companies file with the SEC) and offers 10+ years of history, removing yfinance's free-tier ceiling that forced the recent shrink from 5y/4q to 3y/2q (commit `7e67b1d`).

## Non-goals

- HK report (`hk_eod.py`, HK enrichment path) is out of scope. yfinance remains the HK fundamentals source.
- US Shorts / HK Shorts / Morning Gap (no fundamentals analysis today, none planned).
- Snapshot / metadata fields stay on yfinance (see "Field ownership" below).

## Decisions (from brainstorming)

1. **Hybrid split.** EDGAR owns financial-statement history; yfinance keeps live snapshot fields (price, market cap, sector, industry, institutional %, latest earnings date).
2. **History depth.** 5 annual YoY datapoints (= 6 fiscal years) + 4 quarterly YoY datapoints (= 8 quarters of underlying data, since each YoY pairs with the same calendar quarter one year prior).
3. **Triple fallback.** EDGAR → yfinance income statement → None. Both layers preserved; on EDGAR miss the existing yfinance code path runs unchanged.
4. **Cache.** Local file cache under `output/state/edgar_cache/` with simple mtime-based TTL (7 days for `companyfacts`, 1 day for the ticker→CIK map).
5. **No config toggle.** Always-on. The yfinance fallback already provides a kill-switch for EDGAR-side failures; an extra `enabled` flag would be redundant.

## Field ownership after migration

| Field | Source after migration | Notes |
|---|---|---|
| `company_name`, `sector`, `industry` | yfinance (unchanged) | EDGAR has SIC code only, not Yahoo's curated names |
| `market_cap`, `last_price`, `prev_close`, `gap_pct` | yfinance (unchanged) | EDGAR has no real-time price |
| `institutional_holdings_pct` | yfinance (unchanged) | EDGAR 13F data is a separate, harder pipeline |
| `latest_earnings_date` | yfinance (unchanged) | EDGAR has filing dates, not earnings call dates |
| `eps_latest_q`, `revenue_latest_q` | EDGAR primary, yfinance fallback | |
| `eps_latest_q_yoy_pct`, `revenue_latest_q_yoy_pct` | EDGAR primary, yfinance fallback | |
| **`annual_eps_yoy_5y`** (renamed from `_3y`) | EDGAR primary, yfinance fallback | 5 datapoints, oldest→newest |
| **`annual_revenue_yoy_5y`** | EDGAR primary, yfinance fallback | 5 datapoints, oldest→newest |
| **`quarterly_eps_yoy_4q`** + `_labels` (renamed from `_2q`) | EDGAR primary, yfinance fallback | 4 datapoints, oldest→newest |
| **`quarterly_revenue_yoy_4q`** + `_labels` | EDGAR primary, yfinance fallback | 4 datapoints, oldest→newest |
| `yahoo_revenue_growth_yoy_pct`, `yahoo_earnings_growth_yoy_pct` | yfinance (unchanged, tertiary fallback) | Used today by `analyst.py` when both EDGAR and `income_stmt` are sparse — keep as-is |

For HK, the field names also change to the new `_5y` / `_4q` suffixes, but they are populated only from yfinance, padded with `None` in the indices that yfinance can't fill (yfinance free tier returns ~4 fiscal years and ~6 quarters → typically only the most recent 3 annual YoY slots and 2 quarterly YoY slots are non-None). This keeps `enrich.py` and `renderer.py` schema-uniform across markets. The same padding applies to the US-side **fallback path**: when EDGAR fails entirely and yfinance is the only source, the new `_5y` / `_4q` arrays will have leading `None`s — `renderer.py`'s SVG must skip None bars cleanly (already does, validated in commit `34b9026`).

## Architecture

### New module: `report/edgar.py`

Public surface — one function:

```python
def fetch_edgar_fundamentals(ticker: str) -> dict | None:
    """Return a dict with the 8 EDGAR-owned fields for `ticker`.
    Returns None on any failure (network, missing CIK, no matching XBRL concepts).
    Caller is expected to fall back to yfinance on None."""
```

Returned dict shape (when successful):

```python
{
    "eps_latest_q": float | None,
    "eps_latest_q_yoy_pct": float | None,
    "revenue_latest_q": float | None,
    "revenue_latest_q_yoy_pct": float | None,
    "annual_eps_yoy_5y": list[float | None],          # length 5, oldest→newest
    "annual_revenue_yoy_5y": list[float | None],      # length 5
    "quarterly_eps_yoy_4q": list[float | None],       # length 4, oldest→newest
    "quarterly_eps_yoy_4q_labels": list[str],         # length 4, e.g. ["Mar'24", ...]
    "quarterly_revenue_yoy_4q": list[float | None],   # length 4
    "quarterly_revenue_yoy_4q_labels": list[str],     # length 4
}
```

Internal helpers (all private, all pure-logic except the two HTTP wrappers):

- `_load_ticker_cik_map() -> dict[str, str]` — read/refresh `company_tickers.json`, return uppercase ticker → 10-digit zero-padded CIK string.
- `_fetch_companyfacts(cik: str) -> dict | None` — HTTP GET with TTL cache; returns parsed JSON or `None` on failure.
- `_match_concept(facts: dict, candidates: tuple[str, ...]) -> list[dict] | None` — walk concept fallback chain, return the first concept's `units.USD` (or `units.USD/shares` for EPS) array of fact dicts.
- `_select_annual_facts(facts_array: list[dict]) -> list[dict]` — filter `form == "10-K"` and `fp == "FY"`, dedupe by `end` date (keeping latest-filed), sort oldest→newest.
- `_select_quarterly_facts(facts_array: list[dict]) -> list[dict]` — combine 10-Q (Q1/Q2/Q3) and derived Q4 (= FY value − sum of Q1+Q2+Q3 of same FY); sort oldest→newest by `end`.
- `_compute_yoy(current: float, prior: float) -> float | None` — same semantics as `enrich.compute_yoy` (None when prior ≤ 0).
- `_period_label(end_date: date) -> str` — `"Mar'24"` short month + 2-digit year.

Concept fallback chains (in order):

```python
REVENUE_CONCEPTS = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
EPS_CONCEPTS = (
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
)
```

If a chain yields zero matches, the corresponding fields stay `None` and the function returns the partial dict; the caller's fallback then runs for the missing fields only (not all-or-nothing).

### Q4 derivation (the standard EDGAR gotcha)

`companyfacts` exposes Q1/Q2/Q3 directly via 10-Q filings (each a 3-month period) and the full year via 10-K (`fp == "FY"`, 12-month period). Q4 is **not** filed as a standalone period; it must be computed:

```
Q4_revenue = FY_revenue − (Q1_revenue + Q2_revenue + Q3_revenue)   # same fiscal year
Q4_eps     = FY_eps     − (Q1_eps     + Q2_eps     + Q3_eps)       # same fiscal year
```

If any of Q1/Q2/Q3 for the matching FY is missing (recent IPO, restated filing), that Q4 datapoint becomes `None` — no exception, just a gap. The caller's fallback will fill the gap from yfinance if possible.

### HTTP details

- Library: `httpx` (already a project dependency).
- Headers: `User-Agent: finviz-to-tv xuelong0208@gmail.com` (SEC requires email; missing or generic UA returns 403).
- Timeout: 10 seconds per request.
- Retry: 1 retry after 0.5s on 5xx or 429. No exponential backoff — fall back to yfinance instead of hammering.
- Concurrency: synchronous; called inside `enrich.fetch_ticker_data` which already runs sequentially in `__main__.py`. SEC's 10 req/s limit is not an issue at 30 tickers/day.

### Cache layout

`output/state/edgar_cache/`:

| File | TTL | Refresh trigger |
|---|---|---|
| `company_tickers.json` | 1 day | `mtime + 86400 < time.time()` |
| `CIK{0:010d}.json` | 7 days | `mtime + 7*86400 < time.time()` |

Cache reads/writes use `json.dumps(..., separators=(",", ":"))` for compactness. On any cache read error (corrupted file, partial write), delete and refetch — never raise.

No reverse `ticker → CIK` cache file: built in-memory once per process from `company_tickers.json` at first call.

## Wiring into `enrich.fetch_ticker_data`

The existing function gains an EDGAR-first branch for the 8 fundamental fields. Pseudocode:

```python
def fetch_ticker_data(ticker, group, exchange, rs_lookup):
    data = {...}  # template, unchanged

    # B-class fields via yfinance (unchanged: info, earnings_date, MRQ growth)
    t = yf.Ticker(ticker)
    ... existing info / earnings_date / yahoo_*_growth code ...

    # A-class fields: EDGAR first, then yfinance fallback
    edgar = fetch_edgar_fundamentals(ticker) if exchange in US_EXCHANGES else None
    if edgar:
        data.update(edgar)

    # Per-field fallback: any A-class field still None → try yfinance income_stmt
    if any of A-class fields is None:
        ... existing quarterly_income_stmt + income_stmt code ...
        # Only fill fields that EDGAR didn't fill, so EDGAR data is never overwritten.

    return data
```

The `US_EXCHANGES` gate (`{"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"}`) prevents EDGAR calls for HK tickers when this code path is shared. HK orchestrator passes a different `exchange` string, so the gate is a cheap sanity check; the real source of truth is `__main__.py` choosing market-specific enrichment, but defending here costs nothing.

## Renderer changes

`report/renderer.py` consumes the field names directly. Search-and-replace plus chart-axis updates:

- `annual_eps_yoy_3y` → `annual_eps_yoy_5y` (and revenue analogue)
- `quarterly_eps_yoy_2q` → `quarterly_eps_yoy_4q` (and revenue analogue)
- SVG chart code: 3 bars → 5 bars (annual), 2 bars → 4 bars (quarterly). Existing label-collision logic (commit `34b9026`) already scales with bar count.
- Verify that the existing renderer correctly skips `None` entries (chart with leading nulls renders only populated bars). This matters for HK and for the US fallback path. If it currently assumes a fixed bar count rather than iterating the array, fix it as part of this work.

No new chart styles or layout changes.

## Failure semantics

Mirrors the existing soft-fail philosophy of the report pipeline:

| Failure | Behavior |
|---|---|
| `company_tickers.json` HTTP fails | Log warning once, fall back to yfinance for *every* ticker this run |
| Single-ticker EDGAR HTTP fails | Log warning, fall back to yfinance for *that* ticker |
| Ticker not in CIK map | Log info (not warning — common for new IPOs), fall back to yfinance |
| Concept fallback exhausts without match | Field stays None, yfinance fallback runs for that field |
| Both EDGAR and yfinance return nothing | Field is None — `analyst.py` already gracefully handles this in the LLM prompt |
| EDGAR returns malformed JSON | Treat as fetch failure; delete corrupted cache file |

The `.txt` watchlists are the primary daily artifact and are written before the report ever runs. The report is itself already soft-fail in the wrapper script. Nothing in this design changes that contract.

## Testing

New `tests/test_edgar.py` — pure-logic, no network:

1. **Concept matching priority** — fixture with both `Revenues` and `RevenueFromContract...`; verify first match wins.
2. **Concept fallback** — fixture with only `RevenueFromContract...`; verify it's used when `Revenues` is absent.
3. **Annual YoY extraction** — fixture with 6 fiscal years; verify 5 YoY values, oldest→newest, correct percentages.
4. **Quarterly YoY with Q4 derivation** — fixture with two fiscal years of 10-Q + 10-K; verify the four most recent quarters all populate including derived Q4s.
5. **Q4 derivation gap** — fixture missing Q2 of the relevant FY; verify Q4 of that FY is None, neighbours unaffected.
6. **EPS concept fallback** — fixture with only `EarningsPerShareBasic`; verify it's used when `Diluted` absent.
7. **Cache TTL logic** — `_is_fresh(path, ttl_seconds)` returns True/False correctly; deliberately short TTL.
8. **Empty fact arrays** — `fetch_edgar_fundamentals` returns None gracefully when concepts match but have zero facts.

Fixtures live in `tests/fixtures/edgar/` as trimmed real `companyfacts` JSON (AAPL, V for the alt-revenue concept, plus a synthetic gap fixture).

No live network in tests. The HTTP layer is exercised manually during development and via the first real `--mode report --market us` run.

## Out of scope (deliberate)

- **Async EDGAR client.** `enrich` is sequential today; making EDGAR async without making yfinance async wouldn't move the wall-clock needle.
- **Cross-process cache locking.** Single-writer assumption holds (one report run at a time per market).
- **EDGAR full-text submissions API.** Only `companyfacts` is needed for the structured fields we use.
- **Coverage of foreign issuer filings (20-F / 40-F).** EDGAR does host these, but the XBRL fact structure differs and yfinance covers ADRs adequately. Treating ADRs as "EDGAR miss → yfinance fallback" is intentional.
- **Cache eviction / size limits.** ~30 tickers × ~1MB each = trivial. No GC needed.

## File-level change summary

- **New:** `report/edgar.py` (~250 lines), `tests/test_edgar.py` + `tests/fixtures/edgar/*.json`.
- **Modified:** `report/enrich.py` (add EDGAR call + fallback orchestration; update field names), `report/renderer.py` (field renames + SVG bar count), `report/__main__.py` (`_EMPTY_DATA_TEMPLATE` field renames), `prompts/canslim_system.md` (only if any prompt text references the `_3y` / `_2q` field names — check during implementation).
- **Unchanged:** `report/analyst.py`, `report/llm.py`, `report/ranker.py`, `report/state.py`, `report/search.py`, `hk_eod.py`, `main.py`, `config.toml`, `pyproject.toml`.
- **CLAUDE.md:** add a one-paragraph note under the "Daily CANSLIM Report" entry mentioning EDGAR is the primary US fundamentals source with yfinance fallback.
