"""Backtest the NIFTY straddle strategy on OUR OWN Kite data, in OUR backend.

Uses the SAME StraddleEngine that runs live, so this validates the exact engine
on real premiums. LIMIT: Kite only serves history for currently-live contracts, so
this can only cover the days the current live weekly expiry reaches (~1 week).
The full-year Rs.1.24L verdict needs AlgoTest's archived expired-option data; this
is our own-data, own-engine check over the window we can actually reach.

Run:  python -m app.straddle_backtest [history_days]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from .options_straddle import StraddleConfig, StraddleEngine
from .straddle_runner import discover_legs, _minute_candles, pick_closest_premium


def run(history_days: int = 14, target: float = 50.0) -> dict:
    cfg = StraddleConfig()
    chain = discover_legs()
    lot = chain["lot"]
    frm = (datetime.now() - timedelta(days=history_days)).strftime("%Y-%m-%d") + "+09:15:00"
    to = datetime.now().strftime("%Y-%m-%d") + "+15:30:00"

    # generous strike band around the recent NIFTY range so the ~Rs.50 strike is
    # always inside it (CE OTM above spot, PE OTM below).
    CE = {s: t for s, t in chain["CE"].items() if 23400 <= s <= 24600}
    PE = {s: t for s, t in chain["PE"].items() if 22400 <= s <= 23600}

    def load(book):
        out = {}
        for s, tok in sorted(book.items()):
            try:
                out[s] = _minute_candles(tok, frm, to)
            except Exception as e:
                print(f"  strike {s} err {e}", file=sys.stderr)
        return out
    CEd, PEd = load(CE), load(PE)

    # index by day -> strike -> {minute_dt: premium}
    def by_day(m):
        d = defaultdict(lambda: defaultdict(dict))
        for s, ser in m.items():
            for dt, p in ser:
                d[dt.date()][s][dt] = p
        return d
    CEday, PEday = by_day(CEd), by_day(PEd)

    def at935(daymap, s):
        for dt, p in daymap.get(s, {}).items():
            if (dt.hour, dt.minute) == cfg.entry_hm:
                return p
        return None

    def pick(daymap):
        prem = {s: at935(daymap, s) for s in daymap}
        return pick_closest_premium({s: p for s, p in prem.items() if p is not None}, target)

    days = sorted(set(CEday) & set(PEday))
    per_day = []
    total = 0.0
    for day in days:
        ce_s, pe_s = pick(CEday[day]), pick(PEday[day])
        if ce_s is None or pe_s is None:
            continue
        ce_ser, pe_ser = CEday[day][ce_s], PEday[day][pe_s]
        eng = StraddleEngine(cfg)
        last_ce = last_pe = None
        for t in sorted(set(ce_ser) | set(pe_ser)):
            last_ce = ce_ser.get(t, last_ce)
            last_pe = pe_ser.get(t, last_pe)
            if last_ce is None or last_pe is None:
                continue
            eng.on_minute(t, {"CE": last_ce, "PE": last_pe})
        pnl = eng.day_pnl(lot)
        total += pnl
        per_day.append({"date": str(day), "ce_strike": ce_s, "pe_strike": pe_s,
                        "ce_935": at935(CEday[day], ce_s), "pe_935": at935(PEday[day], pe_s),
                        "pnl": round(pnl, 2),
                        "ce_entries": eng.legs["CE"].entries, "pe_entries": eng.legs["PE"].entries})
    wins = sum(1 for d in per_day if d["pnl"] > 0)
    return {"expiry": chain["expiry"], "lot": lot, "days": len(per_day),
            "win_days": wins, "total_pnl": round(total, 2),
            "avg_per_day": round(total / len(per_day), 2) if per_day else 0,
            "per_day": per_day}


if __name__ == "__main__":
    hd = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    res = run(hd)
    print(f"\n=== OUR-DATA BACKTEST (NIFTY straddle, expiry {res['expiry']}, lot {res['lot']}) ===")
    print(f"{'date':12s} {'CE@935':>14s} {'PE@935':>14s} {'P&L (Rs)':>10s}  entries(CE/PE)")
    for d in res["per_day"]:
        ce = f"{d['ce_strike']:.0f}@Rs{(d['ce_935'] or 0):.0f}"
        pe = f"{d['pe_strike']:.0f}@Rs{(d['pe_935'] or 0):.0f}"
        print(f"{d['date']:12s} {ce:>14s} {pe:>14s} {d['pnl']:>10.0f}  CE:{d['ce_entries']} PE:{d['pe_entries']}")
    print(f"\n=== {res['days']} days | win days {res['win_days']}/{res['days']} | "
          f"TOTAL Rs.{res['total_pnl']:,.0f} | avg/day Rs.{res['avg_per_day']:,.0f} (1 lot each leg) ===")
    print("NOTE: small window (only the current live weekly expiry's data is reachable). "
          "This proves OUR engine on OUR data; the full-year Rs.1.24L verdict is AlgoTest's "
          "(it archives expired options, which Kite drops). No slippage modeled.")
