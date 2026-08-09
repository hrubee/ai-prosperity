"""Money-math invariant for the F&O brain (pure, no network).

The basis bug: the brain used to compute ref_price/sl_price on the spot INDEX
while clients trade the FUTURE (135-400 pt basis), so the placed stop sat
hundreds of points from the real fill and clients ate multiples of 2%. The fix
drives every calculation off the future's own bars. These tests pin the
invariant that makes 2% hold:

    sl distance PUBLISHED  ==  distance SIZED against  ==  distance PLACED,
    and all three live in the bars' (future) price domain.

Run:  .venv/bin/python -m pytest tests/test_nifty_brain.py   (or run the file)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.nifty_brain import build_signal, SL_ATR_MULT, EMA_SLOW  # noqa: E402
from app.sizing import fno_lots  # noqa: E402
from app.config import settings  # noqa: E402


def _bars_with_fresh_cross(base: float, up: bool) -> list[list]:
    """Build a future-price bar series that produces a FRESH EMA cross on the
    last bar: a long flat-but-wiggling history (closes constant → EMA_fast ==
    EMA_slow, but high/low give a non-zero ATR) then one decisive jump."""
    bars = []
    ts = 1_700_000_000_000
    step = 5 * 60 * 1000
    n_flat = EMA_SLOW + 40
    for i in range(n_flat):
        bars.append([ts + i * step, base, base + 15, base - 15, base])  # close == base
    jump = 100.0 if up else -100.0
    c = base + jump
    hi = max(base, c) + 10
    lo = min(base, c) - 5
    bars.append([ts + n_flat * step, base, hi, lo, c])
    return bars


def test_buy_invariant_future_domain():
    base = 23744.0  # NIFTY *future* level (index is ~23609 — basis ~135)
    bars = _bars_with_fresh_cross(base, up=True)
    r = build_signal(bars)

    assert r["signal"] == "buy", r
    # ref_price is the LAST FUTURE BAR's close — same domain as the data, not the index.
    assert r["ref_price"] == bars[-1][4]
    assert r["atr"] > 0
    # SL sits exactly SL_ATR_MULT * ATR BELOW ref, in future points.
    dist = round(SL_ATR_MULT * r["atr"], 1)
    assert abs((r["ref_price"] - r["sl_price"]) - dist) < 0.2, r
    assert r["sl_price"] < r["ref_price"]


def test_sell_invariant_future_domain():
    base = 54770.0  # BANKNIFTY future (index ~54365 — basis ~405, the worst case)
    bars = _bars_with_fresh_cross(base, up=False)
    r = build_signal(bars)

    assert r["signal"] == "sell", r
    assert r["ref_price"] == bars[-1][4]
    dist = round(SL_ATR_MULT * r["atr"], 1)
    assert abs((r["sl_price"] - r["ref_price"]) - dist) < 0.2, r
    assert r["sl_price"] > r["ref_price"]


def test_sizing_realizes_two_percent_not_more():
    """The number the brain publishes is the number the executor sizes against
    AND places — so a stopped-out trade loses <= 2% (whole-lot floor), never the
    ~5.6% the basis bug produced."""
    base, equity, lot = 23744.0, 800_000.0, 65  # NIFTY future, ₹8L margin, lot 65
    r = build_signal(_bars_with_fresh_cross(base, up=True))
    ref, sl = r["ref_price"], r["sl_price"]

    lots, qty = fno_lots(equity, ref, sl, lot)
    assert lots >= 1 and qty == lots * lot, (lots, qty)

    placed_distance = abs(ref - sl)            # what the executor places on the future
    real_risk = qty * placed_distance          # loss if the stop fills at the placed level
    budget = equity * settings.risk_per_trade  # the 2% line
    assert real_risk <= budget, (real_risk, budget)
    # and we're using a meaningful chunk of the budget (whole-lot rounding only).
    assert real_risk >= budget - placed_distance * lot


def test_basis_bug_would_have_blown_the_budget():
    """Regression guard: had we kept publishing INDEX prices while the client
    trades the future, the real stop distance = sized distance + basis, inflating
    risk far past 2%. This asserts the OLD behavior was unsafe (documents why the
    fix matters); the live brain no longer does this."""
    equity, lot, basis = 800_000.0, 65, 135.0  # NIFTY basis ~135 pts
    index_base = 23609.0
    r = build_signal(_bars_with_fresh_cross(index_base, up=True))
    ref_idx, sl_idx = r["ref_price"], r["sl_price"]          # index-domain (the bug)
    sized_distance = abs(ref_idx - sl_idx)

    lots, qty = fno_lots(equity, ref_idx, sl_idx, lot)       # sized on index distance
    # client actually fills the FUTURE ~basis above the index; the index-level SL
    # is `basis` farther away than what was sized.
    real_distance = sized_distance + basis
    real_risk = qty * real_distance
    budget = equity * settings.risk_per_trade
    assert real_risk > budget * 2.0, (real_risk, budget)     # >2x the 2% — the bug


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
