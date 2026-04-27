"""Sync watchlists to Futu (富途牛牛) via OpenAPI.

Requires FutuOpenD running locally (default 127.0.0.1:11111) and the user
to have pre-created custom watchlist groups matching the configured names —
the API can only modify custom groups, not create them.

The .txt watchlist files remain the primary artifact; failures here log a
warning and return False without raising.
"""

import logging
import socket
from typing import Literal

logger = logging.getLogger(__name__)


def _opend_reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    """Quick TCP probe — OpenQuoteContext retries forever on a closed port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _to_futu_code(ticker: str, market: Literal["US", "HK"]) -> str | None:
    """Convert internal ticker to Futu market.code format.

    US: AAPL → US.AAPL
    HK: HKEX:0522 / 522 / 0522.HK → HK.00522 (5-digit zero-padded)
    """
    t = ticker.strip()
    if not t:
        return None
    if market == "US":
        return f"US.{t}"
    if market == "HK":
        if t.startswith("HKEX:"):
            t = t[5:]
        t = t.replace(".HK", "")
        try:
            n = int(t)
        except ValueError:
            return None
        return f"HK.{n:05d}"
    return None


def sync_to_futu(
    tickers: list[str],
    group_name: str,
    market: Literal["US", "HK"],
    host: str = "127.0.0.1",
    port: int = 11111,
) -> bool:
    """Sync the ticker list to a Futu custom watchlist group.

    Computes the diff against the group's current contents and applies only
    the necessary ADD / DEL ops (saves API calls — limit is 10 per 30s).
    """
    try:
        from futu import OpenQuoteContext, ModifyUserSecurityOp, RET_OK
    except ImportError:
        logger.warning(f"  futu-api not installed; skipping Futu sync for '{group_name}'")
        return False

    futu_codes = [c for t in tickers if (c := _to_futu_code(t, market))]
    if not futu_codes:
        logger.info(f"  Futu sync ({group_name}): no tickers to sync")
        return False
    desired = set(futu_codes)

    if not _opend_reachable(host, port):
        logger.warning(
            f"  Futu sync ({group_name}): OpenD not reachable at {host}:{port}, skipping"
        )
        return False

    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
    except Exception as e:
        logger.warning(
            f"  Futu sync ({group_name}): cannot connect to OpenD at {host}:{port} — {e}"
        )
        return False

    try:
        ret, data = ctx.get_user_security(group_name)
        if ret != RET_OK:
            logger.warning(f"  Futu sync ({group_name}): get_user_security failed — {data}")
            return False

        if hasattr(data, "columns") and "code" in data.columns:
            current = set(data["code"].tolist())
        else:
            current = set()

        to_add = sorted(desired - current)
        to_del = sorted(current - desired)

        if to_del:
            ret, msg = ctx.modify_user_security(group_name, ModifyUserSecurityOp.DEL, to_del)
            if ret != RET_OK:
                logger.warning(f"  Futu sync ({group_name}): DEL failed — {msg}")
        if to_add:
            ret, msg = ctx.modify_user_security(group_name, ModifyUserSecurityOp.ADD, to_add)
            if ret != RET_OK:
                logger.warning(f"  Futu sync ({group_name}): ADD failed — {msg}")
                return False

        logger.info(
            f"  Futu sync ({group_name}): +{len(to_add)} -{len(to_del)} "
            f"({len(desired)} tickers in group)"
        )
        return True
    except Exception as e:
        logger.warning(f"  Futu sync ({group_name}): unexpected error — {e}")
        return False
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
