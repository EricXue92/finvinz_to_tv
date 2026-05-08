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


FIXTURES = Path(__file__).parent / "fixtures" / "edgar"


def test_parse_ticker_cik_map_zero_pads_cik():
    raw = json.loads((FIXTURES / "company_tickers.json").read_text())
    table = edgar._parse_ticker_cik_map(raw)
    assert table["AAPL"] == "0000320193"
    assert table["V"] == "0001403161"
    assert table["GOOGL"] == "0000001652044"[-10:]   # 10-digit zero-padded


def test_parse_ticker_cik_map_uppercases_ticker():
    raw = {"0": {"cik_str": 1, "ticker": "tsla", "title": "Tesla"}}
    table = edgar._parse_ticker_cik_map(raw)
    assert "TSLA" in table


def test_get_cik_uses_cache_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    cache_path = tmp_path / "company_tickers.json"
    cache_path.write_text(
        (FIXTURES / "company_tickers.json").read_text()
    )
    # _get_cik should not hit the network when cache is fresh
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: pytest.fail("network hit"))
    edgar._cached_ticker_map = None    # reset module-level memo
    assert edgar._get_cik("AAPL") == "0000320193"


def test_get_cik_returns_none_for_unknown_ticker(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "company_tickers.json").write_text(
        (FIXTURES / "company_tickers.json").read_text()
    )
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: None)
    edgar._cached_ticker_map = None
    assert edgar._get_cik("ZZZZ") is None


def test_get_cik_returns_none_when_network_and_cache_both_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: None)
    edgar._cached_ticker_map = None
    assert edgar._get_cik("AAPL") is None


def test_fetch_companyfacts_uses_cache_when_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    cik = "0000320193"
    cache_path = tmp_path / f"CIK{cik}.json"
    cache_path.write_text('{"facts": {"us-gaap": {}}}')
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: pytest.fail("network hit"))
    assert edgar._fetch_companyfacts(cik) == {"facts": {"us-gaap": {}}}


def test_fetch_companyfacts_fetches_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    cik = "0000320193"
    payload = {"facts": {"us-gaap": {"Revenues": {}}}}
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: payload)
    got = edgar._fetch_companyfacts(cik)
    assert got == payload
    # Cache should now exist.
    assert (tmp_path / f"CIK{cik}.json").is_file()


def test_fetch_companyfacts_returns_none_when_404(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(edgar, "_http_get_json", lambda url: None)
    assert edgar._fetch_companyfacts("0000000001") is None


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_match_concept_returns_first_match(monkeypatch):
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    out = edgar._match_concept_facts(
        facts, ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"), "USD"
    )
    assert out is not None
    assert any(f["fy"] == 2024 and f["fp"] == "FY" for f in out)


def test_match_concept_falls_back_when_first_missing():
    facts = _load_fixture("companyfacts_v_alt_revenue.json")
    out = edgar._match_concept_facts(
        facts, ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"), "USD"
    )
    assert out is not None
    # Should have used the alt concept.
    assert any(f["fy"] == 2024 and f["fp"] == "FY" for f in out)


def test_match_concept_returns_none_when_no_match():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    assert edgar._match_concept_facts(facts, ("NoSuchConcept",), "USD") is None


def test_match_concept_handles_missing_unit():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    # Revenues exists but only with USD unit; asking for EUR returns None.
    assert edgar._match_concept_facts(facts, ("Revenues",), "EUR") is None


def test_select_annual_facts_filters_to_10k_fy_and_sorts():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    annual = edgar._select_annual_facts(raw)
    # 6 fiscal years, oldest first
    assert len(annual) == 6
    assert annual[0]["fy"] == 2020
    assert annual[-1]["fy"] == 2025


def test_select_annual_facts_dedupes_amendments(tmp_path):
    raw = [
        {"end": "2024-09-28", "val": 100, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
        {"end": "2024-09-28", "val": 105, "fy": 2024, "fp": "FY", "form": "10-K/A", "filed": "2025-02-01"},
    ]
    out = edgar._select_annual_facts(raw)
    assert len(out) == 1
    # Latest filed wins (the amendment).
    assert out[0]["val"] == 105


def test_extract_annual_yoy_5y_full_history():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    yoy = edgar._extract_annual_yoy(raw, years_back=5)
    assert len(yoy) == 5
    # Oldest first: FY2021 vs FY2020 = (365817 - 274515) / 274515 = +33.26%
    assert yoy[0] == pytest.approx(33.26, rel=0.01)
    # Newest: FY2025 vs FY2024 = (416000 - 391035) / 391035 = +6.38%
    assert yoy[-1] == pytest.approx(6.38, rel=0.01)


def test_extract_annual_yoy_pads_with_none_when_history_short():
    raw = [
        {"end": "2024-09-28", "val": 100, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
        {"end": "2025-09-27", "val": 110, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
    ]
    yoy = edgar._extract_annual_yoy(raw, years_back=5)
    assert yoy == [None, None, None, None, pytest.approx(10.0, rel=0.01)]


def test_extract_annual_yoy_skips_bad_prior():
    raw = [
        {"end": "2023-09-30", "val": -10, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
        {"end": "2024-09-28", "val":  20, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
        {"end": "2025-09-27", "val":  30, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"},
    ]
    yoy = edgar._extract_annual_yoy(raw, years_back=5)
    # FY24 vs FY23: prior was -10 (negative) → None
    # FY25 vs FY24: 20 → 30 = +50%
    assert yoy[-2] is None
    assert yoy[-1] == pytest.approx(50.0, rel=0.01)


def test_select_quarterly_facts_uses_10q_directly_for_q1_q2_q3():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    quarters = edgar._select_quarterly_facts(raw)
    # Should include Apple's Q1 2024 = 119575000000
    found = [q for q in quarters if q["end"] == "2023-12-30"]
    assert len(found) == 1
    assert found[0]["val"] == 119575000000


def test_select_quarterly_facts_derives_q4_from_fy_minus_q1q2q3():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    quarters = edgar._select_quarterly_facts(raw)
    # FY2024 Q4 = 391035 - (119575 + 90753 + 85777) = 94930 (in millions: 94930000000)
    fy24_q4 = [q for q in quarters if q.get("fy") == 2024 and q.get("fp") == "Q4"]
    assert len(fy24_q4) == 1
    assert fy24_q4[0]["val"] == 391035000000 - (119575000000 + 90753000000 + 85777000000)


def test_select_quarterly_facts_skips_q4_when_a_component_missing():
    facts = _load_fixture("companyfacts_q2_gap.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    quarters = edgar._select_quarterly_facts(raw)
    # FY2024 Q2 missing → no FY2024 Q4 emitted.
    fy24_q4 = [q for q in quarters if q.get("fy") == 2024 and q.get("fp") == "Q4"]
    assert fy24_q4 == []
    # FY2023 Q4 should still be present (220 + 240 + 260 = 720; FY=1000; Q4=280).
    fy23_q4 = [q for q in quarters if q.get("fy") == 2023 and q.get("fp") == "Q4"]
    assert len(fy23_q4) == 1
    assert fy23_q4[0]["val"] == 280


def test_extract_quarterly_yoy_4q_with_full_history():
    facts = _load_fixture("companyfacts_aapl_minimal.json")
    raw = edgar._match_concept_facts(facts, ("Revenues",), "USD")
    yoy, labels = edgar._extract_quarterly_yoy(raw, n_quarters=4)
    assert len(yoy) == 4
    assert len(labels) == 4
    # Fixture has FY2025 10-K → Q4 FY2025 is derived (110.6B vs Q4 FY2024 94.93B ≈ +16.5%).
    # The last 4 quarters chronologically: Q1/Q2/Q3 FY2025 + derived Q4 FY2025.
    # Q3 FY2025 (Jun'25): 85.7B vs Q3 FY2024 (Jun'24): 85.777B ≈ -0.09%
    assert yoy[-2] == pytest.approx(-0.09, abs=0.5)
    # Q4 FY2025 (Sep'25): derived 110.6B vs Q4 FY2024 94.93B ≈ +16.5%
    assert yoy[-1] == pytest.approx(16.5, abs=0.5)
    # Each label is "Mon'YY"
    for lbl in labels:
        assert len(lbl) == 6
        assert lbl[3] == "'"


def test_extract_quarterly_yoy_pads_when_short_history():
    raw = [
        {"start": "2024-10-01", "end": "2024-12-31", "val": 100, "fy": 2025, "fp": "Q1", "form": "10-Q", "filed": "2025-02-01"},
    ]
    yoy, labels = edgar._extract_quarterly_yoy(raw, n_quarters=4)
    assert yoy == [None, None, None, None]
    assert labels[-1] == "Dec'24"
