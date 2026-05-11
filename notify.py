"""ntfy.sh push notifications. Single side-effect: HTTP POST.

Failures are swallowed and logged. Never raises — same contract as futu_sync.
"""

import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 5.0


def _format_body(tickers: list[str], max_in_body: int, total: int) -> str:
    shown = tickers[:max_in_body]
    extra = len(tickers) - len(shown)
    parts = [", ".join(shown)]
    if extra > 0:
        parts.append(f"(+{extra} more)")
    parts.append(f" · total: {total}")
    return " ".join(parts)


def _ntfy_post(server: str, topic: str, title: str, body: str, priority: str) -> None:
    url = f"{server}/{topic}"
    req = Request(
        url,
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": priority},
        method="POST",
    )
    try:
        with urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            logger.info(f"[Notify] pushed: {title} (HTTP {resp.status})")
    except (URLError, TimeoutError, OSError) as e:
        logger.warning(f"[Notify] ntfy POST failed: {e}")
    except Exception as e:
        logger.warning(f"[Notify] unexpected error: {e}")


def notify_morning_gap(
    new_tickers: list[str],
    offset_min: int,
    total: int,
    config: dict,
    promoted: list[str] | None = None,
    market: str = "US",
) -> None:
    """Push ntfy alert(s) for a morning-gap scan.

    Args:
        new_tickers: brand-new tickers this scan (not seen in any earlier
            scan of the same phase, and — for post-open — not in pre-market
            seen either). Fires the regular alert.
        offset_min: minutes from market open. Negative = pre-market.
        total: total ticker count for this scan (including repeats).
        config: full parsed config.toml dict. Reads [notify] section.
        promoted: tickers that appeared in pre-market and are now first-confirmed
            post-open (post-open volume gate just crossed). Fires a separate
            high-priority alert. Always empty for pre-market scans and for
            HK (HK has no pre phase).
        market: "US" or "HK" — controls the title prefix only.
    """
    promoted = promoted or []
    if not new_tickers and not promoted:
        return

    notify_cfg = config.get("notify") or {}
    if not notify_cfg.get("enabled", False):
        return

    topic = notify_cfg.get("ntfy_topic")
    if not topic:
        logger.warning("[Notify] ntfy_topic missing in [notify] config")
        return

    server = notify_cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    max_in_body = int(notify_cfg.get("max_tickers_in_body", 10))
    sign = "" if offset_min < 0 else "+"
    prefix = "HK Morning Gap" if market == "HK" else "Morning Gap"

    if new_tickers:
        title = f"{prefix} {sign}{offset_min}min · {len(new_tickers)} new"
        body = _format_body(new_tickers, max_in_body, total)
        _ntfy_post(server, topic, title, body, priority="default")

    if promoted:
        title = f"{prefix} {sign}{offset_min}min · {len(promoted)} PROMOTED"
        body = (
            _format_body(promoted, max_in_body, total)
            + " · pre-market gap confirmed by RTH volume"
        )
        _ntfy_post(server, topic, title, body, priority="high")
