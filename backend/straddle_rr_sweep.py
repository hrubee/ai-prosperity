"""R:R sweep — what does it take to push avg risk:reward toward 1:4, and at what
cost to win rate / total profit. Runs the SAME engine over the TT 2021-2022 data
for a grid of (sl_pct, trail_move), collecting per-TRADE P&L (not just per day) so
we get the true win/loss distribution per config. Analysis-only; touches nothing.

Run:  .venv/bin/python straddle_rr_sweep.py /root/ttdata [lot]
"""
import glob
import statistics as st
import sys

from app.options_straddle import StraddleConfig, StraddleEngine
from app.straddle_backtest_tt import parse_file

LOT = int(sys.argv[2]) if len(sys.argv) > 2 else 65
CHARGE = 45.0


def trades_for_day(dd, cfg):
    def at935(legs):
        out = {}
        for s, ser in legs.items():
            for dt, p in ser.items():
                if (dt.hour, dt.minute) == cfg.entry_hm:
                    out[s] = p
                    break
        return out
    ce935, pe935 = at935(dd["CE"]), at935(dd["PE"])
    if not ce935 or not pe935:
        return []
    ce_s = min(ce935, key=lambda s: abs(ce935[s] - cfg.target_premium))
    pe_s = min(pe935, key=lambda s: abs(pe935[s] - cfg.target_premium))
    ce_ser, pe_ser = dd["CE"][ce_s], dd["PE"][pe_s]
    eng = StraddleEngine(cfg)
    last_ce = last_pe = None
    for t in sorted(set(ce_ser) | set(pe_ser)):
        last_ce = ce_ser.get(t, last_ce)
        last_pe = pe_ser.get(t, last_pe)
        if last_ce is None or last_pe is None:
            continue
        eng.on_minute(t, {"CE": last_ce, "PE": last_pe})
    return [(r["exit"] - r["entry"]) * LOT for r in eng.trade_rows()]


def evaluate(days, sl, trail, mom=0.10):
    cfg = StraddleConfig(sl_pct=sl, trail_move=trail, momentum_pct=mom)
    pnl = []
    for dd in days:
        pnl.extend(trades_for_day(dd, cfg))
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    n = len(pnl)
    if not wins or not losses:
        return None
    aw, al = st.mean(wins), st.mean(losses)
    rr = aw / abs(al)
    decisive = len(wins) + len(losses)
    wr = len(wins) / decisive
    exp_net = (sum(pnl) - CHARGE * n) / n
    total_net = sum(pnl) - CHARGE * n
    return dict(n=n, wr=wr, aw=aw, al=al, rr=rr, exp_net=exp_net, total=total_net,
               wins=len(wins), losses=len(losses))


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "/root/ttdata"
    days = []
    for path in sorted(glob.glob(f"{data_dir}/*.csv")):
        try:
            for _day, dd in sorted(parse_file(path).items()):
                days.append(dd)
        except Exception as e:
            print(f"skip {path}: {e}", file=sys.stderr)
    print(f"loaded {len(days)} trading days @ lot {LOT}\n")

    grid = [
        ("BASE live (sl5/tr5)",   0.05, 0.05),
        ("sl 0.06 (relax a bit)", 0.06, 0.05),
        ("sl 0.07",               0.07, 0.05),
        ("sl 0.08",               0.08, 0.05),
        ("sl 0.10 (loose)",       0.10, 0.05),
        ("trail 0.10 (slow step)",0.05, 0.10),
        ("trail 0.15",            0.05, 0.15),
        ("sl 0.07 + trail 0.10",  0.07, 0.10),
        ("sl 0.08 + trail 0.15",  0.08, 0.15),
    ]
    hdr = f"{'config':24s} {'trades':>6} {'win%':>6} {'avgWin':>8} {'avgLoss':>8} {'R:R':>6} {'exp/tr':>8} {'totalNet':>11}"
    print(hdr); print("-" * len(hdr))
    for name, sl, trail in grid:
        r = evaluate(days, sl, trail)
        if r is None:
            print(f"{name:24s}  (no trades)"); continue
        star = "  <== >=4:1" if r["rr"] >= 4.0 else ""
        print(f"{name:24s} {r['n']:>6} {r['wr']*100:>5.1f}% {r['aw']:>8.0f} {r['al']:>8.0f} "
              f"{r['rr']:>5.2f} {r['exp_net']:>8.1f} {r['total']:>11,.0f}{star}")
    print("\nnote: R:R = avgWin/|avgLoss| (mean). exp/tr & totalNet are NET of "
          f"Rs.{CHARGE:.0f}/round-trip @ {LOT} qty. Higher R:R is only better if "
          "totalNet/exp stays up — watch the win% column fall as R:R rises.")


if __name__ == "__main__":
    main()
