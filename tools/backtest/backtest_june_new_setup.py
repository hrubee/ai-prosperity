#!/usr/bin/env python3
"""backtest_june_new_setup.py — Independent 1m Candle-Granularity June Backtest with New Trailing SL Setup.
Database: /root/data/june_2026_1m.db (15,313,290 1m candles, 493 symbols).
New Setup: Entry Fib = 0.700 | SL Fib = 0.800 | Instant 1.0R Activation | 3.0R Trailing Distance | Uncapped TP.
"""
import os, sys, sqlite3, time
import pandas as pd
import numpy as np

DB_PATH = "/root/data/june_2026_1m.db"

print("===================================================================", flush=True)
print("🚀 INDEPENDENT JUNE BACKTEST — NEW TRAILING SL SETUP (1M GRANULARITY)", flush=True)
print(f"   Database: {DB_PATH}", flush=True)
print("   Setup: Entry Fib=0.700 | SL Fib=0.800 | Act=1.0R | Trail Dist=3.0R | Uncapped TP", flush=True)
print("===================================================================", flush=True)

if not os.path.exists(DB_PATH):
    print(f"Error: Database not found at {DB_PATH}")
    sys.exit(1)

t0 = time.time()
conn = sqlite3.connect(DB_PATH)

ENTRY_FIB = 0.700
SL_FIB = 0.800
RISK_USD = 10.00  # $10 Risk per trade (~1% of $1000 account)

cursor = conn.cursor()
symbols = [row[0] for row in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
print(f"Found {len(symbols)} total symbols in June 1m database.", flush=True)

sym_data = {}
for i, sym in enumerate(symbols):
    df = pd.read_sql_query("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", conn, params=(sym,))
    if len(df) < 500: continue
    
    df['ts'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('ts', inplace=True)
    
    # Resample to 15m bars
    df_15m = df.resample('15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    
    if len(df_15m) < 40: continue
    
    df_15m['vol_ma'] = df_15m['volume'].rolling(40, min_periods=20).mean()
    df_15m['is_green'] = df_15m['close'] >= df_15m['open']
    df_15m['vol_mult'] = df_15m['volume'] / df_15m['vol_ma']
    
    sym_data[sym] = (df, df_15m)

print(f"Processed 1m/15m data for {len(sym_data)} symbols in {time.time()-t0:.2f}s! Running 1m Granularity Backtest...", flush=True)

# Test configurations on June data:
# Config 1: Old Setup (30x Vol, Static 5R TP, +2R Act / 1.0R Trail)
# Config 2: NEW SETUP (30x Vol, Uncapped TP, +1.0R Act / 3.0R Trail)
# Config 3: NEW SETUP (30x Vol, Uncapped TP, +1.0R Act / 2.0R Trail)
# Config 4: NEW SETUP (30x Vol, Uncapped TP, +1.0R Act / 4.0R Trail)

configs = [
    {"name": "Old Setup (Static 5R TP, +2.0R Act / 1.0R Trail)", "tp_rr": 5.0, "act_r": 2.0, "trail_r": 1.0},
    {"name": "NEW SETUP (Uncapped TP, +1.0R Act / 2.0R Trail)",  "tp_rr": 99.0, "act_r": 1.0, "trail_r": 2.0},
    {"name": "NEW SETUP (Uncapped TP, +1.0R Act / 3.0R Trail)",  "tp_rr": 99.0, "act_r": 1.0, "trail_r": 3.0},
    {"name": "NEW SETUP (Uncapped TP, +1.0R Act / 4.0R Trail)",  "tp_rr": 99.0, "act_r": 1.0, "trail_r": 4.0},
]

summary_results = []

for cfg in configs:
    st = time.time()
    tot_spikes = 0
    tot_trades = 0
    wins = 0
    losses = 0
    r_multiples = []
    pnls = []
    
    act_r = cfg["act_r"]
    trail_r = cfg["trail_r"]
    tp_rr = cfg["tp_rr"]
    
    for sym, (df_1m, df_15m) in sym_data.items():
        spikes = df_15m[df_15m['is_green'] & (df_15m['vol_mult'] >= 30.0)]
        if len(spikes) == 0: continue
        
        tot_spikes += len(spikes)
        
        for idx, srow in spikes.iterrows():
            high_px, low_px = srow['high'], srow['low']
            rng = high_px - low_px
            if rng <= 0: continue
            
            entry_px = high_px - (ENTRY_FIB * rng)
            sl_px = high_px - (SL_FIB * rng)
            risk = entry_px - sl_px
            if risk <= 0: continue
            tp_px = entry_px + (tp_rr * risk)
            
            spike_end_ts = idx + pd.Timedelta(minutes=15)
            # Evaluate 1-minute granularity for up to 12 hours after spike
            sub = df_1m[(df_1m.index >= spike_end_ts) & (df_1m.index <= spike_end_ts + pd.Timedelta(hours=12))]
            if len(sub) == 0: continue
            
            filled = False
            trade_r = 0.0
            peak_px = entry_px
            cur_sl = sl_px
            
            for m_idx, m_row in sub.iterrows():
                m_high, m_low = m_row['high'], m_row['low']
                if not filled:
                    if m_low <= entry_px:
                        if m_low <= sl_px: break  # Invalidated before fill
                        filled = True
                        peak_px = entry_px
                        cur_sl = sl_px
                else:
                    if m_high > peak_px:
                        peak_px = m_high
                        peak_r = (peak_px - entry_px) / risk
                        if peak_r >= act_r:
                            cur_sl = max(cur_sl, peak_px - (trail_r * risk))
                            
                    if m_low <= cur_sl:
                        trade_r = (cur_sl - entry_px) / risk
                        break
                    elif m_high >= tp_px:
                        trade_r = tp_rr
                        break
                        
            if filled and trade_r != 0.0:
                fee_r = 0.05
                net_r = trade_r - fee_r
                tot_trades += 1
                r_multiples.append(net_r)
                pnls.append(net_r * RISK_USD)
                if net_r > 0: wins += 1
                else: losses += 1

    wr = (wins / tot_trades * 100) if tot_trades > 0 else 0
    avg_r = float(np.mean(r_multiples)) if r_multiples else 0
    tot_pnl = sum(pnls)
    win_sum = sum(r for r in r_multiples if r > 0)
    loss_sum = abs(sum(r for r in r_multiples if r < 0))
    pf = (win_sum / loss_sum) if loss_sum > 0 else 99.9
    
    summary_results.append({
        "name": cfg["name"],
        "spikes": tot_spikes,
        "trades": tot_trades,
        "win_rate": wr,
        "avg_r": avg_r,
        "pf": pf,
        "pnl_usd": tot_pnl,
        "pnl_inr": tot_pnl * 88.5
    })
    
    print(f"Processed {cfg['name']} in {time.time()-st:.2f}s", flush=True)

print("\n==================================================================", flush=True)
print("📊 INDEPENDENT JUNE 2026 CONTINUOUS 1M BACKTEST RESULTS SUMMARY", flush=True)
print("==================================================================", flush=True)
print(f"{'Setup / Configuration':<46} | {'Trades':<7} | {'Win Rate':<9} | {'Avg R':<8} | {'PF':<6} | {'Net PnL ($)':<12} | {'Net PnL (₹)':<14}")
print("-" * 115)

for r in summary_results:
    print(f"{r['name']:<46} | {r['trades']:>7} | {r['win_rate']:>7.1f}% | {r['avg_r']:>+6.2f}R | {r['pf']:>6.2f} | ${r['pnl_usd']:>+10.2f} | ₹{r['pnl_inr']:>+12.2f}")

print("==================================================================", flush=True)
