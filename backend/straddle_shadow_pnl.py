"""Reconstruct today's SHADOW (paper) straddle P&L from the runner's journal.
Shadow trades are logged but not persisted to the DB, so we pair BUY->SELL per leg
straight from the log. Gross = (sell-buy)*lot; net models Rs.45/round-trip (1 lot)."""
import re
import subprocess

LOT = 65
CH = 45.0

out = subprocess.run(
    ["journalctl", "-u", "straddle-shadow.service", "--since", "today", "--no-pager", "-o", "cat"],
    capture_output=True, text=True).stdout

pos = {}
trades = []
for line in out.splitlines():
    if "[LIVE]" in line:   # ignore any real orders (there shouldn't be any)
        continue
    m = re.search(r"straddle (BUY|SELL)\s+(CE|PE)\s+@Rs([0-9.]+)", line)
    if not m:
        continue
    act, leg, px = m.group(1), m.group(2), float(m.group(3))
    if act == "BUY":
        pos[leg] = px
    elif leg in pos:
        b = pos.pop(leg)
        trades.append((leg, b, px))

gross = sum((s - b) * LOT for _, b, s in trades)
wins = sum(1 for _, b, s in trades if s > b)
losses = sum(1 for _, b, s in trades if s < b)
be = sum(1 for _, b, s in trades if s == b)
net = gross - CH * len(trades)

print("SHADOW paper P&L today (from runner log, no real orders)")
print("round-trips: {}   W:{}  L:{}  BE:{}".format(len(trades), wins, losses, be))
print("GROSS: Rs.{:+.0f}".format(gross))
print("NET (after Rs.45/round-trip charges): Rs.{:+.0f}".format(net))
if pos:
    print("currently OPEN (paper):", ", ".join("{} @Rs{}".format(k, v) for k, v in pos.items()))
print("-- legs --")
for leg, b, s in trades:
    print("  {} {:.1f} -> {:.1f}  = Rs.{:+.0f}".format(leg, b, s, (s - b) * LOT))
