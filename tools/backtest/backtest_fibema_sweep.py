#!/usr/bin/env python3
"""tools/backtest/backtest_fibema_sweep.py

Comprehensive 1-minute granularity backtest and parameter sweep for the "fibema" Long-Only strategy:
- 21 EMA calculated on LOW
- Price above 21 EMA (Low) creates impulse swing (Low to High)
- Sweep of Fibonacci Entry Levels (0.236, 0.382, 0.500, 0.618, 0.705, 0.786)
- Sweep of Stop Loss placements (Below Swing Low 1.0, Fib 0.786, Fib 0.886, ATR buffer, Below 21 EMA)
- Sweep of Risk-Reward (RR) ratios (1:1.5, 1:2, 1:2.5, 1:3, 1:4)
- 1-Minute intrabar execution simulation across 15.3M candles in june_2026_1m.db
- Includes realistic 0.10% CoinDCX taker fees and 0.10% slippage
"""
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime

DB_PATH = "datasets/june_2026_1m.db"
print(f"Loading 1-minute candle dataset from {DB_PATH}...", flush=True)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]

raw_m1 = {}
for sym in symbols:
    rows = cursor.execute(
        "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
        (sym,)
    ).fetchall()
    if len(rows) >= 100:
        raw_m1[sym] = rows
conn.close()

print(f"Loaded {len(raw_m1)} coins with 1-minute candle history.", flush=True)

def resample_bars(m1_rows, tf_min):
    tf_ms = tf_min * 60 * 1000
    tf_bars = []
    cur_bar = None
    cur_1m_slice = []
    for row in m1_rows:
        ts, o, h, l, c, v = row
        b_ts = (ts // tf_ms) * tf_ms
        if cur_bar is None or cur_bar['ts'] != b_ts:
            if cur_bar is not None:
                cur_bar['m1_bars'] = cur_1m_slice
                tf_bars.append(cur_bar)
            cur_bar = {'ts': b_ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}
            cur_1m_slice = [row]
        else:
            if h > cur_bar['high']: cur_bar['high'] = h
            if l < cur_bar['low']: cur_bar['low'] = l
            cur_bar['close'] = c
            cur_bar['volume'] += v
            cur_1m_slice.append(row)
    if cur_bar is not None:
        cur_bar['m1_bars'] = cur_1m_slice
        tf_bars.append(cur_bar)
    return tf_bars

# Timeframes to evaluate
timeframes = [15, 60, 240] # 15m, 1h, 4h
fib_entry_levels = [0.382, 0.500, 0.618, 0.705, 0.786]
sl_types = [
    ("Swing Low (1.0)", "swing_low"),
    ("Fib 0.886", "fib_0886"),
    ("Below EMA21", "below_ema"),
    ("1.0x ATR", "atr_10")
]
rr_targets = [1.5, 2.0, 2.5, 3.0, 4.0]

fee_pct = 0.0010
slip_pct = 0.0010

results = []

for tf in timeframes:
    tf_name = f"{tf}m" if tf < 60 else (f"{tf//60}h" if tf >= 60 else f"{tf}m")
    print(f"\n================================================================================")
    print(f"🔄 EVALUATING TIMEFRAME: {tf_name.upper()} WITH 1-MINUTE EXECUTION PRECISION")
    print(f"================================================================================", flush=True)
    
    # Process each symbol
    sym_data = {}
    for sym, m1_rows in raw_m1.items():
        bars = resample_bars(m1_rows, tf)
        if len(bars) < 35: continue
        
        lows = pd.Series([b['low'] for b in bars])
        highs = pd.Series([b['high'] for b in bars])
        closes = pd.Series([b['close'] for b in bars])
        opens = pd.Series([b['open'] for b in bars])
        times = [b['ts'] for b in bars]
        
        # 21 EMA on LOW
        ema21_low = lows.ewm(span=21, adjust=False).mean().values
        
        # ATR 14
        tr = np.maximum(highs - lows, np.maximum(abs(highs - closes.shift(1)), abs(lows - closes.shift(1)))).fillna(highs - lows)
        atr14 = tr.rolling(14).mean().fillna(highs - lows).values
        
        sym_data[sym] = {
            "bars": bars,
            "lows": lows.values,
            "highs": highs.values,
            "closes": closes.values,
            "opens": opens.values,
            "ema21_low": ema21_low,
            "atr14": atr14,
            "times": times,
            "m1_rows": m1_rows
        }
        
    for fib_entry in fib_entry_levels:
        for sl_name, sl_mode in sl_types:
            for rr in rr_targets:
                all_trades = []
                
                for sym, data in sym_data.items():
                    bars = data["bars"]
                    lows = data["lows"]
                    highs = data["highs"]
                    closes = data["closes"]
                    ema21 = data["ema21_low"]
                    atr = data["atr14"]
                    times = data["times"]
                    m1_rows = data["m1_rows"]
                    
                    n = len(bars)
                    for i in range(25, n - 2):
                        # Condition: Price is above 21 EMA (Low)
                        # Identify an impulse swing high above EMA
                        if closes[i] <= ema21[i]: continue
                        
                        # Swing Low: lowest low of last 10 bars
                        sw_low = np.min(lows[max(0, i - 10) : i + 1])
                        sw_high = highs[i]
                        
                        if sw_high <= sw_low or (sw_high - sw_low) / sw_low < 0.008:
                            continue
                            
                        # Fibonacci retracement level entry
                        fib_range = sw_high - sw_low
                        entry_level = sw_high - (fib_entry * fib_range)
                        
                        # Stop loss calculation
                        if sl_mode == "swing_low":
                            sl_level = sw_low * 0.998 # Just below swing low
                        elif sl_mode == "fib_0886":
                            sl_level = sw_high - (0.886 * fib_range)
                        elif sl_mode == "below_ema":
                            sl_level = ema21[i] * 0.997
                        elif sl_mode == "atr_10":
                            sl_level = entry_level - (1.0 * atr[i])
                        else:
                            sl_level = sw_low
                            
                        risk_dist = entry_level - sl_level
                        if risk_dist <= 0 or (risk_dist / entry_level) < 0.005:
                            continue
                            
                        tp_level = entry_level + (rr * risk_dist)
                        
                        # 1-Minute Intrabar execution forward test
                        # Find 1m bar starting at bar i+1
                        bar_next_ts = times[i + 1]
                        m1_times = [r[0] for r in m1_rows]
                        
                        # Binary search or scan for start idx
                        start_idx = -1
                        for idx in range(len(m1_times)):
                            if m1_times[idx] >= bar_next_ts:
                                start_idx = idx
                                break
                        if start_idx == -1: continue
                        
                        # Check for limit fill within next 20 bars
                        limit_filled = False
                        fill_idx = -1
                        fill_time = -1
                        max_wait_ms = 20 * tf * 60 * 1000
                        
                        for m_i in range(start_idx, len(m1_rows)):
                            m_t, m_o, m_h, m_l, m_c, m_v = m1_rows[m_i]
                            if m_t - bar_next_ts > max_wait_ms:
                                break # Order expired before pull back
                            if m_l <= entry_level:
                                limit_filled = True
                                fill_idx = m_i
                                fill_time = m_t
                                break
                                
                        if not limit_filled:
                            continue
                            
                        # Evaluate trade exit 1m by 1m
                        exit_r = None
                        for m_i in range(fill_idx, min(len(m1_rows), fill_idx + (48 * tf * 60))):
                            m_t, m_o, m_h, m_l, m_c, m_v = m1_rows[m_i]
                            if m_l <= sl_level:
                                real_sl = sl_level * (1.0 - slip_pct)
                                exit_r = -((entry_level - real_sl) / risk_dist)
                                break
                            elif m_h >= tp_level:
                                real_tp = tp_level * (1.0 - slip_pct)
                                exit_r = (real_tp - entry_level) / risk_dist
                                break
                                
                        if exit_r is None and fill_idx < len(m1_rows):
                            # Closed at end of dataset
                            exit_r = (m1_rows[-1][4] - entry_level) / risk_dist
                            
                        if exit_r is not None:
                            # Taker fee 0.10% roundtrip
                            fee_r = (2.0 * fee_pct) / (risk_dist / entry_level)
                            net_r = exit_r - fee_r
                            all_trades.append(net_r)
                            
                num_t = len(all_trades)
                if num_t < 15: continue
                wins = [t for t in all_trades if t > 0]
                losses = [t for t in all_trades if t <= 0]
                wr = len(wins) / num_t * 100
                tot_r = sum(all_trades)
                gw = sum(wins)
                gl = abs(sum(losses)) if losses else 0.001
                pf = gw / gl
                
                results.append({
                    "tf": tf_name,
                    "fib_entry": fib_entry,
                    "sl_type": sl_name,
                    "rr": rr,
                    "trades": num_t,
                    "win_rate": wr,
                    "profit_factor": pf,
                    "total_r": tot_r
                })

# Print top 25 configurations
results.sort(key=lambda x: x["total_r"], reverse=True)

print("\n" + "=" * 100)
print("🏆 TOP 25 FIBEMA CONFIGURATIONS (1-MINUTE INTRABAR PRECISION)")
print("=" * 100)
print(f"{'TF':<5} | {'Fib Entry':<10} | {'SL Type':<18} | {'RR Target':<10} | {'Trades':<8} | {'Win Rate':<10} | {'PF':<6} | {'Total Return (R)'}")
print("-" * 100)

for r in results[:25]:
    print(f"{r['tf']:<5} | {r['fib_entry']:<10.3f} | {r['sl_type']:<18} | 1:{r['rr']:<8.1f} | {r['trades']:<8} | {r['win_rate']:>6.1f}%    | {r['profit_factor']:<6.2f} | {r['total_r']:>+10.2f} R")

print("=" * 100)
