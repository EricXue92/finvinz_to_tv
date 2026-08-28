"""Tests for ntfy_notify.sh message-ID dedup.

Regression: the ntfy CLI reuses its original --since on every internal
reconnect, replaying all messages in the retention window — the same
OKTA push fired 3 banners on 2026-08-27/28. The bridge script must skip
messages whose $NTFY_ID it has already delivered.

The script's paths and notifier binary are overridable via
NTFY_BRIDGE_{STATE_FILE,IDS_FILE,LOG,NOTIFIER} so tests run hermetically.
"""
from __future__ import annotations

import stat
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ntfy_notify.sh"


def _run(tmp_path: Path, msg_id: str, *, title: str = "T", time_s: str | None = None):
    """Invoke the bridge script with a stub notifier; return paths dict."""
    calls = tmp_path / "notifier_calls.txt"
    stub = tmp_path / "stub_notifier.sh"
    if not stub.exists():
        stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {
        "PATH": "/usr/bin:/bin",
        "NTFY_BRIDGE_STATE_FILE": str(tmp_path / "last_seen.txt"),
        "NTFY_BRIDGE_IDS_FILE": str(tmp_path / "seen_ids.txt"),
        "NTFY_BRIDGE_LOG": str(tmp_path / "bridge.log"),
        "NTFY_BRIDGE_NOTIFIER": str(stub),
        "NTFY_ID": msg_id,
        "NTFY_TIME": time_s or str(int(time.time())),
        "NTFY_TITLE": title,
        "NTFY_MESSAGE": "body",
        "NTFY_PRIORITY": "3",
    }
    subprocess.run([str(SCRIPT)], env=env, check=True, timeout=10)
    return calls


def test_first_delivery_notifies(tmp_path: Path) -> None:
    calls = _run(tmp_path, "id-aaa")
    assert calls.exists() and calls.read_text().count("\n") == 1


def test_replayed_id_is_skipped(tmp_path: Path) -> None:
    calls = _run(tmp_path, "id-aaa")
    _run(tmp_path, "id-aaa")  # reconnect replay of the same message
    assert calls.read_text().count("\n") == 1, "duplicate banner fired"


def test_distinct_ids_same_second_both_notify(tmp_path: Path) -> None:
    """Two pushes in the same second (new + PROMOTED) must both fire —
    dedup must key on message ID, not on timestamp."""
    now = str(int(time.time()))
    calls = _run(tmp_path, "id-aaa", time_s=now)
    _run(tmp_path, "id-bbb", time_s=now)
    assert calls.read_text().count("\n") == 2


def test_missing_id_still_notifies(tmp_path: Path) -> None:
    """Defensive: an ntfy CLI without $NTFY_ID must degrade to the old
    always-notify behavior, never to silence."""
    calls = _run(tmp_path, "")
    _run(tmp_path, "")
    assert calls.read_text().count("\n") == 2


def test_ids_file_is_trimmed(tmp_path: Path) -> None:
    for i in range(250):
        _run(tmp_path, f"id-{i}")
    ids = (tmp_path / "seen_ids.txt").read_text().splitlines()
    assert len(ids) <= 200
    assert "id-249" in ids  # newest kept


def test_replay_does_not_rewind_last_seen(tmp_path: Path) -> None:
    """A replayed old message must not move last_seen backwards, or the
    wrapper would resume from a stale offset after restart."""
    _run(tmp_path, "id-new", time_s="2000000000")
    _run(tmp_path, "id-old", time_s="1000000000")  # replayed older message
    assert (tmp_path / "last_seen.txt").read_text() == "2000000000"
