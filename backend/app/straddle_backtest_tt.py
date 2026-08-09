"""Backtest the NIFTY straddle on REAL historical premiums (TradingTuitions free
weekly-options dataset, 2021-2022). Each CSV = one weekly expiry's full minute
data for all strikes, so using it for that week is FAITHFUL (it IS the nearest
weekly). Runs the SAME StraddleEngine that trades live → a true own-engine,
own-data, multi-month backtest.

CSV cols: Ticker, Date/Time(dd-mm-YYYY HH:MM:SS), Open, High, Low, Close, Vol, OI.
NIFTY weekly option ticker: NIFTYWK<strike><CE|PE>.

Run: python -m app.straddle_backtest_tt /root/ttdata [lot]
"""
from __future__ import annotations

import csv
import glob
import re
import sys
from collections import defaultdict
from datetime import datetime

from .options_straddle import StraddleConfig, StraddleEngine

_SYM = re.compile(r"^NIFTYWK(\d+)(CE|PE)$")


def parse_file(path: str):
    """day -> {'CE': {strike: {dt: close}}, 'PE': {...}} for NIFTY weekly options."""
    days = defaultdict(lambda: {"CE": defaultdict(dict), "PE": defaultdict(dict)})
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 6:
                continue
            m = _SYM.match(row[0])
            if not m:
                continue
            try:
                dt = datetime.strptime(row[1].strip(), "%d-%m-%Y %H:%M:%S")
                close = float(row[5])
            except Exception:
                continue
            days[dt.date()][m.group(2)][float(m.group(1))][dt] = close
    return days


def run_day(dd, cfg, lot):
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
    ce_ser, pe_ser = dd["CE"][ce_s], dd["PE"][pe_s]
    eng = StraddleEngine(cfg)
    last_ce = last_pe = None
    for t in sorted(set(ce_ser) | set(pe_ser)):
        last_ce = ce_ser.get(t, last_ce)
        last_pe = pe_ser.get(t, last_pe)
        if last_ce is None or last_pe is None:
            continue
        eng.on_minute(t, {"CE": last_ce, "PE": last_pe})
    n_rt = sum(len(lg.trades) for lg in eng.legs.values())  # round-trips that day
    return eng.day_pnl(lot), n_rt


def run(data_dir: str, lot: int = 50):
    cfg = StraddleConfig()
    rows = []  # (date, pnl)
    files = sorted(glob.glob(f"{data_dir}/*.csv"))
    for i, path in enumerate(files, 1):
        try:
            days = parse_file(path)
        except Exception as e:
            print(f"  skip {path}: {e}", file=sys.stderr)
            continue
        for day, dd in sorted(days.items()):
            res = run_day(dd, cfg, lot)
            if res is not None:
                rows.append((day, res[0], res[1]))
        print(f"  [{i}/{len(files)}] {path.split('/')[-1][:40]}: cumulative days={len(rows)}", file=sys.stderr)
    return rows


def report(rows, lot, charge=45.0):
    """charge = per-round-trip cost (INR) to deduct. ~Rs.45 for a 1-lot trader
    (Rs.40 fixed brokerage + ~Rs.5 taxes); the fixed brokerage amortises with size
    (≈Rs.15/lot at 4 lots, ≈Rs.6/lot at 40 lots)."""
    if not rows:
        print("no days"); return
    rows.sort()
    n = len(rows)
    gross = sum(p for _, p, _ in rows)
    total_rt = sum(rt for _, _, rt in rows)
    total_charges = total_rt * charge
    net_rows = [(d, p - rt * charge) for d, p, rt in rows]   # net daily series
    net = sum(p for _, p in net_rows)
    wins = [p for _, p in net_rows if p > 0]
    eq = peak = 0.0; mdd = 0.0
    for _, p in net_rows:
        eq += p; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    print(f"\n=== TT BACKTEST w/ CHARGES (NIFTY straddle, OUR engine, lot {lot}, "
          f"Rs.{charge:.0f}/round-trip) ===")
    print(f"  period: {rows[0][0]} -> {rows[-1][0]}  ({n} trading days)")
    print(f"  round-trips: {total_rt} ({total_rt/n:.1f}/day)")
    print(f"  GROSS:   Rs.{gross:,.0f}  (avg/day Rs.{gross/n:,.0f})")
    print(f"  CHARGES: -Rs.{total_charges:,.0f}  (avg/day -Rs.{total_charges/n:,.0f})")
    print(f"  NET:     Rs.{net:,.0f}  (avg/day Rs.{net/n:,.0f})   <-- real edge")
    print(f"  net win days {len(wins)} ({100*len(wins)/n:.1f}%)  best Rs.{max(p for _,p in net_rows):,.0f}  "
          f"worst Rs.{min(p for _,p in net_rows):,.0f}  maxDD Rs.{mdd:,.0f}  Ret/DD {net/abs(mdd) if mdd else 0:.1f}")
    mon = defaultdict(float); mc = defaultdict(int)
    for d, p in net_rows:
        k = f"{d.year}-{d.month:02d}"; mon[k] += p; mc[k] += 1
    print("  --- monthly NET ---")
    for k in sorted(mon):
        flag = "" if mon[k] > 0 else "  <-- LOSS"
        print(f"    {k}: Rs.{mon[k]:>8,.0f} ({mc[k]}d){flag}")


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "/root/ttdata"
    lot = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    rows = run(d, lot)
    # bracket the edge: 0 = gross, 15 = ~4-lot client (Rs.1L), 45 = 1-lot worst case
    levels = [float(sys.argv[3])] if len(sys.argv) > 3 else [0.0, 15.0, 45.0]
    for ch in levels:
        report(rows, lot, ch)
