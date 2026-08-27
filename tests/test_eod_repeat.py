"""EOD Repeat: 已在跨日 master 里、今天又命中事件类 Longs 子组的老票。"""

from main import _repeat_hits


def test_all_new_tickers_yield_no_repeats():
    assert _repeat_hits("[Repeat/gap_up]", ["AAA", "BBB"], {"ZZZ"}) == []


def test_all_seen_tickers_are_all_repeats():
    seen = {"AAA", "BBB"}
    assert _repeat_hits("[Repeat/gap_up]", ["AAA", "BBB"], seen) == ["AAA", "BBB"]


def test_mixed_returns_only_the_seen_subset_in_input_order():
    seen = {"BBB", "DDD"}
    assert _repeat_hits(
        "[Repeat/high_volume]", ["AAA", "BBB", "CCC", "DDD"], seen
    ) == ["BBB", "DDD"]


def test_does_not_mutate_the_master_set():
    seen = {"BBB"}
    _repeat_hits("[Repeat/earnings_gap]", ["AAA", "BBB"], seen)
    assert seen == {"BBB"}


def test_empty_candidate_list_is_empty():
    assert _repeat_hits("[Repeat/top_gainers]", [], {"AAA"}) == []


def test_config_repeat_keys_all_match_a_real_longs_group():
    """A typo'd key would silently collect nothing — catch it here."""
    import tomllib
    from pathlib import Path

    cfg = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "config.toml").read_text("utf-8")
    )
    longs_keys = {g["key"] for g in cfg["longs"]}
    assert set(cfg["eod_repeat"]["keys"]) <= longs_keys
