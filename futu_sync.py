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


def pre_market_gap_futu(
    tickers: list[str],
    min_gap_pct: float,
    host: str = "127.0.0.1",
    port: int = 11111,
) -> list[str] | None:
    """Filter US tickers by pre-market gap % via Futu OpenAPI snapshot.
    Reads ``pre_change_rate`` (already in percent units) from
    ``get_market_snapshot`` — real-time on US Lv1 BBO accounts. Tickers with
    no pre-market trades (``pre_volume == 0``) are dropped.

    Returns the surviving subset (in input ticker format), or ``None`` on
    any failure so the caller can fall back to another data source.
    """
    if not tickers:
        return []
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError:
        logger.warning("  Futu pre-market: futu-api not installed")
        return None

    if not _opend_reachable(host, port):
        logger.warning(
            f"  Futu pre-market: OpenD not reachable at {host}:{port}"
        )
        return None

    code_to_ticker: dict[str, str] = {}
    for t in tickers:
        c = _to_futu_code(t, "US")
        if c:
            code_to_ticker[c] = t
    if not code_to_ticker:
        return []

    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        ret, data = ctx.get_market_snapshot(list(code_to_ticker.keys()))
        if ret != RET_OK:
            logger.warning(f"  Futu pre-market: get_market_snapshot failed — {data}")
            return None

        result: list[str] = []
        for _, row in data.iterrows():
            code = row.get("code")
            ticker = code_to_ticker.get(code)
            if ticker is None:
                continue
            try:
                pre_vol = float(row.get("pre_volume", 0) or 0)
            except (TypeError, ValueError):
                pre_vol = 0
            if pre_vol <= 0:
                logger.info(f"  {ticker}: no pre-market trades yet, dropping")
                continue
            try:
                gap = float(row.get("pre_change_rate"))
            except (TypeError, ValueError):
                logger.info(f"  {ticker}: pre_change_rate unavailable, dropping")
                continue
            if gap >= min_gap_pct:
                result.append(ticker)
            else:
                logger.info(
                    f"  {ticker}: pre-market gap {gap:+.2f}% < +{min_gap_pct}%, dropping"
                )
        return result
    except Exception as e:
        logger.warning(f"  Futu pre-market: unexpected error — {e}")
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def intraday_cumulative_volume_futu(
    tickers: list[str],
    avg_daily_volumes: dict[str, float],
    host: str = "127.0.0.1",
    port: int = 11111,
) -> list[str] | None:
    """Filter US tickers whose today's RTH cumulative volume >= their 20-day
    average daily volume. Reads ``volume`` from ``get_market_snapshot`` —
    that field is today's regular-session cumulative (separate from
    ``pre_volume`` / ``after_volume``), so calling it at e.g. 09:50 ET
    returns the first ~20 minutes of RTH volume.

    Returns the surviving subset (input ticker format), or ``None`` on any
    failure so the caller can fall back. Tickers with zero or missing
    avg_daily_volume entries are dropped.
    """
    if not tickers:
        return []
    try:
        from futu import OpenQuoteContext, RET_OK
    except ImportError:
        logger.warning("  Futu intraday volume: futu-api not installed")
        return None

    if not _opend_reachable(host, port):
        logger.warning(
            f"  Futu intraday volume: OpenD not reachable at {host}:{port}"
        )
        return None

    code_to_ticker: dict[str, str] = {}
    for t in tickers:
        c = _to_futu_code(t, "US")
        if c:
            code_to_ticker[c] = t
    if not code_to_ticker:
        return []

    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        ret, data = ctx.get_market_snapshot(list(code_to_ticker.keys()))
        if ret != RET_OK:
            logger.warning(f"  Futu intraday volume: get_market_snapshot failed — {data}")
            return None

        result: list[str] = []
        for _, row in data.iterrows():
            code = row.get("code")
            ticker = code_to_ticker.get(code)
            if ticker is None:
                continue
            avg = avg_daily_volumes.get(ticker)
            if avg is None or avg <= 0:
                continue
            try:
                vol = float(row.get("volume", 0) or 0)
            except (TypeError, ValueError):
                vol = 0
            if vol >= avg:
                result.append(ticker)
            else:
                logger.info(
                    f"  {ticker}: cumulative {vol:,.0f} < 20d avg {avg:,.0f}, dropping"
                )
        return result
    except Exception as e:
        logger.warning(f"  Futu intraday volume: unexpected error — {e}")
        return None
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass


def sync_to_futu(
    tickers: list[str],
    group_name: str,
    market: Literal["US", "HK"],
    host: str = "127.0.0.1",
    port: int = 11111,
    append_only: bool = False,
) -> bool:
    """Sync the ticker list to a Futu custom watchlist group.

    Computes the diff against the group's current contents and applies only
    the necessary ADD / DEL ops (saves API calls — limit is 10 per 30s).

    When ``append_only`` is True, the DEL phase is skipped: tickers are only
    added, never removed. Used for shared/merged groups (e.g. multiple
    scanners feeding into one Futu group) so each scanner doesn't clobber
    others' contributions. The group accumulates monotonically across runs.
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
        to_del = [] if append_only else sorted(current - desired)

        if to_del:
            ret, msg = ctx.modify_user_security(group_name, ModifyUserSecurityOp.DEL, to_del)
            if ret != RET_OK:
                logger.warning(f"  Futu sync ({group_name}): DEL failed — {msg}")
        if to_add:
            ret, msg = ctx.modify_user_security(group_name, ModifyUserSecurityOp.ADD, to_add)
            if ret != RET_OK:
                logger.warning(f"  Futu sync ({group_name}): ADD failed — {msg}")
                return False

        final_size = len(current | desired) if append_only else len(desired)
        logger.info(
            f"  Futu sync ({group_name}): +{len(to_add)} -{len(to_del)} "
            f"({final_size} tickers in group{', append-only' if append_only else ''})"
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
