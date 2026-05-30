"""Tests for the cold-wake network-readiness gate."""

from __future__ import annotations

import pytest

import net_ready


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_returns_true_immediately_when_all_hosts_reachable() -> None:
    clock = FakeClock()
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock.sleep(s)

    assert net_ready.wait_for_network(
        ["a.example", "b.example"],
        probe=lambda host, port: True,
        sleep=fake_sleep,
        monotonic=clock.monotonic,
    )
    assert sleeps == []  # no waiting needed


def test_polls_until_reachable() -> None:
    clock = FakeClock()
    attempts = {"n": 0}

    def probe(host: str, port: int) -> bool:
        attempts["n"] += 1
        # First two passes (= 4 probes for 2 hosts) fail; then succeed.
        return attempts["n"] > 4

    assert net_ready.wait_for_network(
        ["a.example", "b.example"],
        max_wait_seconds=60.0,
        initial_delay=1.0,
        probe=probe,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    # 3rd pass succeeded → 2 sleeps consumed; clock should have advanced.
    assert clock.now > 0


def test_returns_false_on_timeout() -> None:
    clock = FakeClock()
    assert not net_ready.wait_for_network(
        ["a.example"],
        max_wait_seconds=10.0,
        initial_delay=1.0,
        max_delay=2.0,
        probe=lambda host, port: False,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    # Should not have slept past the deadline.
    assert clock.now <= 10.0


def test_one_host_unreachable_keeps_waiting() -> None:
    clock = FakeClock()

    def probe(host: str, port: int) -> bool:
        return host == "good.example"  # bad host never resolves

    assert not net_ready.wait_for_network(
        ["good.example", "bad.example"],
        max_wait_seconds=5.0,
        initial_delay=1.0,
        probe=probe,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
