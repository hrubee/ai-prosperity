"""Split-sample robustness: does trail-loosening beat BASE in BOTH halves?"""
import glob
from app.straddle_backtest_tt import parse_file
from straddle_rr_sweep import evaluate

days = []
for path in sorted(glob.glob("/root/ttdata/*.csv")):
    try:
        for _d, dd in sorted(parse_file(path).items()):
            days.append(dd)
    except Exception:
        pass
h = len(days) // 2
halves = [("H1 (2021->early22)", days[:h]), ("H2 (2022)", days[h:])]
configs = [("BASE trail0.05", 0.05, 0.05), ("trail0.15", 0.05, 0.15),
           ("sl0.03+trail0.15", 0.03, 0.15)]
print("{} days; split {}/{}\n".format(len(days), h, len(days) - h))
print("{:20s} {:18s} {:>6} {:>6} {:>8} {:>10}".format("config", "half", "win%", "R:R", "exp/tr", "totalNet"))
print("-" * 72)
for name, sl, tr in configs:
    for hn, dd in halves:
        r = evaluate(dd, sl, tr)
        print("{:20s} {:18s} {:>5.1f}% {:>5.2f} {:>8.1f} {:>10,.0f}".format(
            name, hn, r["wr"] * 100, r["rr"], r["exp_net"], r["total"]))
    print()
