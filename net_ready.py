"""Wait for outbound network (DNS + TCP) before starting a pipeline.

Fixes the cold-wake race: launchd fires at the scheduled second, but Wi-Fi /
DNS resolver are not yet up, so every fetcher NXDOMAINs and the pipeline
silently writes 0-byte .txt files. Mirrors `futu_sync._opend_reachable` but
also exercises DNS (the OpenD probe is loopback-only).
"""

from __future__ import annotations

import logging
import socket
import time
from typing import Callable

logger = logging.getLogger(__name__)


def _host_reachable(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    """True if DNS resolves AND a TCP connect to host:port succeeds."""
    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_network(
    hosts: list[str],
    *,
    max_wait_seconds: float = 300.0,
    initial_delay: float = 5.0,
    max_delay: float = 30.0,
    settle_seconds: float = 0.0,
    port: int = 443,
    probe: Callable[[str, int], bool] = _host_reachable,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """Block until every host is DNS+TCP reachable, or `max_wait_seconds` elapses.

    Returns True on success, False on timeout. Caller decides whether a timeout
    is fatal (EOD modes) or a clean-exit (morning-gap modes — see CLAUDE.md).

    A first successful TCP handshake on a cold-wake Wi-Fi link does not mean the
    link is stable: yfinance batch downloads issued in the next minute can still
    come back empty. `settle_seconds` sleeps AFTER every host is reachable,
    before returning, to let the freshly-established route stabilize.
    """
    deadline = monotonic() + max_wait_seconds
    delay = initial_delay
    attempt = 0
    while True:
        attempt += 1
        unreachable = [h for h in hosts if not probe(h, port)]
        if not unreachable:
            if attempt > 1:
                logger.info(
                    f"[net-ready] Network up after {attempt} attempts "
                    f"({len(hosts)} host(s) reachable)"
                )
            if settle_seconds > 0:
                logger.info(
                    f"[net-ready] Settling {settle_seconds:.0f}s before starting"
                )
                sleep(settle_seconds)
            return True
        remaining = deadline - monotonic()
        if remaining <= 0:
            logger.error(
                f"[net-ready] Network not ready after {max_wait_seconds:.0f}s; "
                f"still unreachable: {unreachable}"
            )
            return False
        sleep_for = min(delay, remaining)
        if attempt == 1:
            logger.warning(
                f"[net-ready] Waiting for network (unreachable: {unreachable}); "
                f"polling up to {max_wait_seconds:.0f}s before aborting"
            )
        sleep(sleep_for)
        delay = min(delay * 1.5, max_delay)
