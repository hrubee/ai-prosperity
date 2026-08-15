#!/usr/bin/env python3
"""robust_grid_sweep_june.py — Full Parameter Grid Search across June 2026 Continuous 1m Dataset.
Database: /root/data/june_2026_1m.db (15,313,290 1m candles, 357 symbols).

Sweep Grid (135 Total Combinations):
- Fib Entry: [0.600, 0.700]
- Fib SL: [0.700, 0.800]  (Valid pairs: (0.6,0.7), (0.6,0.8), (0.7,0.8))
- Volume Multipliers: [10x, 15x, 20x, 25x, 30x, 35x, 40x, 45x, 50x]
- Trailing SL Distances: [1.0R, 1.5R, 2.0R, 2.5R, 3.0R]
"""
import os, sys, sqlite3, time, json
import pandas as pd
import numpy as np

DB_PATH = "/root/data/june_2026_1m.db"
SUMMARY_FILE = "/root/grid_sweep_summary.txt"
JSON_FILE = "/root/grid_sweep_results.json"

def write_log(msg):
    print(msg, flush=True)
    with open(SUMMARY_FILE, "a") as f:
        f.write(msg + "\n")

with open(SUMMARY_FILE, "w") as f:
    f.write("=== FULL PARAMETER GRID SEARCH (JUNE 2026 CONTINUOUS 1M DATASET) ===\n\n")

write_log("===================================================================================")
write_log("🚀 ROBUST FULL-GRID PARAMETER SWEEP (JUNE 2026 CONTINUOUS 1M DATASET)")
write_log(f"   Database: {DB_PATH}")
write_log("   Grid: Entry Fibs [0.6, 0.7] | SL Fibs [0.7, 0.8] | Vol [10x-50x] | Trail [1.0R-3.0R]")
write_log("===================================================================================")

if not os.path.exists(DB_PATH):
    write_log(f"ERROR: Database not found at {DB_PATH}")
    sys.exit(1)

t0 = time.time()
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
write_log(f"Found {len(symbols)} total symbols in June 1m database.")

sym_data = {}
for i, sym in enumerate(symbols):
    rows = cursor.execute("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", (sym,)).fetchall()
    if len(rows) < 300: continue
    
    bars_15m = []
    cur_bar = None
    for c in rows:
        ts, o, h, l, cl, v = c
        b_ts = (ts // 900000) * 900000
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None: bars_15m.append(cur_bar)
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': cl, 'volume': v}
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = cl
            cur_bar['volume'] += v
    if cur_bar is not None: bars_15m.append(cur_bar)
    if len(bars_15m) < 40: continue
    
    vols = [b['volume'] for b in bars_15m]
    for idx, b in enumerate(bars_15m):
        start_i = max(0, idx - 39)
        w = vols[start_i:idx+1]
        b['vol_ma'] = sum(w) / len(w)
        b['is_green'] = b['close'] >= b['open']
        b['vol_mult'] = (b['volume'] / b['vol_ma']) if b['vol_ma'] > 0 else 0
        
    sym_data[sym] = (rows, bars_15m)

write_log(f"Pre-processed 1m/15m data for {len(sym_data)} symbols in {time.time()-t0:.2f}s!")

fib_pairs = [(0.600, 0.700), (0.600, 0.800), (0.700, 0.800)]
vol_mults = [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0]
trail_distances = [1.0, 1.5, 2.0, 2.5, 3.0]
RISK_USD = 10.00  # $10 Risk per trade (~1% of $1,000 account)

grid_results = []
combo_count = 0
total_combos = len(fib_pairs) * len(vol_mults) * len(trail_distances)

write_log(f"\nBeginning Evaluation of {total_combos} Strategy Combinations...")

for entry_fib, sl_fib in fib_pairs:
    for v_mult in vol_mults:
        for trail_r in trail_distances:
            combo_count += 1
            st_combo = time.time()
            
            tot_spikes = 0
            tot_trades = 0
            wins = 0
            losses = 0
            r_multiples = []
            pnls = []
            
            act_r = 1.0  # Instant activation at 1.0R
            
            for sym, (candles_1m, bars_15m) in sym_data.items():
                spikes = [b for b in bars_15m if b['is_green'] and b['vol_mult'] >= v_mult]
                if not spikes: continue
                
                tot_spikes += len(spikes)
                
                for s in spikes:
                    high_px, low_px = s['high'], s['low']
                    rng = high_px - low_px
                    if rng <= 0: continue
                    
                    entry_px = high_px - (entry_fib * rng)
                    sl_px = high_px - (sl_fib * rng)
                    risk = entry_px - sl_px
                    if risk <= 0: continue
                    
                    spike_end_ts = int(s['ts']) + 900000
                    sub = [c for c in candles_1m if spike_end_ts <= c[0] <= (spike_end_ts + 43200000)]
                    if not sub: continue
                    
                    filled = False
                    trade_r = 0.0
                    peak_px = entry_px
                    cur_sl = sl_px
                    
                    for m_row in sub:
                        m_ts, m_o, m_h, m_l, m_cl, m_v = m_row
                        if not filled:
                            if m_l <= entry_px:
                                if m_l <= sl_px: break  # Invalidated before fill
                                filled = True
                                peak_px = entry_px
                                cur_sl = sl_px
                        else:
                            if m_h > peak_px:
                                peak_px = m_h
                                peak_r = (peak_px - entry_px) / risk
                                if peak_r >= act_r:
                                    cur_sl = max(cur_sl, peak_px - (trail_r * risk))
                                    
                            if m_l <= cur_sl:
                                trade_r = (cur_sl - entry_px) / risk
                                break
                                
                    if filled and trade_r != 0.0:
                        fee_r = 0.05
                        net_r = trade_r - fee_r
                        tot_trades += 1
                        r_multiples.append(net_r)
                        pnls.append(net_r * RISK_USD)
                        if net_r > 0: wins += 1
                        else: losses += 1
                        
            if tot_trades > 0:
                wr = (wins / tot_trades) * 100
                avg_r = sum(r_multiples) / len(r_multiples)
                tot_pnl_usd = sum(pnls)
                win_sum = sum(r for r in r_multiples if r > 0)
                loss_sum = abs(sum(r for r in r_multiples if r < 0))
                pf = (win_sum / loss_sum) if loss_sum > 0 else (99.9 if win_sum > 0 else 0.0)
                
                # Max drawdown calculation
                cum = np.cumsum(pnls)
                peak = np.maximum.accumulate(cum)
                dd = peak - cum
                max_dd_usd = float(np.max(dd)) if len(dd) > 0 else 0.0
                
                res = {
                    "combo_id": combo_count,
                    "entry_fib": entry_fib,
                    "sl_fib": sl_fib,
                    "vol_mult": v_mult,
                    "trail_r": trail_r,
                    "spikes": tot_spikes,
                    "trades": tot_trades,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": round(wr, 1),
                    "avg_r": round(avg_r, 2),
                    "pf": round(pf, 2),
                    "pnl_usd": round(tot_pnl_usd, 2),
                    "pnl_inr": round(tot_pnl_usd * 88.5, 2),
                    "max_dd_usd": round(max_dd_usd, 2)
                }
                grid_results.append(res)
                
            if combo_count % 15 == 0:
                write_log(f"  Processed {combo_count}/{total_combos} combinations ({time.time()-t0:.1f}s)...")

# Sort all results by Net PnL INR descending
grid_results.sort(key=lambda x: x["pnl_inr"], reverse=True)

# Save JSON results
with open(JSON_FILE, "w") as f:
    json.dump(grid_results, f, indent=2)

write_log("\n=========================================================================================================================")
write_log("🏆 TOP 15 BEST PERFORMING STRATEGY COMBINATIONS (SORTED BY NET PNL)")
write_log("=========================================================================================================================")
write_log(f"{'Rank':<5} | {'Entry':<6} | {'SL':<5} | {'Vol':<6} | {'Trail R':<8} | {'Trades':<7} | {'Win Rate':<9} | {'Avg R':<7} | {'PF':<6} | {'Net PnL ($)':<12} | {'Net PnL (₹)':<14}")
write_log("-" * 120)

for rank, r in enumerate(grid_results[:15], 1):
    write_log(f"{rank:<5} | {r['entry_fib']:<6.3f} | {r['sl_fib']:<5.3f} | {r['vol_mult']:>4.1f}x | {r['trail_r']:>5.1f}R  | {r['trades']:>7} | {r['win_rate']:>7.1f}% | {r['avg_r']:>+5.2f}R | {r['pf']:>6.2f} | ${r['pnl_usd']:>+10.2f} | ₹{r['pnl_inr']:>+12.2f}")

write_log("=========================================================================================================================")
write_log(f"Completed Full Grid Search in {time.time()-t0:.2f} seconds!")

