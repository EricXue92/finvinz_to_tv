"""Big-gap ADR% bypass for morning-gap: gap >= adr_bypass_gap_percent relaxes
the ADR% floor to adr_bypass_min_percent.

Fixtures mirror the real 2026-09-03 pre-market scan, where SNOW gapped >5% on
earnings but was dropped every run at "ADR% 3.68% < 4.0%" — a low-volatility
large cap whose 20-day range can never clear the 4.0% global floor.
"""

import pandas as pd

from main import _filter_adr_percent

TODAY = pd.Timestamp("2026-09-03").date()


def _frame(ticker: str, adr_pct: float, bars: int = 21) -> pd.DataFrame:
    """yfinance batch shape with a constant daily range of `adr_pct`%.

    The last row is dated TODAY so `_trim_today` drops it, exactly as during
    a live intraday scan; 21 bars leave the 20 completed ones ADR needs.
    """
    idx = pd.date_range(end="2026-09-03", periods=bars, freq="B")
    close = [100.0] * bars
    high = [100.0 * (1 + adr_pct / 100)] * bars
    low = [100.0] * bars
    cols = pd.MultiIndex.from_product([[ticker], ["High", "Low", "Close"]])
    return pd.DataFrame(
        {(ticker, "High"): high, (ticker, "Low"): low, (ticker, "Close"): close},
        index=idx, columns=cols,
    )


def test_big_gap_relaxes_adr_floor_snow_shape():
    """SNOW 2026-09-03 shape: ADR% 3.68 < 4.0, but gap 12% >= 10% → kept at 3.0 floor."""
    data = _frame("SNOW", 3.68)
    kept = _filter_adr_percent(
        ["SNOW"], data, 4.0, 20, TODAY, single=False,
        gaps={"SNOW": 12.0}, bypass_gap_pct=10.0, bypass_min_pct=3.0,
    )
    assert kept == ["SNOW"]


def test_bypass_floor_still_enforced():
    """gap 12% but ADR% 2.5 < relaxed 3.0 floor → still dropped."""
    data = _frame("SLOW", 2.5)
    kept = _filter_adr_percent(
        ["SLOW"], data, 4.0, 20, TODAY, single=False,
        gaps={"SLOW": 12.0}, bypass_gap_pct=10.0, bypass_min_pct=3.0,
    )
    assert kept == []


def test_gap_below_bypass_threshold_keeps_base_floor():
    """gap 6% under the 10% trigger → base 4.0 floor still enforced."""
    data = _frame("SNOW", 3.68)
    kept = _filter_adr_percent(
        ["SNOW"], data, 4.0, 20, TODAY, single=False,
        gaps={"SNOW": 6.0}, bypass_gap_pct=10.0, bypass_min_pct=3.0,
    )
    assert kept == []


def test_no_bypass_args_reproduces_old_behavior():
    """EOD call sites pass no gap args — behavior must be byte-for-byte identical."""
    data = _frame("SNOW", 3.68)
    assert _filter_adr_percent(["SNOW"], data, 4.0, 20, TODAY, single=False) == []
    data_ok = _frame("HOOD", 5.2)
    assert _filter_adr_percent(
        ["HOOD"], data_ok, 4.0, 20, TODAY, single=False
    ) == ["HOOD"]


def test_bypass_disabled_when_threshold_zero_or_none():
    """bypass_gap_pct 0 / None both mean 'no exemption'."""
    data = _frame("SNOW", 3.68)
    for pct in (0, None):
        kept = _filter_adr_percent(
            ["SNOW"], data, 4.0, 20, TODAY, single=False,
            gaps={"SNOW": 12.0}, bypass_gap_pct=pct, bypass_min_pct=3.0,
        )
        assert kept == [], f"bypass_gap_pct={pct!r} must not relax the floor"


def test_ticker_missing_from_gaps_takes_strict_path():
    data = _frame("SNOW", 3.68)
    kept = _filter_adr_percent(
        ["SNOW"], data, 4.0, 20, TODAY, single=False,
        gaps={}, bypass_gap_pct=10.0, bypass_min_pct=3.0,
    )
    assert kept == []


def test_ticker_above_base_floor_kept_regardless_of_gap():
    """A normal high-ADR name passes with or without the bypass in play."""
    data = _frame("HOOD", 5.2)
    kept = _filter_adr_percent(
        ["HOOD"], data, 4.0, 20, TODAY, single=False,
        gaps={"HOOD": 6.0}, bypass_gap_pct=10.0, bypass_min_pct=3.0,
    )
    assert kept == ["HOOD"]


def test_config_exposes_adr_bypass_knobs():
    """[morning_gap] must ship the knobs with the agreed defaults, so a revert
    is a config edit rather than a code change."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    s = cfg["morning_gap"]
    assert s["adr_bypass_gap_percent"] == 10.0
    assert s["adr_bypass_min_percent"] == 3.0
