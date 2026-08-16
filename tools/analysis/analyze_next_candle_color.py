#!/usr/bin/env python3
"""tools/analysis/analyze_next_candle_color.py — Empirical Analysis of Post-Spike Candle Color.

Calculates the exact percentage of volume spike candles that have their literal
next candle (Candle +1) close RED (Close < Open).
Analyzes across:
1. Full 15.3M 1m Historical Dataset (datasets/june_2026_1m.db)
2. Fresh 24-Hour CoinDCX Dataset (datasets/coindcx_last_24h.db)
3. Multiple Timeframes (15m, 1h, 4h) and Spike Multipliers (5x, 10x, 20x, 30x).
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

def analyze_db(db_path, db_name):
    if not os.path.exists(db_path):
        print(f"File not found: {db_path}")
        return
        
    print(f"\n==================================================================================")
    print(f"📊 ANALYZING POST-SPIKE NEXT CANDLE COLOR: {db_name}")
    print(f"   Database Path: {db_path}")
    print(f"==================================================================================")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check tables
    tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    
    table_name = "klines_1m" if "klines_1m" in tables else "klines_15m"
    symbols = [r[0] for r in cursor.execute(f"SELECT DISTINCT symbol FROM {table_name}").fetchall()]
    
    for tf_name, tf_min in [("15m", 15), ("1h", 60), ("4h", 240)]:
        tf_ms = tf_min * 60 * 1000
        
        all_spikes = []
        
        for sym in symbols:
            rows = cursor.execute(
                f"SELECT timestamp, open, high, low, close, volume FROM {table_name} WHERE symbol=? ORDER BY timestamp ASC",
                (sym,)
            ).fetchall()
            if len(rows) < 100:
                continue
                
            # If 1m data, resample to TF
            if table_name == "klines_1m":
                bars = []
                cur_bar = None
                for r in rows:
                    ts, o, h, l, c, v = r
                    b_ts = (ts // tf_ms) * tf_ms
                    if cur_bar is None or cur_bar[0] != b_ts:
                        if cur_bar is not None: bars.append(cur_bar)
                        cur_bar = [b_ts, o, h, l, c, v]
                    else:
                        if h > cur_bar[2]: cur_bar[2] = h
                        if l < cur_bar[3]: cur_bar[3] = l
                        cur_bar[4] = c
                        cur_bar[5] += v
                if cur_bar is not None: bars.append(cur_bar)
            else:
                bars = rows
                
            n = len(bars)
            if n < 45:
                continue
                
            opens = np.array([b[1] for b in bars])
            highs = np.array([b[2] for b in bars])
            lows = np.array([b[3] for b in bars])
            closes = np.array([b[4] for b in bars])
            vols = np.array([b[5] for b in bars])
            times = [b[0] for b in bars]
            
            for i in range(40, n - 3):
                if closes[i] < opens[i]: # only examine green volume spikes
                    continue
                    
                base_v = np.mean(vols[i - 40 : i])
                if base_v <= 0:
                    continue
                vol_mult = vols[i] / base_v
                
                # Check next 3 candles
                c1_red = closes[i + 1] < opens[i + 1]
                c2_red = closes[i + 2] < opens[i + 2]
                c3_red = closes[i + 3] < opens[i + 3]
                
                # Change % on next candle
                c1_chg = (closes[i + 1] - opens[i + 1]) / opens[i + 1] * 100
                c1_drawdown_from_high = (highs[i] - lows[i + 1]) / highs[i] * 100
                
                all_spikes.append({
                    "symbol": sym,
                    "vol_mult": vol_mult,
                    "spike_chg": (closes[i] - opens[i]) / opens[i] * 100,
                    "c1_red": c1_red,
                    "c2_red": c2_red,
                    "c3_red": c3_red,
                    "c1_chg": c1_chg,
                    "c1_drawdown": c1_drawdown_from_high
                })
                
        df = pd.DataFrame(all_spikes)
        if len(df) == 0:
            continue
            
        print(f"\n📌 TIMEFRAME: {tf_name.upper()} ({len(df):,} total green spike events evaluated)")
        print(f"----------------------------------------------------------------------------------")
        print(f"{'Spike Threshold':<18} | {'Total Spikes':<12} | {'Next Candle RED %':<18} | {'Next Candle GREEN %':<20} | {'Avg Next Candle %'}")
        print(f"----------------------------------------------------------------------------------")
        
        for mult in [5.0, 10.0, 15.0, 20.0, 30.0]:
            sub = df[df['vol_mult'] >= mult]
            if len(sub) == 0:
                continue
            red_pct = sub['c1_red'].mean() * 100
            green_pct = 100.0 - red_pct
            avg_chg = sub['c1_chg'].mean()
            print(f">={mult:<4.1f}x Baseline   | {len(sub):<12,} | {red_pct:>16.1f}% | {green_pct:>18.1f}% | {avg_chg:>+14.2f}%")
            
        # Additional multi-candle sequence analysis for 10x spikes
        sub10 = df[df['vol_mult'] >= 10.0]
        if len(sub10) > 0:
            r1 = sub10['c1_red'].mean() * 100
            r2 = (sub10['c1_red'] & sub10['c2_red']).mean() * 100
            r3 = (sub10['c1_red'] & sub10['c2_red'] & sub10['c3_red']).mean() * 100
            print(f"\n  🔍 Sequential Post-Spike Breakdown (>=10x Spikes, N={len(sub10):,}):")
            print(f"     • Next Candle (Candle +1) is RED               : {r1:.1f}%")
            print(f"     • Next 2 Consecutive Candles (+1 & +2) are RED : {r2:.1f}%")
            print(f"     • Next 3 Consecutive Candles (+1 to +3) are RED: {r3:.1f}%")
            print(f"     • Median Pullback on Next Candle (from High)   : -{sub10['c1_drawdown'].median():.2f}%")
            
    conn.close()

if __name__ == "__main__":
    analyze_db("datasets/june_2026_1m.db", "HISTORICAL 15.3M CANDLE DATASET (357 COINS)")
    analyze_db("datasets/coindcx_last_24h.db", "FRESH 24-HOUR COINDCX DATASET (449 COINS)")
