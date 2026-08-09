"""Simulate the live StraddleEngine on real days and print the blow-by-blow
system behavior (arm -> momentum entry -> stop / trail / square-off) + P&L.
Shows one TREND day (winner) and one CHOP day (like today) so the behavior is
visible. Uses the SAME engine that trades live, on real TT minute premiums."""
import glob
import sys

from app.options_straddle import StraddleConfig, StraddleEngine
from app.straddle_backtest_tt import parse_file

LOT = 65
CHG = 45.0
cfg = StraddleConfig()


def sim_day(dd):
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
        return None
    ce_s = min(ce935, key=lambda s: abs(ce935[s] - cfg.target_premium))
    pe_s = min(pe935, key=lambda s: abs(pe935[s] - cfg.target_premium))
    strike = {"CE": ce_s, "PE": pe_s}
    ce_ser, pe_ser = dd["CE"][ce_s], dd["PE"][pe_s]
    eng = StraddleEngine(cfg)
    last_ce = last_pe = None
    trace, entry_px = [], {}
    run = 0.0
    for t in sorted(set(ce_ser) | set(pe_ser)):
        last_ce = ce_ser.get(t, last_ce)
        last_pe = pe_ser.get(t, last_pe)
        if last_ce is None or last_pe is None:
            continue
        for a in eng.on_minute(t, {"CE": last_ce, "PE": last_pe}):
            leg = a["leg"]
            if a["side"] == "buy":
                entry_px[leg] = a["premium"]
                trace.append((t, "BUY ", leg, strike[leg], a["premium"], a["reason"], None, run))
            else:
                pl = (a["premium"] - entry_px.get(leg, a["premium"])) * LOT
                run += pl
                trace.append((t, "SELL", leg, strike[leg], a["premium"], a["reason"], pl, run))
    return dict(ce_s=ce_s, pe_s=pe_s, ce0=ce935[ce_s], pe0=pe935[pe_s],
                trace=trace, gross=eng.day_pnl(LOT),
                rt=sum(len(l.trades) for l in eng.legs.values()))


def show(title, r):
    print("\n" + "=" * 74)
    print(title)
    print("  09:35 ARM legs:  CE %.0f @Rs.%.1f   |   PE %.0f @Rs.%.1f   (~Rs.50 each)"
          % (r["ce_s"], r["ce0"], r["pe_s"], r["pe0"]))
    print("  %-6s %-4s %-3s %-7s %-8s %-10s %9s %9s" %
          ("time", "act", "leg", "strike", "premium", "reason", "legP&L", "dayP&L"))
    print("  " + "-" * 70)
    for (t, act, leg, strike, px, reason, pl, run) in r["trace"]:
        pls = "" if pl is None else "{:+,.0f}".format(pl)
        print("  {:<6} {:<4} {:<3} {:<7.0f} Rs.{:<5.1f} {:<10} {:>9} {:>9}".format(
            t.strftime("%H:%M"), act, leg, strike, px, reason, pls, "{:+,.0f}".format(run)))
    net = r["gross"] - CHG * r["rt"]
    print("  " + "-" * 70)
    print("  RESULT:  {} round-trips  |  GROSS Rs.{:+,.0f}  |  charges -Rs.{:,.0f}  |  NET Rs.{:+,.0f}".format(
        r["rt"], r["gross"], CHG * r["rt"], net))


def main():
    days = []
    for path in sorted(glob.glob("/root/ttdata/*.csv")):
        try:
            for d, dd in sorted(parse_file(path).items()):
                r = sim_day(dd)
                if r:
                    days.append((d, r))
        except Exception:
            pass
    # pick the biggest winner and a representative chop/loss day
    winner = max(days, key=lambda x: x[1]["gross"])
    loser = min(days, key=lambda x: x[1]["gross"])
    show("TREND DAY (a winner) — %s" % winner[0], winner[1])
    show("CHOP DAY (whipsaw, like today) — %s" % loser[0], loser[1])


if __name__ == "__main__":
    main()
