#!/usr/bin/env python3
import sys, json, time, urllib.request

trades = [
    {"sym": "AWE", "entry": 0.061031, "sl": 0.060990, "entry_t": 1786648873000, "qty": 17178, "hard_pnl": 1.86},
    {"sym": "HANA", "entry": 0.032510, "sl": 0.032490, "entry_t": 1786654238000, "qty": 33433, "hard_pnl": 5.53},
    {"sym": "HANA", "entry": 0.032630, "sl": 0.032580, "entry_t": 1786655772000, "qty": 33392, "hard_pnl": -1.67},
    {"sym": "PIEVERSE", "entry": 0.751307, "sl": 0.748700, "entry_t": 1786663270000, "qty": 823, "hard_pnl": -1.41},
    {"sym": "SOPH", "entry": 0.003553, "sl": 0.003548, "entry_t": 1786664915000, "qty": 288814, "hard_pnl": 3.18},
    {"sym": "SWARMS", "entry": 0.008914, "sl": 0.008857, "entry_t": 1786667652000, "qty": 52335, "hard_pnl": 18.13},
    {"sym": "RED", "entry": 0.091886, "sl": 0.091600, "entry_t": 1786673116000, "qty": 6941, "hard_pnl": -1.29},
    {"sym": "PIXEL", "entry": 0.004449, "sl": 0.004436, "entry_t": 1786678482000, "qty": 90185, "hard_pnl": 6.77}
]

print("=== RIGOROUS CHRONOLOGICAL 1-MINUTE INTRABAR REPLAY ===")
print(f"{'Symbol':<10} | {'Entry':<9} | {'1:5 Hard TP PnL ($)':<18} | {'Peak R':<8} | {'Exit Price':<10} | {'Exact 1m Replay PnL ($)':<24}")
print("-" * 95)

total_hard_tp = 0.0
total_exact_1m = 0.0

for t in trades:
    sym = t["sym"]
    entry = t["entry"]
    sl = t["sl"]
    qty = t["qty"]
    entry_t = t["entry_t"]
    hard_pnl = t["hard_pnl"]
    total_hard_tp += hard_pnl
    
    risk = entry - sl
    if risk <= 0:
        continue

    # Fetch 1m candles for symbol
    url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval=1m&limit=300"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        # Sort candles chronologically
        candles = sorted(raw, key=lambda c: float(c.get('time', 0)))
    except Exception as e:
        candles = []

    # Filter candles starting from entry time
    trade_candles = [c for c in candles if float(c.get('time', 0)) >= (entry_t - 60000)]
    
    peak_px = entry
    cur_sl = sl
    exit_px = None
    
    for c in trade_candles:
        c_high = float(c.get('high', 0))
        c_low = float(c.get('low', 0))
        
        # Update peak high
        if c_high > peak_px:
            peak_px = c_high
            peak_r = (peak_px - entry) / risk
            if peak_r >= 2.0:
                desired_sl = peak_px - (1.0 * risk)
                if desired_sl > cur_sl:
                    cur_sl = desired_sl
                    
        # Check if low hit current trailing stop loss or initial SL
        if c_low <= cur_sl:
            exit_px = cur_sl
            break

    if exit_px is None:
        exit_px = peak_px if peak_px > entry else entry

    exact_pnl = (exit_px - entry) * qty
    total_exact_1m += exact_pnl
    peak_r = (peak_px - entry) / risk

    print(f"{sym:<10} | {entry:<9.6f} | ${hard_pnl:<18.2f} | {peak_r:<8.2f}R | {exit_px:<10.6f} | ${exact_pnl:<24.2f}")

print("-" * 95)
print(f"TOTAL PROFIT WITH HARD 1:5 TP CAP:         ${total_hard_tp:.2f} USD (₹{total_hard_tp*84:.2f} INR)")
print(f"TOTAL PROFIT WITH STEP-BY-STEP 1m REPLAY:  ${total_exact_1m:.2f} USD (₹{total_exact_1m*84:.2f} INR)")
