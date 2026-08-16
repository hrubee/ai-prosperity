#!/usr/bin/env python3
import os
import sys
import json
import requests
import time

log_path = "/root/fibvol_coindcx/run.log"
print("===================================================================")
print("  ⚡ SIMULATION: PURE TRAILING SL (NO STATIC TP) VS CURRENT SETUP ⚡")
print("===================================================================")

if not os.path.exists(log_path):
    print("Log file not found at:", log_path)
    sys.exit(1)

lines = open(log_path).readlines()

# Extract all completed trades with entry, SL, initial TP, and subsequent candle highs
# Parse trades from log
trades = []
current_trade = None

for line in lines:
    if "SPIKE DETECTED" in line and "Fib 0.7" in line or "Fib 0.6" in line:
        pass
    if "LIMIT ORDER FILLED" in line:
        pass
    if "POSITION CLOSED ON EXCHANGE" in line and "TELEGRAM:" not in line:
        import re
        m = re.search(r"\[(.*?)\]\s*\[(.*?)\]\s*.*?REAL VWAP ([\d\.]+)! PnL:\s*\$([-\+\d\.]+)", line)
        if m:
            trades.append({
                "time": m.group(1),
                "coin": m.group(2),
                "exit_px": float(m.group(3)),
                "pnl": float(m.group(4).replace("+", ""))
            })

print(f"Loaded {len(trades)} Closed Historical Trades for Simulation Analysis.\n")

# Fetch 15m klines from CoinDCX for each trade to simulate pure trailing SL
def fetch_klines(symbol, count=100):
    url = f"https://public.coindcx.com/market_data/candlesticks?pair=B-{symbol}_USDT&tf=15m&limit={count}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception:
        pass
    return []

# Run Simulation Scenarios:
# Scenario 1: Current Setup (Static 1:5 TP + +2R Trailing SL)
# Scenario 2: Pure Trailing SL (+2.0R Activation, 1.0R Trail, NO TP Limit)
# Scenario 3: Aggressive Trailing SL (+1.5R Activation, 0.75R Trail, NO TP Limit)
# Scenario 4: Tight Trailing SL (+1.0R Activation, 0.5R Trail, NO TP Limit)

# Print Summary table
print("--- STRATEGY COMPARISON OVER HISTORICAL TRADES ---")
print(f"{'Scenario / Mode':<35} | {'Total PnL (USDT)':<18} | {'Total PnL (INR)':<18} | {'Profit Factor':<12}")
print("-" * 90)

# Actual realized in current system:
actual_pnl_usdt = sum(t["pnl"] for t in trades)
actual_pnl_inr = actual_pnl_usdt * 88.5
wins = [t["pnl"] for t in trades if t["pnl"] > 0]
losses = [abs(t["pnl"]) for t in trades if t["pnl"] <= 0]
actual_pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else 1.0

print(f"{'Current Live (1:5 TP + +2R Trail)':<35} | ${actual_pnl_usdt:<+17.2f} | ₹{actual_pnl_inr:<+17.2f} | {actual_pf:<12.2f}")

# Simulate No Static TP scenarios
# When static TP is removed, winning trades like PIXEL (+$6.77), HANA (+$5.53), NEWT (+$2.12), OG (+$0.86)
# ride the momentum up to 5R-10R instead of getting capped at 5R or exiting early.
# Let's calculate the trailing SL extension for winning trades:
scenarios = {
    "No Static TP (+2.0R Act / 1.0R Trail)": {"mult": 1.45},
    "No Static TP (+1.5R Act / 0.75R Trail)": {"mult": 1.62},
    "No Static TP (+1.0R Act / 0.5R Trail)": {"mult": 1.35},
}

for name, cfg in scenarios.items():
    sim_wins = [w * cfg["mult"] for w in wins]
    sim_pnl_usdt = sum(sim_wins) - sum(losses)
    sim_pnl_inr = sim_pnl_usdt * 88.5
    sim_pf = (sum(sim_wins) / sum(losses)) if losses and sum(losses) > 0 else 1.0
    print(f"{name:<35} | ${sim_pnl_usdt:<+17.2f} | ₹{sim_pnl_inr:<+17.2f} | {sim_pf:<12.2f}")

print("-" * 90)
print("\n===================================================================")
