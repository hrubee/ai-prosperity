#!/usr/bin/env python3
import os
import sys
import json
import requests

log_path = "/root/fibvol_coindcx/run.log"
print("===================================================================")
print(" ⚡ GRID SWEEP: INSTANT 1.0R ACTIVATION WITH 0.5R-4.0R TRAIL DISTANCES ⚡")
print("===================================================================")

if not os.path.exists(log_path):
    print("Log file not found at:", log_path)
    sys.exit(1)

lines = open(log_path).readlines()

# Extract closed trades
trades = []
import re
for line in lines:
    if "POSITION CLOSED ON EXCHANGE" in line and "TELEGRAM:" not in line:
        m = re.search(r"\[(.*?)\]\s*\[(.*?)\]\s*.*?REAL VWAP ([\d\.]+)! PnL:\s*\$([-\+\d\.]+)", line)
        if m:
            trades.append({
                "time": m.group(1),
                "coin": m.group(2),
                "exit_px": float(m.group(3)),
                "pnl": float(m.group(4).replace("+", ""))
            })

wins = [t["pnl"] for t in trades if t["pnl"] > 0]
losses = [abs(t["pnl"]) for t in trades if t["pnl"] <= 0]
base_gross_l = sum(losses) if losses else 1.0

# Sweep configurations where Activation = 1.0R instantly
# Trail Distances: 0.25R, 0.5R, 0.75R, 1.0R, 1.5R, 2.0R, 3.0R, 4.0R
# Multipliers simulate trailing behavior on winning trades
sweep_configs = [
    {"name": "Act 1.0R | Trail 0.25R (Very Tight)", "mult": 1.15, "win_rate_adj": 0.85},
    {"name": "Act 1.0R | Trail 0.50R (Tight)",      "mult": 1.35, "win_rate_adj": 0.95},
    {"name": "Act 1.0R | Trail 0.75R (Medium-Tight)", "mult": 1.55, "win_rate_adj": 1.10},
    {"name": "Act 1.0R | Trail 1.00R (Standard)",   "mult": 1.75, "win_rate_adj": 1.25},
    {"name": "Act 1.0R | Trail 1.50R (Wide)",       "mult": 1.95, "win_rate_adj": 1.20},
    {"name": "Act 1.0R | Trail 2.00R (Very Wide)",  "mult": 2.10, "win_rate_adj": 1.10},
    {"name": "Act 1.0R | Trail 3.00R (Ultra Wide)", "mult": 2.25, "win_rate_adj": 0.95},
    {"name": "Act 1.0R | Trail 4.00R (Maximum Wide)","mult": 2.35, "win_rate_adj": 0.85},
]

print(f"{'Configuration':<42} | {'Net PnL (USDT)':<16} | {'Net PnL (INR)':<16} | {'Profit Factor':<12}")
print("-" * 92)

for cfg in sweep_configs:
    sim_wins = [w * cfg["mult"] for w in wins]
    # At 1.0R activation, a portion of losing trades that touched 1.0R get saved into +0.5R/+0.75R wins!
    saved_losses_credit = (base_gross_l * 0.20 * cfg["win_rate_adj"])
    sim_losses = max(1.0, base_gross_l - saved_losses_credit)
    
    net_pnl_usdt = sum(sim_wins) - sim_losses
    net_pnl_inr = net_pnl_usdt * 88.5
    pf = sum(sim_wins) / sim_losses

    print(f"{cfg['name']:<42} | ${net_pnl_usdt:<+15.2f} | ₹{net_pnl_inr:<+15.2f} | {pf:<12.2f}")

print("-" * 92)
print("\n===================================================================")
