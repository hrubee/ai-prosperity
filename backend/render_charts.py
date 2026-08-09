"""Render the current chart for every active F&O instrument (run on the VPS).
Render-only (no NVIDIA call) — these are the exact images the brain hands to
AI Vision on a fresh cross."""
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

from app import fno_instruments, kite_data, vision  # noqa: E402
from app.nifty_brain import (TIMEFRAME_MIN, EMA_FAST, EMA_SLOW,  # noqa: E402
                             CHART_BARS, _fetch_count)

for inst in fno_instruments.active_instruments():
    try:
        nm = fno_instruments.resolve_near_month(inst)
        bars = kite_data.bars_for_token(nm.kite_token, TIMEFRAME_MIN, _fetch_count())
        out = f"/tmp/fno_chart_{inst.key}.png"
        dots = vision.render_crossover_chart(bars, EMA_FAST, EMA_SLOW, out,
                                             f"{inst.key} {nm.tj_symbol}", CHART_BARS, TIMEFRAME_MIN)
        print(f"{inst.key}: {out} ({os.path.getsize(out)} bytes, {dots} crossovers, last={bars[-1][4]:.1f})")
    except Exception as e:
        print(f"{inst.key}: FAIL {e}")
