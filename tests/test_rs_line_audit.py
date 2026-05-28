import pandas as pd

from rs_line_audit import render_report, _hk_master_to_futu


def _frame(d):
    return pd.DataFrame.from_dict(
        d, orient="index", columns=["rs_ema", "rs_ema_chg_5d"]
    )


def test_render_sorts_weakest_first_and_flags_cuts():
    feats = _frame({
        "AAA": (0.01, 0.06),    # +6%  strong
        "BBB": (0.02, -0.032),  # -3.2% cut
        "CCC": (0.03, -0.004),  # -0.4% within band
        "DDD": (0.04, -0.009),  # -0.9% cut
    })
    text = render_report(["AAA", "BBB", "CCC", "DDD"], feats, tolerance=0.005,
                         market="US", as_of="2026-05-28")
    # weakest first: BBB(-3.2) DDD(-0.9) CCC(-0.4) AAA(+6)
    assert text.index("BBB") < text.index("DDD") < text.index("CCC") < text.index("AAA")
    # cut flag only on chg < -0.5%
    bbb_line = next(l for l in text.splitlines() if "BBB" in l)
    ccc_line = next(l for l in text.splitlines() if "CCC" in l)
    assert "CUT" in bbb_line
    assert "CUT" not in ccc_line


def test_render_lists_unknowns_and_counts():
    feats = _frame({"AAA": (0.01, 0.06), "BBB": (0.02, -0.032)})
    text = render_report(["AAA", "BBB", "ZZZ", "YYY"], feats, tolerance=0.005,
                         market="US", as_of="2026-05-28")
    assert "ZZZ" in text and "YYY" in text          # unknown names shown
    assert "scanned: 4" in text
    assert "scored: 2" in text
    assert "unknown: 2" in text
    assert "would-cut: 1" in text


def test_render_handles_all_unknown_without_crash():
    text = render_report(["AAA", "BBB"], _frame({}), tolerance=0.005,
                         market="HK", as_of="2026-05-28")
    assert "scored: 0" in text and "unknown: 2" in text


def test_hk_master_to_futu_pads_and_prefixes():
    assert _hk_master_to_futu("HKEX:522") == "HK.00522"
    assert _hk_master_to_futu("HKEX:1304") == "HK.01304"
    assert _hk_master_to_futu("148") == "HK.00148"   # tolerate bare code
