#!/usr/bin/env python3
import sys, json, time, urllib.request, os
from concurrent.futures import ThreadPoolExecutor, as_completed

state_path = "/root/fibvol_coindcx/state.json"
try:
    state = json.load(open(state_path))
    last_spikes = state.get("last_spikes", {})
except Exception as e:
    print("Error reading state.json:", e)
    sys.exit(1)

def fetch_1m(sym):
    url = f"https://public.coindcx.com/market_data/candles?pair=B-{sym}_USDT&interval=1m&limit=1000"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = json.loads(urllib.request.urlopen(req).read().decode())
        if isinstance(raw, list) and len(raw) > 0:
            return sorted(raw, key=lambda c: float(c.get('time', 0)))
    except Exception:
        pass
    return []

def simulate_trade_for_sym(item):
    sym, spike_t = item
    candles = fetch_1m(sym)
    if not candles:
        return sym, spike_t, ("NO_DATA", 0.0), ("NO_DATA", 0.0), ("NO_DATA", 0.0)
        
    c_15m = [c for c in candles if float(c.get('time', 0)) <= spike_t]
    if len(c_15m) < 15:
        return sym, spike_t, ("NO_15M", 0.0), ("NO_15M", 0.0), ("NO_15M", 0.0)
        
    c_spike_15m = c_15m[-15:]
    high = max(float(c.get('high', 0)) for c in c_spike_15m)
    low = min(float(c.get('low', 0)) for c in c_spike_15m)
    
    rng = high - low
    if rng <= 0:
        return sym, spike_t, ("ZERO_RNG", 0.0), ("ZERO_RNG", 0.0), ("ZERO_RNG", 0.0)
        
    res_a = run_single_sim(candles, spike_t, high, low, rng, 0.50, 0.60)
    res_b = run_single_sim(candles, spike_t, high, low, rng, 0.50, 0.70)
    res_c = run_single_sim(candles, spike_t, high, low, rng, 0.60, 0.70)
    
    return sym, spike_t, res_a, res_b, res_c

def run_single_sim(candles, spike_t, high, low, rng, entry_fib, sl_fib, rr_ratio=5.0):
    entry_px = high - (entry_fib * rng)
    sl_px = high - (sl_fib * rng)
    risk = entry_px - sl_px
    if risk <= 0:
        return "BAD_RISK", 0.0
    tp_px = entry_px + (rr_ratio * risk)
    
    risk_usd = 1.50 # $1.50 USD (1.0% of $150 equity / ~₹125 INR)
    qty = risk_usd / risk
    
    post_spike_candles = [c for c in candles if float(c.get('time', 0)) >= spike_t]
    
    filled = False
    fill_px = 0.0
    exit_px = 0.0
    pnl = 0.0
    status = "NO_FILL"
    peak_r = 0.0
    trail_sl = sl_px
    
    for c in post_spike_candles:
        c_high = float(c.get('high', 0))
        c_low = float(c.get('low', 0))
        
        if not filled:
            if c_low <= sl_px:
                status = "SKIPPED_SL"
                break
            if c_low <= entry_px:
                filled = True
                fill_px = entry_px
        else:
            curr_r = (c_high - fill_px) / risk
            if curr_r > peak_r:
                peak_r = curr_r
                
            if peak_r >= 2.0:
                new_t = fill_px + ((peak_r - 1.0) * risk)
                if new_t > trail_sl:
                    trail_sl = new_t
                    
            if c_low <= trail_sl:
                exit_px = trail_sl
                pnl = (exit_px - fill_px) * qty
                status = "WIN_TRAIL" if pnl > 0 else "STOP_OUT"
                break
                
            if c_high >= tp_px:
                exit_px = tp_px
                pnl = (exit_px - fill_px) * qty
                status = "WIN_TP"
                break

    return status, pnl

print(f"=== SIMULATING ALL {len(last_spikes)} TRADES DETECTED BY FIBVOL SINCE DEPLOYMENT ===")
print("Replaying 1-Minute Intrabar Data across exact Volume Spike Events:")
print("  Option A: Baseline (Entry 0.5 Fib / SL 0.6 Fib)")
print("  Option B: Wider SL (Entry 0.5 Fib / SL 0.7 Fib)")
print("  Option C: Deeper Entry (Entry 0.6 Fib / SL 0.7 Fib)")
print("-" * 105)

items = sorted(last_spikes.items(), key=lambda x: x[1])

print(f"{'Symbol':<12} | {'Spike Time (IST)':<18} | {'Option A (0.5/0.6)':<22} | {'Option B (0.5/0.7 Wider SL)':<25} | {'Option C (0.6/0.7 Deeper)':<22}")
print("-" * 105)

tot_a = 0.0
tot_b = 0.0
tot_c = 0.0

with ThreadPoolExecutor(max_workers=15) as executor:
    futures = [executor.submit(simulate_trade_for_sym, item) for item in items]
    results = [f.result() for f in as_completed(futures)]

results = sorted(results, key=lambda x: x[1])

for sym, spike_t, (st_a, pnl_a), (st_b, pnl_b), (st_c, pnl_c) in results:
    t_str = time.strftime('%Y-%m-%d %H:%M', time.gmtime((spike_t + 19800000) / 1000))
    
    tot_a += pnl_a
    tot_b += pnl_b
    tot_c += pnl_c
    
    str_a = f"${pnl_a:+.2f} ({st_a})" if st_a not in ["NO_FILL", "SKIPPED_SL"] else f"{st_a}"
    str_b = f"${pnl_b:+.2f} ({st_b})" if st_b not in ["NO_FILL", "SKIPPED_SL"] else f"{st_b}"
    str_c = f"${pnl_c:+.2f} ({st_c})" if st_c not in ["NO_FILL", "SKIPPED_SL"] else f"{st_c}"
    
    print(f"{sym:<12} | {t_str:<18} | {str_a:<22} | {str_b:<25} | {str_c:<22}")

print("-" * 105)
print(f"TOTAL NET PROFIT ACROSS ALL DEPLOYED TRADES:")
print(f"  Option A (Baseline 0.5 Fib / 0.6 SL):         ${tot_a:+.2f} USD (₹{tot_a*84:+.2f} INR)")
print(f"  Option B (User Proposed 0.5 Fib / 0.7 SL):   ${tot_b:+.2f} USD (₹{tot_b*84:+.2f} INR)")
print(f"  Option C (Deeper Entry 0.6 Fib / 0.7 SL):     ${tot_c:+.2f} USD (₹{tot_c*84:+.2f} INR)")
