#!/usr/bin/env python3
import os
import sys
import re

log_path = "/root/fibvol_coindcx/run.log"
print("===================================================================")
print("  ⚡ AUDITING FIBVOL STRATEGY TRADES SPECIFICALLY FOR ACCOUNT 1 ⚡")
print("===================================================================")

if not os.path.exists(log_path):
    print("Log file not found at:", log_path)
    sys.exit(1)

lines = open(log_path).readlines()

# Extract all Acc 1 orders placed by fibvol (Acc 1 is the primary execution account)
acc1_orders = []
for line in lines:
    if "(Acc 1)" in line or "LIVE ORDER PLACED" in line or "LIVE LIMIT BRACKET" in line:
        if "(Acc 2)" not in line:
            m = re.search(r"\[(.*?)\]\s*\[(.*?)\]\s*.*?ID=(.*)", line)
            if m:
                acc1_orders.append({
                    "time": m.group(1),
                    "coin": m.group(2),
                    "order_id": m.group(3).strip()
                })

print(f"Total Bracket Orders Placed by FIBVOL for Account 1: {len(acc1_orders)}")

# Extract all closed positions for Account 1
closed_trades = []
for line in lines:
    if "POSITION CLOSED ON EXCHANGE" in line and "TELEGRAM:" not in line:
        m = re.search(r"\[(.*?)\]\s*\[(.*?)\]\s*.*?PnL:\s*\$([-\+\d\.]+)", line)
        if m:
            ts = m.group(1)
            coin = m.group(2)
            pnl_val = float(m.group(3).replace("+", ""))
            closed_trades.append({
                "time": ts,
                "coin": coin,
                "pnl": pnl_val,
                "raw": line.strip()
            })

print(f"Total Completed Strategy Trades for Account 1: {len(closed_trades)}")

if closed_trades:
    pnls = [t["pnl"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    total_usdt = sum(pnls)
    total_inr = total_usdt * 88.5
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    pf = (gross_w / gross_l) if gross_l > 0 else 0

    print("\n  --- ACCOUNT 1 FIBVOL PNL METRICS ---")
    print(f"  - Total Strategy Trades: {len(closed_trades)}")
    print(f"  - Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {win_rate:.1f}%")
    print(f"  - Gross Profit: +${gross_w:.2f} USDT")
    print(f"  - Gross Loss: -${gross_l:.2f} USDT")
    print(f"  - Profit Factor: {pf:.2f}")
    print(f"  - NET FIBVOL REALIZED PNL (USDT): ${total_usdt:+.2f} USDT")
    print(f"  - NET FIBVOL REALIZED PNL (INR @ 88.5): ₹{total_inr:+,.2f} INR")

    print("\n  --- ACCOUNT 1 FIBVOL TRADES HISTORY ---")
    for t in closed_trades:
        sign = "+" if t["pnl"] > 0 else ""
        print(f"  {t['time']} | {t['coin']:<10} | PnL: {sign}${t['pnl']:.2f}")

print("\n===================================================================")
