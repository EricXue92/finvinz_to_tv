# Morning Gap Notifications — Design

**Status:** Approved
**Date:** 2026-04-28
**Author:** XUE (with Claude)

## Goal

Push a phone/desktop notification whenever a Morning Gap scan surfaces **new** tickers, so the user doesn't have to babysit the launchd job or open the `.txt` file to know if anything fired.

## Channel

[ntfy.sh](https://ntfy.sh) only — single public/free pub-sub server, no API key, no account.

- Topic: `xue-finviz-morning-gap-9f3k2` (random suffix = private channel; anyone who knows the topic name can subscribe).
- Phone: ntfy iOS app subscribes to topic.
- Mac: web tab on `https://ntfy.sh/xue-finviz-morning-gap-9f3k2` with browser notification permission granted (delivers via macOS Notification Center).

`osascript` / native macOS notifications are **not** used — they silently fail when triggered from launchd background processes without per-app permission grants. The browser-based ntfy subscription gives the same effect with no permission friction.

## Trigger condition

Fire only when the current scan adds **new tickers** that have not appeared in any earlier morning-gap scan **today**. Repeated tickers across the 7 daily scans are suppressed.

Why new-only: morning-gap re-runs every ~15 min and the same tickers will recur. A ping per repeat trains the user to ignore notifications. New-only keeps signal high while still alerting on first sighting and any fresh adds across pre-market and post-open.

Pre-market and post-open scans share the same seen-set — a ticker that hit at -30min will not re-ping at +15min.

## State

One file per day, auto-resets each morning, no cleanup logic needed:

```
output/state/morning_gap_seen_<YYYY_MM_DD>.txt
```

One ticker per line. Read at scan start, union'd with the current tickers, written back at end.

## Notification format

**Title:** `Morning Gap ±Nmin · K new`
- Sign + offset minutes from market open (matches existing log format).
- `K` = count of new tickers in this scan.

**Body:** Up to `max_tickers_in_body` (default 10) tickers comma-separated, then `(+N more)` if truncated, then ` · total: M` where `M` is the full scan ticker count.

Examples:
- Title: `Morning Gap -30min · 2 new` / Body: `AAPL, NVDA  ·  total: 2`
- Title: `Morning Gap +45min · 12 new` / Body: `AAPL, AMD, AVGO, COIN, CRWD, GOOG, META, MSFT, NVDA, TSLA (+2 more) · total: 18`

ntfy `Priority: default` (normal phone notification, respects Do Not Disturb).

## Architecture

New module `notify.py`:

```python
def notify_morning_gap(
    new_tickers: list[str],
    offset_min: int,
    total: int,
    config: dict,
) -> None
```

- No-op if `new_tickers` empty or `[notify] enabled = false`.
- Sends one HTTP POST to `<ntfy_server>/<ntfy_topic>` via `urllib.request` (no new dependency).
- Catches all exceptions, logs a warning, never raises. Same robustness contract as `_futu_sync`.

Called from `main.py` morning-gap branch right after the dated `.txt` is successfully written by `safe_write_watchlist` (around line 1101). The "new tickers" diff and seen-state read/write happen inline in `main.py` since they need the same `today` / `stem` context already in scope:

```python
if safe_write_watchlist(sorted_tickers, dated, fmt):
    ...
    _write_webull(...)
    _futu_sync(...)
    new = _morning_gap_new_tickers(today, sorted_tickers, output_dir)
    if new:
        notify_morning_gap(new, offset, len(sorted_tickers), config)
```

`_morning_gap_new_tickers(today, tickers, output_dir)` is a small helper in `main.py` that reads the state file, computes the diff, writes the union back, returns the new set sorted.

## Config

New section in `config.toml`:

```toml
[notify]
enabled = true
ntfy_topic = "xue-finviz-morning-gap-9f3k2"
ntfy_server = "https://ntfy.sh"
max_tickers_in_body = 10
```

No secrets — the topic name is the only sensitivity, and the random suffix makes it unguessable.

## Failure handling

- ntfy POST fails (network down, 5xx, timeout): warning logged, scan completes normally, `.txt` and Futu sync unaffected.
- State file missing or unreadable: treated as empty seen-set (every ticker is new today). Scan continues.
- State file write fails: warning logged. Next scan will re-notify on the same tickers — annoying but not broken.

## Out of scope

- Other scanner groups (EarningsGap, HighVolume, Shorts, etc.) — only morning-gap notifies in v1. Easy to extend later by reusing `notify.py` from those code paths.
- Click-to-open (notification → open `.txt` or Futu) — ntfy supports `Click` header but adds complexity for marginal value.
- Priority escalation based on ticker count — flat `default` priority for all alerts.
- macOS native notifications — see Channel section.

## Testing

- Manual ntfy POST via `curl` already verified end-to-end delivery to phone and Mac browser tab on 2026-04-28.
- Post-implementation: trigger one morning-gap scan manually (or wait for next launchd firing in pre-market window) and verify a notification arrives with the expected title/body format.
