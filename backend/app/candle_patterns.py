"""Candlestick pattern detection for the "AAA" daily-setup scanner.

Implements every pattern in ``candle pattern.md`` on a chronological list of
daily OHLCV candles. Pure functions only — no I/O, no network — so the detectors
are unit-testable in CI without Kite. The scanner (``aaa_scanner``) feeds it the
candles it pulls from Kite.

The reference workflow is: (1) identify the trend → (2) identify the pattern →
(3) decide buy/short. Every reversal pattern therefore only fires when it sits in
the trend it's supposed to reverse (a hammer in a downtrend, a hanging man in an
uptrend, …). We classify the trend leading *into* the pattern, then gate each
detector on it.

Patterns are detected on the MOST RECENT candle(s): a match means the pattern
*just completed* on the last candle the scanner passed in (yesterday's close when
the scan runs pre-open). Per the reference, almost every pattern wants
next-candle confirmation — which on a daily scan is today's not-yet-formed
candle — so detected setups are always "watch today for confirmation."
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── trend classification ───────────────────────────────────────
# A reversal pattern needs a real move into it. We measure the net % move of the
# closes over `lookback` trading days ending on the candle immediately BEFORE the
# pattern starts (so the pattern's own candles never bias the trend read), and
# label it down/up when that move clears `threshold`. Simple, explainable, and
# resistant to a single-bar wick — exactly what a daily swing scan wants.
TREND_LOOKBACK = 10
TREND_THRESHOLD = 0.03  # 3% net move over the lookback window
# Volume confirmation baseline (pattern-candle volume vs. its own recent average).
VOL_PERIOD = 20
DOJI_BODY_FRAC = 0.05    # body ≤ 5% of range ⇒ open≈close ⇒ doji (a true cross/plus)
# Hammer vs Hanging Man are distinguished by WHERE the body sits in the high–low
# range. Hammer: body in the BOTTOM 40% (long UPPER wick), in a downtrend → bullish.
# Hanging Man: body in the TOP 40% (long LOWER wick), in an uptrend → bearish. The
# long wick must be ≥ 2× the body and the opposite wick little/none.
HAMMER_BODY_ZONE = 0.40       # body within the bottom 40% (hammer) / top 40% (hanging man)
HAMMER_WICK_MULT = 2.0        # the long wick ≥ 2× the body
HAMMER_SHORT_WICK_MAX = 1.0   # the short (opposite) wick ≤ this × body ("little/no")
SMALL_BODY_FRAC = 0.5    # "small" star body ≤ 50% of the large bodies it sits between
LONG_BODY_FRAC = 0.5     # a "long" candle's body ≥ 50% of its OWN range (a solid body)
# A candle is only "long/decisive" when its body is ALSO large vs. recent volatility
# (≥ this × the average high–low range of the prior RANGE_BASELINE candles). Without
# this, a tiny candle that merely fills its own small range counted as "long" — the
# bug that produced bogus haramis on low-volatility names/ETFs.
DECISIVE_RANGE_MULT = 0.8
RANGE_BASELINE = 14
# Swing-extreme gate: a reversal must sit AT the turning point, not just in a drift
# — bullish patterns near the recent low, bearish near the recent high.
EXTREME_LOOKBACK = 20
EXTREME_TOL = 0.03         # within 3% of the 20-bar low (bullish) / high (bearish)


@dataclass
class Candle:
    t: int      # epoch ms (candle open time)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


def to_candles(raw: list) -> list[Candle]:
    """Convert ``[[ts_ms, o, h, l, c, v?], …]`` rows into Candle objects."""
    out: list[Candle] = []
    for r in raw:
        if len(r) < 5:
            continue
        v = float(r[5]) if len(r) > 5 and r[5] is not None else 0.0
        out.append(Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), v))
    return out


# ── single-candle geometry helpers ─────────────────────────────
def _body(c: Candle) -> float:
    return abs(c.c - c.o)


def _rng(c: Candle) -> float:
    return c.h - c.l


def _upper(c: Candle) -> float:
    return c.h - max(c.o, c.c)


def _lower(c: Candle) -> float:
    return min(c.o, c.c) - c.l


def _bull(c: Candle) -> bool:
    return c.c > c.o


def _bear(c: Candle) -> bool:
    return c.c < c.o


def _body_mid(c: Candle) -> float:
    return (c.o + c.c) / 2.0


def _is_doji(c: Candle) -> bool:
    r = _rng(c)
    return r > 0 and _body(c) <= DOJI_BODY_FRAC * r


def _is_long(c: Candle) -> bool:
    r = _rng(c)
    return r > 0 and _body(c) >= LONG_BODY_FRAC * r


def _avg_range(candles: list[Candle], end_idx: int, period: int = RANGE_BASELINE) -> float:
    """Average high–low range of the `period` candles ending just before end_idx —
    the recent-volatility baseline. 0.0 when there's no prior history."""
    seg = candles[max(0, end_idx - period):end_idx]
    rs = [_rng(c) for c in seg if _rng(c) > 0]
    return sum(rs) / len(rs) if rs else 0.0


def _decisive(c: Candle, avg_range: float) -> bool:
    """A 'long'/decisive candle: a solid body (≥ half its OWN range) that is ALSO
    large vs. recent volatility (≥ DECISIVE_RANGE_MULT × avg_range). Falls back to
    the self-relative test only when no volatility baseline is available."""
    if not _is_long(c):
        return False
    return avg_range <= 0 or _body(c) >= DECISIVE_RANGE_MULT * avg_range


def at_swing_low(candles: list[Candle], pattern_low: float,
                 lookback: int = EXTREME_LOOKBACK, tol: float = EXTREME_TOL) -> bool:
    """Is the pattern's low within `tol` of the lowest low of the last `lookback`
    candles — i.e. is this a bottom, not a mid-trend dip?"""
    window = candles[-lookback:] if len(candles) >= lookback else candles
    if not window:
        return True
    lo = min(c.l for c in window)
    return pattern_low <= lo * (1 + tol)


def at_swing_high(candles: list[Candle], pattern_high: float,
                  lookback: int = EXTREME_LOOKBACK, tol: float = EXTREME_TOL) -> bool:
    """Is the pattern's high within `tol` of the highest high of the last `lookback`
    candles — i.e. is this a top?"""
    window = candles[-lookback:] if len(candles) >= lookback else candles
    if not window:
        return True
    hi = max(c.h for c in window)
    return pattern_high >= hi * (1 - tol)


def classify_trend(candles: list[Candle], pattern_start: int,
                   lookback: int = TREND_LOOKBACK,
                   threshold: float = TREND_THRESHOLD) -> tuple[str, float]:
    """Trend of the run leading into the pattern that starts at index
    ``pattern_start``. Returns ("up"|"down"|"flat"|"unknown", net_move_fraction).
    "unknown" when there isn't enough history before the pattern."""
    ref = pattern_start - 1          # last candle before the pattern
    base = ref - lookback
    if ref < 0 or base < 0:
        return "unknown", 0.0
    start_px = candles[base].c
    if start_px <= 0:
        return "unknown", 0.0
    move = (candles[ref].c - start_px) / start_px
    if move <= -threshold:
        return "down", move
    if move >= threshold:
        return "up", move
    return "flat", move


def _volume_confirm(candles: list[Candle], idx: int, period: int = VOL_PERIOD) -> bool | None:
    """Did the pattern's signal candle trade above its own recent average volume?
    Higher volume increases a pattern's reliability. None when volume is absent."""
    sig = candles[idx]
    if sig.v <= 0:
        return None
    base = candles[max(0, idx - period):idx]
    vols = [b.v for b in base if b.v > 0]
    if not vols:
        return None
    avg = sum(vols) / len(vols)
    return sig.v > avg


# ── result ──────────────────────────────────────────────────────
@dataclass
class Match:
    code: str            # slug, e.g. "bullish_engulfing"
    name: str            # display name
    direction: str       # "bullish" | "bearish"
    action: str          # "buy" | "short"
    candles: int         # 1 | 2 | 3
    trend: str           # the trend it formed in
    trend_move_pct: float
    confirmed_required: bool  # True ⇒ next-candle confirmation needed before acting
    pattern_high: float
    pattern_low: float
    last_close: float
    # Actionable levels: confirmation trigger + protective stop suggestion.
    trigger: float       # price beyond which today confirms the reversal
    stop_suggest: float
    volume_confirm: bool | None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        # `pattern` (not `name`) holds the display label so the scanner can attach
        # the company `name` without clobbering it.
        return {
            "code": self.code,
            "pattern": self.name,
            "direction": self.direction,
            "action": self.action,
            "candles": self.candles,
            "trend": self.trend,
            "trend_move_pct": round(self.trend_move_pct * 100, 2),
            "confirmation_required": self.confirmed_required,
            "pattern_high": round(self.pattern_high, 2),
            "pattern_low": round(self.pattern_low, 2),
            "last_close": round(self.last_close, 2),
            "trigger": round(self.trigger, 2),
            "stop_suggest": round(self.stop_suggest, 2),
            "volume_confirm": self.volume_confirm,
            **self.extra,
        }


def _levels(span: list[Candle]) -> tuple[float, float]:
    """(high, low) across the candles that make up the pattern."""
    return max(c.h for c in span), min(c.l for c in span)


def _bull_match(code, name, ncandles, span, trend, move, last, vol, doji=False) -> Match:
    hi, lo = _levels(span)
    return Match(code, name, "bullish", "buy", ncandles, trend, move,
                 confirmed_required=True, pattern_high=hi, pattern_low=lo,
                 last_close=last.c, trigger=hi, stop_suggest=lo, volume_confirm=vol,
                 extra={"note": "watch today for a bullish confirmation candle"} if doji else {})


def _bear_match(code, name, ncandles, span, trend, move, last, vol, doji=False) -> Match:
    hi, lo = _levels(span)
    return Match(code, name, "bearish", "short", ncandles, trend, move,
                 confirmed_required=True, pattern_high=hi, pattern_low=lo,
                 last_close=last.c, trigger=lo, stop_suggest=hi, volume_confirm=vol,
                 extra={"note": "watch today for a bearish confirmation candle"} if doji else {})


# ── detectors ───────────────────────────────────────────────────
# Each returns a Match or None. They operate on the tail of `candles`: the
# pattern's final candle is always the last element.

def detect_one_candle(candles: list[Candle]) -> list[Match]:
    """Hammer / Hanging Man / Doji — the pattern is the single last candle."""
    n = len(candles)
    if n < 2:
        return []
    c = candles[-1]
    r = _rng(c)
    if r <= 0:
        return []
    trend, move = classify_trend(candles, n - 1)
    vol = _volume_confirm(candles, n - 1)
    out: list[Match] = []

    # Doji — indecision; only meaningful at a trend extreme, confirmation required.
    if _is_doji(c):
        if trend == "down":
            out.append(_bull_match("doji_downtrend", "Doji (downtrend)", 1, [c], trend, move, c, vol, doji=True))
        elif trend == "up":
            out.append(_bear_match("doji_uptrend", "Doji (uptrend)", 1, [c], trend, move, c, vol, doji=True))
        return out  # a doji can't simultaneously be a hammer (body too small)

    # Hammer vs Hanging Man — by body position. (Doji handled above ⇒ real body.)
    body = _body(c)
    upper, lower = _upper(c), _lower(c)
    # Hammer: body in the BOTTOM 40% of the range, long UPPER wick ≥ 2× body, little/
    # no lower wick — a bullish reversal in a downtrend.
    hammer = (body > 0
              and max(c.o, c.c) <= c.l + HAMMER_BODY_ZONE * r
              and upper >= HAMMER_WICK_MULT * body
              and lower <= HAMMER_SHORT_WICK_MAX * body)
    # Hanging Man: body in the TOP 40% of the range, long LOWER wick ≥ 2× body,
    # little/no upper wick — a bearish reversal in an uptrend.
    hanging = (body > 0
               and min(c.o, c.c) >= c.l + (1.0 - HAMMER_BODY_ZONE) * r
               and lower >= HAMMER_WICK_MULT * body
               and upper <= HAMMER_SHORT_WICK_MAX * body)
    if hammer and trend == "down":
        out.append(_bull_match("hammer", "Hammer", 1, [c], trend, move, c, vol))
    elif hanging and trend == "up":
        out.append(_bear_match("hanging_man", "Hanging Man", 1, [c], trend, move, c, vol))
    return out


def detect_two_candle(candles: list[Candle]) -> list[Match]:
    """Engulfing / Piercing / Harami — the prior + last candle."""
    n = len(candles)
    if n < 3:
        return []
    prev, cur = candles[-2], candles[-1]
    if _rng(prev) <= 0 or _rng(cur) <= 0:
        return []
    trend, move = classify_trend(candles, n - 2)
    vol = _volume_confirm(candles, n - 1)
    span = [prev, cur]
    out: list[Match] = []

    pmid = _body_mid(prev)

    # Engulfing — the current candle's RANGE engulfs the prior candle's range (its
    # high ≥ the prior high AND its low ≤ the prior low). The current candle's own
    # open/close sets the direction: close > open ⇒ bullish, open > close ⇒ bearish.
    # (The prior candle's colour is irrelevant.)
    range_engulfs = cur.h >= prev.h and cur.l <= prev.l
    # Bullish Engulfing — green candle engulfing the prior range, in a downtrend.
    if trend == "down" and range_engulfs and _bull(cur):
        out.append(_bull_match("bullish_engulfing", "Bullish Engulfing", 2, span, trend, move, cur, vol))
    # Bearish Engulfing — red candle engulfing the prior range, in an uptrend.
    elif trend == "up" and range_engulfs and _bear(cur):
        out.append(_bear_match("bearish_engulfing", "Bearish Engulfing", 2, span, trend, move, cur, vol))

    # Bullish Piercing — downtrend; green opens ≤ prior close (gap down) and closes
    # back above the midpoint of the prior red body but not above its open.
    if (trend == "down" and _bear(prev) and _bull(cur)
            and cur.o <= prev.c and cur.c > pmid and cur.c < prev.o):
        out.append(_bull_match("bullish_piercing", "Bullish Piercing", 2, span, trend, move, cur, vol))
    # Bearish Piercing (Dark Cloud Cover) — uptrend; red opens ≥ prior close
    # (gap up) and closes below the midpoint of the prior green body, above its open.
    elif (trend == "up" and _bull(prev) and _bear(cur)
          and cur.o >= prev.c and cur.c < pmid and cur.c > prev.o):
        out.append(_bear_match("bearish_piercing", "Bearish Piercing", 2, span, trend, move, cur, vol))

    # Harami — the current ("harami") candle's RANGE sits entirely INSIDE the prior
    # candle's range (high ≤ prior high AND low ≥ prior low). The prior must be a
    # decisive (large) candle. Direction is set by the harami candle's own open/close:
    # open > close (red) ⇒ bullish harami, close > open (green) ⇒ bearish harami.
    avg = _avg_range(candles, n - 2)
    range_inside = cur.h <= prev.h and cur.l >= prev.l
    # Bullish Harami — red harami candle inside a large prior candle, in a downtrend.
    if trend == "down" and _decisive(prev, avg) and range_inside and _bear(cur):
        out.append(_bull_match("bullish_harami", "Bullish Harami", 2, span, trend, move, cur, vol))
    # Bearish Harami — green harami candle inside a large prior candle, in an uptrend.
    elif trend == "up" and _decisive(prev, avg) and range_inside and _bull(cur):
        out.append(_bear_match("bearish_harami", "Bearish Harami", 2, span, trend, move, cur, vol))

    return out


def detect_three_candle(candles: list[Candle]) -> list[Match]:
    """Morning Star / Evening Star — three candles ending on the last."""
    n = len(candles)
    if n < 4:
        return []
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if min(_rng(c1), _rng(c3)) <= 0:
        return []
    trend, move = classify_trend(candles, n - 3)
    vol = _volume_confirm(candles, n - 1)
    span = [c1, c2, c3]
    out: list[Match] = []

    avg = _avg_range(candles, n - 3)
    small_star = _body(c2) <= SMALL_BODY_FRAC * _body(c1) and _body(c2) <= SMALL_BODY_FRAC * _body(c3)
    c1mid = _body_mid(c1)

    # Morning Star — downtrend: big red, small star sitting below it, big green that
    # closes at/above the midpoint of the first candle's body.
    if (trend == "down" and _bear(c1) and _decisive(c1, avg) and small_star and _bull(c3) and _decisive(c3, avg)
            and max(c2.o, c2.c) <= c1.c and c3.c >= c1mid and c3.c > max(c2.o, c2.c)):
        out.append(_bull_match("morning_star", "Morning Star", 3, span, trend, move, c3, vol))
    # Evening Star — uptrend: big green, small star above it, big red that closes
    # at/below the midpoint of the first candle's body.
    elif (trend == "up" and _bull(c1) and _decisive(c1, avg) and small_star and _bear(c3) and _decisive(c3, avg)
          and min(c2.o, c2.c) >= c1.c and c3.c <= c1mid and c3.c < min(c2.o, c2.c)):
        out.append(_bear_match("evening_star", "Evening Star", 3, span, trend, move, c3, vol))

    return out


def detect_all(candles: list[Candle]) -> list[Match]:
    """Run every detector on the tail of `candles` and return all matches whose
    final candle is the most recent candle. A reversal must also sit AT the turning
    point — bullish near the recent low, bearish near the recent high (swing-extreme
    gate) — so mid-trend bounces are dropped. Usually 0–1 per symbol; occasionally a
    couple of compatible patterns (e.g. engulfing + piercing) co-fire."""
    matches: list[Match] = []
    matches.extend(detect_three_candle(candles))
    matches.extend(detect_two_candle(candles))
    matches.extend(detect_one_candle(candles))
    out: list[Match] = []
    for m in matches:
        ok = (at_swing_low(candles, m.pattern_low) if m.direction == "bullish"
              else at_swing_high(candles, m.pattern_high))
        if ok:
            out.append(m)
    return out
