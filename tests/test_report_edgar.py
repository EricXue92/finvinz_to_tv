import json
import os
import time
from pathlib import Path

import pytest

from report import edgar


def test_is_fresh_returns_false_when_file_missing(tmp_path: Path):
    assert edgar._is_fresh(tmp_path / "missing.json", ttl_seconds=10) is False


def test_is_fresh_returns_true_for_fresh_file(tmp_path: Path):
    p = tmp_path / "fresh.json"
    p.write_text("{}")
    assert edgar._is_fresh(p, ttl_seconds=86400) is True


def test_is_fresh_returns_false_for_stale_file(tmp_path: Path):
    p = tmp_path / "stale.json"
    p.write_text("{}")
    old = time.time() - 100
    os.utime(p, (old, old))
    assert edgar._is_fresh(p, ttl_seconds=10) is False
