"""Candlestick pattern detector invariants (pure, no network).

Covers every pattern in candle pattern.md: each builds a synthetic series with
the required prior trend + the precise candle geometry and asserts the right
pattern fires (and that wrong-trend variants do NOT). Builders make the trend
purely from the prefix closes so the pattern candles never bias the trend read.

Run:  .venv/bin/python -m pytest tests/test_candle_patterns.py   (or run the file)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import candle_patterns as cp  # noqa: E402

_TS0 = 1_600_000_000_000
_DAY = 86_400_000


def _series(prefix_closes, pattern_rows):
    """Build raw [ts,o,h,l,c,v] rows: a trend prefix (closes only matter) plus the
    explicit pattern candles appended at the end."""
    rows = []
    for i, c in enumerate(prefix_closes):
        # modest bearish-looking bar around the close; only the close drives trend
        rows.append([_TS0 + i * _DAY, c + 0.6, c + 0.9, c - 0.6, c, 1000])
    base = len(prefix_closes)
    for j, r in enumerate(pattern_rows):
        o, h, l, c = r[:4]
        v = r[4] if len(r) > 4 else 1000
        rows.append([_TS0 + (base + j) * _DAY, o, h, l, c, v])
    return rows


def _down(n=13, top=120.0, bottom=100.0):
    step = (top - bottom) / (n - 1)
    return [top - step * i for i in range(n)]  # ends at `bottom`


def _up(n=13, bottom=100.0, top=120.0):
    step = (top - bottom) / (n - 1)
    return [bottom + step * i for i in range(n)]  # ends at `top`


def _flat(n=13, px=100.0):
    return [px + (0.1 if i % 2 else -0.1) for i in range(n)]  # <3% move ⇒ "flat"


def _codes(candles_raw):
    return {m.code for m in cp.detect_all(cp.to_candles(candles_raw))}


def _matches(candles_raw):
    return {m.code: m for m in cp.detect_all(cp.to_candles(candles_raw))}


# ── one-candle ──────────────────────────────────────────────
# Hammer = body in the BOTTOM 40% / long UPPER wick; Hanging Man = body in the TOP
# 40% / long LOWER wick.
_HAMMER_SHAPE = (100.0, 106.0, 98.8, 99.0, 2500)   # body low, long upper wick
_HANGING_SHAPE = (120.0, 120.8, 114.0, 120.5)      # body high, long lower wick


def test_hammer_in_downtrend():
    rows = _series(_down(), [_HAMMER_SHAPE])
    m = _matches(rows)
    assert "hammer" in m
    assert m["hammer"].direction == "bullish" and m["hammer"].action == "buy"
    assert m["hammer"].trend == "down"
    assert m["hammer"].volume_confirm is True  # 2500 > recent ~1000 avg


def test_hanging_man_in_uptrend():
    rows = _series(_up(), [_HANGING_SHAPE])
    codes = _codes(rows)
    assert "hanging_man" in codes
    assert "hammer" not in codes
    assert _matches(rows)["hanging_man"].action == "short"


def test_hammer_shape_in_uptrend_fires_nothing():
    # Hammer shape (body low) in an UPtrend is neither a hammer (needs downtrend)
    # nor a hanging man (which needs the body high) — so nothing fires.
    rows = _series(_up(), [(120.0, 126.0, 118.8, 119.0)])
    codes = _codes(rows)
    assert "hammer" not in codes and "hanging_man" not in codes


def test_hammer_shape_in_flat_trend_fires_nothing():
    rows = _series(_flat(), [_HAMMER_SHAPE])
    assert _codes(rows) == set()


def test_doji_in_downtrend():
    rows = _series(_down(), [(100.0, 102.0, 98.0, 100.05)])
    m = _matches(rows)
    assert "doji_downtrend" in m and m["doji_downtrend"].direction == "bullish"
    assert "hammer" not in m  # body too small to be a hammer


def test_doji_in_uptrend():
    rows = _series(_up(), [(120.0, 122.0, 118.0, 120.05)])
    m = _matches(rows)
    assert "doji_uptrend" in m and m["doji_uptrend"].direction == "bearish"


# ── two-candle ──────────────────────────────────────────────
def test_bullish_engulfing():
    # cur's high/low engulf the prior candle's high/low; cur is green ⇒ bullish
    rows = _series(_down(), [(99.5, 102.3, 99.3, 102.0)])
    m = _matches(rows)
    assert "bullish_engulfing" in m and m["bullish_engulfing"].action == "buy"


def test_bearish_engulfing():
    # cur's high/low engulf the prior candle's; cur is red ⇒ bearish
    rows = _series(_up(), [(119.0, 120.2, 118.8, 120.0), (120.5, 120.7, 117.8, 118.0)])
    m = _matches(rows)
    assert "bearish_engulfing" in m and m["bearish_engulfing"].action == "short"


def test_engulfing_is_range_based_and_ignores_prior_colour():
    # Prior candle is GREEN (old rule needed it red); cur is a green candle whose
    # high/low engulf the prior range ⇒ still a bullish engulfing under the new rule.
    rows = _series(_down(), [(99.5, 100.1, 99.4, 100.0), (99.3, 102.2, 99.0, 102.0)])
    assert "bullish_engulfing" in _codes(rows)


def test_engulfing_requires_range_engulf_not_just_body():
    # cur's BODY engulfs the prior body, but its high (100.48) doesn't clear the
    # prior high (100.5) — range doesn't engulf ⇒ NOT an engulfing under the new rule.
    rows = _series(_down(), [(100.4, 100.5, 99.9, 100.0), (99.95, 100.48, 99.92, 100.45)])
    assert "bullish_engulfing" not in _codes(rows)


def test_bullish_piercing():
    # prev red; cur gaps down then closes above the prior body midpoint but below its open
    rows = _series(_down(), [(102.0, 102.2, 99.8, 100.0), (99.0, 101.4, 98.8, 101.2)])
    codes = _codes(rows)
    assert "bullish_piercing" in codes
    assert "bullish_engulfing" not in codes  # doesn't fully engulf


def test_bearish_piercing_dark_cloud():
    # prev green; cur gaps up then closes below the prior body midpoint but above its open
    rows = _series(_up(), [(119.0, 121.2, 118.8, 121.0), (122.0, 122.2, 119.6, 119.8)])
    codes = _codes(rows)
    assert "bearish_piercing" in codes
    assert "bearish_engulfing" not in codes


def test_bullish_harami():
    # decisive prior candle (colour irrelevant), then a RED harami candle (open>close)
    # whose range sits inside it ⇒ bullish harami, in a downtrend
    rows = _series(_down(), [(98.0, 104.5, 97.5, 104.0), (101.0, 101.3, 99.8, 100.0)])
    m = _matches(rows)
    assert "bullish_harami" in m and m["bullish_harami"].action == "buy"


def test_bearish_harami():
    # decisive prior candle, then a GREEN harami candle (close>open) whose range sits
    # inside it ⇒ bearish harami, in an uptrend (anchored at the top for the gate)
    rows = _series(_up(), [(121.0, 121.5, 113.5, 114.0), (115.5, 118.3, 115.2, 118.0)])
    m = _matches(rows)
    assert "bearish_harami" in m and m["bearish_harami"].action == "short"


def test_harami_is_range_inside_ignoring_prior_colour_and_direction_by_candle():
    # A GREEN harami candle inside a decisive prior, in an UPtrend ⇒ BEARISH harami,
    # regardless of the prior candle's colour.
    rows = _series(_up(), [(121.0, 121.5, 113.5, 114.0), (115.5, 118.3, 115.2, 118.0)])
    m = _matches(rows)
    assert "bearish_harami" in m and "bullish_harami" not in m


def test_harami_requires_range_inside():
    # Decisive prior + red harami-coloured candle, but its high pokes ABOVE the prior
    # high ⇒ range not inside ⇒ not a harami.
    rows = _series(_down(), [(98.0, 104.5, 97.5, 104.0), (103.0, 105.0, 99.0, 102.0)])
    assert "bullish_harami" not in _codes(rows)


# ── three-candle ────────────────────────────────────────────
def test_morning_star():
    rows = _series(
        _down(),
        [(110.0, 110.5, 99.5, 100.0), (99.0, 99.5, 97.0, 98.5), (99.5, 107.5, 99.0, 107.0)],
    )
    m = _matches(rows)
    assert "morning_star" in m and m["morning_star"].action == "buy"
    assert m["morning_star"].candles == 3


def test_evening_star():
    rows = _series(
        _up(),
        [(112.0, 120.5, 111.5, 120.0), (121.0, 122.0, 120.5, 121.5), (120.5, 121.0, 112.5, 113.0)],
    )
    m = _matches(rows)
    assert "evening_star" in m and m["evening_star"].action == "short"


# ── result shape / levels ───────────────────────────────────
def test_match_levels_and_serialization():
    rows = _series(_down(), [(100.0, 106.0, 98.8, 99.0)])  # hammer: body low, long upper wick
    m = _matches(rows)["hammer"]
    d = m.to_dict()
    assert d["pattern"] == "Hammer"
    assert d["confirmation_required"] is True
    # bullish: trigger = pattern high, stop = pattern low
    assert d["trigger"] == round(m.pattern_high, 2)
    assert d["stop_suggest"] == round(m.pattern_low, 2)
    assert d["last_close"] == 99.0


def test_too_short_history_is_safe():
    # fewer than the trend lookback ⇒ trend "unknown" ⇒ no reversal fires, no crash
    rows = _series([100.0, 99.0, 98.0], [(98.0, 99.3, 92.0, 99.0)])
    assert _codes(rows) == set()


# ── swing-extreme gate ──────────────────────────────────────
def test_at_swing_helpers():
    cs = cp.to_candles([[0, 10, 11, 9, 10], [1, 9, 10, 8, 9], [2, 8.5, 9, 8, 8.5]])
    assert cp.at_swing_low(cs, 8.1) is True      # within 3% of the 8.0 low
    assert cp.at_swing_low(cs, 9.0) is False     # too far above the low
    assert cp.at_swing_high(cs, 10.8) is True    # within 3% of the 11.0 high
    assert cp.at_swing_high(cs, 9.0) is False


def test_extreme_gate_filters_non_bottom_hammer():
    # A textbook hammer in a downtrend, BUT a much lower low sits in the 20-bar
    # window — it's a mid-trend bounce, not a bottom, so the gate drops it.
    rows = [[_TS0, 100.0, 101.0, 60.0, 100.0, 1000]]  # deep low (60) 13 bars back
    ts = _TS0 + _DAY
    for c in [106, 105.5, 105, 104.5, 104, 103.5, 103, 102.5, 102, 101.5, 101, 100]:
        rows.append([ts, c + 0.5, c + 0.7, c - 0.5, c, 1000]); ts += _DAY
    rows.append([ts, 100.0, 106.0, 99.0, 99.3, 1000])  # valid hammer shape (body low, long upper) at ~100
    assert "hammer" not in _codes(rows)


# ── volatility-relative "long" (the harami fix) ─────────────
def test_harami_rejects_tiny_prior_candle():
    # A red harami candle sits inside the prior bar, but the prior bar is tiny vs.
    # recent volatility (not "decisive") — so it's not a valid harami's first candle.
    rows = _series(_down(), [(100.0, 100.1, 99.6, 99.7), (99.95, 99.98, 99.75, 99.80)])
    assert "bullish_harami" not in _codes(rows)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
