"""This week's straddle P&L from the DB, per day, at 1 lot vs 2 lots.
Gross = sum((exit_px-entry_px)*qty) from recorded fills. Charges modelled at
Rs.40 flat brokerage + Rs.5/lot taxes per round-trip -> 1 lot = Rs.45, 2 lots = Rs.50.
2-lot gross = 2x (linear in qty); charges grow only Rs.5/round-trip. Read-only."""
import os
import sys
from collections import defaultdict

HERE = "/root/aiprosperity/backend"
for _l in open(os.path.join(HERE, ".env")):
    _l = _l.strip()
    if _l and not _l.startswith("#") and "=" in _l:
        _k, _v = _l.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
sys.path.insert(0, HERE)

from sqlalchemy import select
from app.db import session_scope
from app.models import StraddlePosition

CH1, CH2 = 45.0, 50.0  # per-round-trip charge at 1 / 2 lots


def main():
    with session_scope() as db:
        rows = db.execute(select(StraddlePosition).order_by(
            StraddlePosition.trade_date)).scalars().all()
    days = defaultdict(lambda: {"n": 0, "gross": 0.0, "w": 0, "l": 0})
    for r in rows:
        if r.entry_px is None or r.exit_px is None or r.trade_date is None:
            continue
        d = str(r.trade_date)
        if d < "2026-06-08":
            continue
        pnl = (r.exit_px - r.entry_px) * (r.qty or 0)
        days[d]["n"] += 1
        days[d]["gross"] += pnl
        days[d]["w"] += pnl > 0
        days[d]["l"] += pnl < 0

    print("host {}  ({} days >= 2026-06-08)".format(os.uname().nodename, len(days)))
    print("{:<12} {:>4} {:>4} {:>10} {:>10} | {:>10} {:>10}".format(
        "date", "#T", "W/L", "gross1L", "net1L", "gross2L", "net2L"))
    print("-" * 70)
    tn = tg1 = tn1 = tg2 = tn2 = 0
    for d in sorted(days):
        x = days[d]
        net1 = x["gross"] - x["n"] * CH1
        gross2 = x["gross"] * 2
        net2 = gross2 - x["n"] * CH2
        print("{:<12} {:>4d} {:>2d}/{:<2d} {:>+10.0f} {:>+10.0f} | {:>+10.0f} {:>+10.0f}".format(
            d, x["n"], x["w"], x["l"], x["gross"], net1, gross2, net2))
        tn += x["n"]; tg1 += x["gross"]; tn1 += net1; tg2 += gross2; tn2 += net2
    print("-" * 70)
    print("{:<12} {:>4d} {:>5} {:>+10.0f} {:>+10.0f} | {:>+10.0f} {:>+10.0f}".format(
        "TOTAL", tn, "", tg1, tn1, tg2, tn2))


if __name__ == "__main__":
    main()
