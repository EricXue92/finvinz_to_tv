from hk_eod import _to_tv, _tv_to_code


def test_tv_to_code_roundtrip():
    for code in ["HK.00700", "HK.00001", "HK.00148"]:
        assert _tv_to_code(_to_tv(code)) == code


def test_tv_to_code_explicit():
    assert _tv_to_code("HKEX:700") == "HK.00700"
    assert _tv_to_code("HKEX:1") == "HK.00001"
