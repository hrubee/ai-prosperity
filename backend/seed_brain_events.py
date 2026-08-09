"""Seed the AI-Vision log with REAL verdicts on the CURRENT charts (run on VPS).

The brain only writes BrainEvents on a fresh cross during market hours, so over
the weekend the admin panel would be empty until Monday. This renders each
instrument's current chart, runs one real Vision call, and records a BrainEvent
(reason prefixed "[seed]" so it's distinguishable from live decisions) — letting
us verify the panel layout / verdict badges / chart lightbox against real data
before clients are watching. Pruned away naturally as live events accumulate.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _le(p):
    if not os.path.exists(p):
        return
    for l in open(p):
        l = l.strip()
        if l and not l.startswith("#") and "=" in l:
            k, v = l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_le(os.path.join(_HERE, ".env"))

from app import brain_log, fno_instruments, kite_data, vision           # noqa: E402
from app.nifty_brain import (_ema_series, compute_cross, TIMEFRAME_MIN,  # noqa: E402
                             EMA_FAST, EMA_SLOW, SL_ATR_MULT, CHART_BARS, _fetch_count)

vision_on = vision.vision_available()
print(f"vision_available={vision_on}\n")
for inst in fno_instruments.active_instruments():
    try:
        nm = fno_instruments.resolve_near_month(inst)
        bars = kite_data.bars_for_token(nm.kite_token, TIMEFRAME_MIN, _fetch_count())
        closes = [b[4] for b in bars]
        fast, slow = _ema_series(closes, EMA_FAST), _ema_series(closes, EMA_SLOW)
        side = "buy" if fast and slow and fast[-1] >= slow[-1] else "sell"
        ref = closes[-1]
        atr = compute_cross(bars)["atr"]
        sl = round(ref - SL_ATR_MULT * atr, 1) if side == "buy" else round(ref + SL_ATR_MULT * atr, 1)

        chart = f"/tmp/fno_chart_{inst.key}.png"
        red_dots = vision.render_crossover_chart(
            bars, EMA_FAST, EMA_SLOW, chart, f"{inst.key} ({nm.tj_symbol})", CHART_BARS, TIMEFRAME_MIN)

        congested, reason, vsl, evaluated = None, None, None, False
        if vision_on:
            congested, reason, vsl = vision.vision_veto(nm.tj_symbol, chart, side, ref, CHART_BARS, TIMEFRAME_MIN)
            evaluated = True
        action = "vetoed" if (evaluated and congested) else "published"
        brain_log.record(
            instrument=inst.key, tj_symbol=nm.tj_symbol, side=side, ref_price=ref,
            sl_price=sl, atr=atr, red_dots=red_dots, vision_evaluated=evaluated,
            congested=congested, vision_reason=f"[seed] {reason}" if reason else "[seed] no vision",
            visual_sl=vsl, action=action, signal_id=None, chart_path=chart)
        print(f"{inst.key:11s} side={side} dots={red_dots} congested={congested} action={action}")
    except Exception as e:
        print(f"{inst.key:11s} FAIL: {e}")
print("\nseeded — check /admin/brain/events")
