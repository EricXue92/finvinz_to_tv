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


def test_save_and_load_json_cache_roundtrip(tmp_path: Path):
    p = tmp_path / "data.json"
    edgar._save_json_cache(p, {"hello": 1})
    assert edgar._load_json_cache(p) == {"hello": 1}


def test_load_json_cache_returns_none_for_corrupt_file_and_deletes_it(tmp_path: Path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not json")
    assert edgar._load_json_cache(p) is None
    assert not p.exists()


def test_load_json_cache_returns_none_when_missing(tmp_path: Path):
    assert edgar._load_json_cache(tmp_path / "missing.json") is None


def test_http_get_json_returns_payload_on_200(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        def json(self):
            return {"ok": True}
        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(edgar.httpx, "get", fake_get)
    assert edgar._http_get_json("https://x") == {"ok": True}
    assert calls["n"] == 1


def test_http_get_json_retries_once_on_5xx(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, code):
            self.status_code = code
        def json(self):
            return {"ok": True}
        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp(500 if calls["n"] == 1 else 200)

    monkeypatch.setattr(edgar.httpx, "get", fake_get)
    monkeypatch.setattr(edgar.time, "sleep", lambda s: None)
    assert edgar._http_get_json("https://x") == {"ok": True}
    assert calls["n"] == 2


def test_http_get_json_returns_none_after_two_failures(monkeypatch):
    class FakeResp:
        status_code = 503
        def raise_for_status(self):
            pass

    monkeypatch.setattr(edgar.httpx, "get", lambda *a, **kw: FakeResp())
    monkeypatch.setattr(edgar.time, "sleep", lambda s: None)
    assert edgar._http_get_json("https://x") is None


def test_http_get_json_returns_none_on_404_no_retry(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        status_code = 404
        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(edgar.httpx, "get", fake_get)
    assert edgar._http_get_json("https://x") is None
    assert calls["n"] == 1   # 404 = "company not in EDGAR", do not retry
