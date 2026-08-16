#!/usr/bin/env python3
"""backtest_timeframes_fibvol.py — Multi-Timeframe Comparative Backtest for FibVOL Strategy.
Evaluates the Champion config (10x Vol, 0.6 Entry, 0.7 SL, 1.0R Trailing SL, 10.0R TP) on June 2026 1m data
across 5m, 10m, 15m, 30m, 1h, 2h, and 4h timeframes.
"""
import os, sys, sqlite3, time, json
import numpy as np

DB_PATH = "/root/data/june_2026_1m.db"

if not os.path.exists(DB_PATH):
    print(f"Error: Database not found at {DB_PATH}")
    sys.exit(1)

t0 = time.time()
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
print(f"Found {len(symbols)} total symbols in June 1m database.", flush=True)

# De-duplicate loaded candles to RAM to save time
raw_candles = {}
for i, sym in enumerate(symbols):
    rows = cursor.execute("SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC", (sym,)).fetchall()
    if len(rows) < 300: continue
    raw_candles[sym] = rows

print(f"Loaded data for {len(raw_candles)} symbols into memory in {time.time()-t0:.2f}s.", flush=True)

# Timeframes to evaluate (value is timeframe duration in minutes)
timeframes = {
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240
}

# Champion Config parameters
ENTRY_FIB = 0.600
SL_FIB = 0.700
VOL_MULT = 10.0
ACT_R = 1.0
TRAIL_R = 1.0
TP_RR = 10.0
RISK_USD = 10.00
FEE_R = 0.05

tf_results = {}

for tf_name, tf_min in timeframes.items():
    print(f"Evaluating {tf_name} timeframe...", flush=True)
    tf_ms = tf_min * 60 * 1000
    
    trades = []
    
    for sym, rows in raw_candles.items():
        # Resample 1m rows into tf_name bars
        bars_tf = []
        cur_bar = None
        for c in rows:
            ts, o, h, l, cl, v = c
            b_ts = (ts // tf_ms) * tf_ms
            if cur_bar is None or cur_bar['ts'] != b_ts:
                if cur_bar is not None: 
                    bars_tf.append(cur_bar)
                cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': cl, 'volume': v}
            else:
                if h > cur_bar['high']: cur_bar['high'] = h
                if l < cur_bar['low']: cur_bar['low'] = l
                cur_bar['close'] = cl
                cur_bar['volume'] += v
        if cur_bar is not None: 
            bars_tf.append(cur_bar)
            
        if len(bars_tf) < 45: continue
        
        # Calculate volume MA
        vols = [b['volume'] for b in bars_tf]
        for idx, b in enumerate(bars_tf):
            start_i = max(0, idx - 39)
            w = vols[start_i:idx+1]
            b['vol_ma'] = sum(w) / len(w)
            b['is_green'] = b['close'] >= b['open']
            b['vol_mult'] = (b['volume'] / b['vol_ma']) if b['vol_ma'] > 0 else 0
            
        # Process trade triggers
        spikes = [b for b in bars_tf if b['is_green'] and b['vol_mult'] >= VOL_MULT]
        if not spikes: continue
        
        for s in spikes:
            high_px, low_px = s['high'], s['low']
            rng = high_px - low_px
            if rng <= 0: continue
            
            entry_px = high_px - (ENTRY_FIB * rng)
            sl_px = high_px - (SL_FIB * rng)
            risk = entry_px - sl_px
            if risk <= 0: continue
            tp_px = entry_px + (TP_RR * risk)
            
            spike_end_ts = int(s['ts']) + tf_ms
            # Get 1m candles for 12h path evaluation
            sub = [c for c in rows if spike_end_ts <= c[0] <= (spike_end_ts + 43200000)]
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
                        if peak_r >= ACT_R:
                            cur_sl = max(cur_sl, peak_px - (TRAIL_R * risk))
                            
                    if m_l <= cur_sl:
                        trade_r = (cur_sl - entry_px) / risk
                        break
                    elif m_h >= tp_px:
                        trade_r = TP_RR
                        break
                        
            if filled and trade_r != 0.0:
                net_r = trade_r - FEE_R
                trades.append(net_r)

    tot = len(trades)
    if tot == 0:
        tf_results[tf_name] = {"trades": 0, "win_rate": 0.0, "pf": 0.0, "pnl_usd": 0.0, "pnl_inr": 0.0}
        continue
        
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    win_rate = (len(wins) / tot) * 100
    
    g_prof = sum(wins)
    g_loss = abs(sum(losses))
    pf = (g_prof / g_loss) if g_loss > 0 else 99.9
    net_pnl_usd = sum(trades) * RISK_USD
    net_pnl_inr = net_pnl_usd * 88.5
    
    tf_results[tf_name] = {
        "trades": tot,
        "win_rate": win_rate,
        "pf": pf,
        "pnl_usd": net_pnl_usd,
        "pnl_inr": net_pnl_inr
    }

# Print Comparative Table
table_txt = """
======================================================================================================
📊 TIMEFRAME COMPARATIVE BACKTEST RESULTS (JUNE 2026 CONTINUOUS 1M DATASET)
======================================================================================================
Timeframe | Trades | Win Rate % | Profit Factor | Net PnL (USDT) | Net PnL (INR) | Performance Rank
------------------------------------------------------------------------------------------------------
"""

# Sort by Net PnL (INR)
sorted_tfs = sorted(tf_results.items(), key=lambda x: x[1]["pnl_inr"], reverse=True)
for rank, (name, metrics) in enumerate(sorted_tfs):
    table_txt += f"{name:<9} | {metrics['trades']:<6} | {metrics['win_rate']:<10.1f}% | {metrics['pf']:<13.2f} | ${metrics['pnl_usd']:<14.2f} | ₹{metrics['pnl_inr']:<13.2f} | #{rank+1}\n"
table_txt += "======================================================================================================\n"

print(table_txt, flush=True)

with open("/root/timeframe_backtest_summary.txt", "w") as f:
    f.write(table_txt)

print("Results written to /root/timeframe_backtest_summary.txt", flush=True)
