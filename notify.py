"""ntfy.sh push notifications. Single side-effect: HTTP POST.

Failures are swallowed and logged. Never raises — same contract as futu_sync.
"""

import logging
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 5.0


def notify_morning_gap(
    new_tickers: list[str],
    offset_min: int,
    total: int,
    config: dict,
) -> None:
    """Push one ntfy notification for a morning-gap scan with new tickers.

    Args:
        new_tickers: tickers that did not appear in any earlier morning-gap
            scan today. Caller computes this diff. Empty list -> no-op.
        offset_min: minutes from market open. Negative = pre-market.
        total: total ticker count for this scan (incl. repeats).
        config: full parsed config.toml dict. Reads [notify] section.
    """
    if not new_tickers:
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
    title = f"Morning Gap {sign}{offset_min}min · {len(new_tickers)} new"

    shown = new_tickers[:max_in_body]
    extra = len(new_tickers) - len(shown)
    body_parts = [", ".join(shown)]
    if extra > 0:
        body_parts.append(f"(+{extra} more)")
    body_parts.append(f" · total: {total}")
    body = " ".join(body_parts)

    url = f"{server}/{topic}"
    req = Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "default",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            if resp.status >= 400:
                logger.warning(f"[Notify] ntfy POST returned {resp.status}")
            else:
                logger.info(f"[Notify] pushed: {title}")
    except (URLError, TimeoutError, OSError) as e:
        logger.warning(f"[Notify] ntfy POST failed: {e}")
    except Exception as e:
        logger.warning(f"[Notify] unexpected error: {e}")
