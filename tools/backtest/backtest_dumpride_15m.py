#!/usr/bin/env python3
"""tools/backtest/backtest_dumpride_15m.py — 15-Minute Timeframe DumpRide Intrabar 1-Minute Backtest.

Tests:
• Timeframe: 15m
• Baseline Volume: 20-period SMA on 15m bars
• Spike Multipliers: 5x, 8x, 10x, 15x, 20x
• RR Ratios: 1:1.5, 1:2, 1:3, 1:4
• Intrabar Execution: 1-minute tick stepping
• Dataset: datasets/june_2026_1m.db (15.3M 1m bars across 357 pairs)
"""
import os
import sys
import sqlite3
import time
import numpy as np
import pandas as pd

DB_PATH = "datasets/june_2026_1m.db"

def resample_bars(m1_rows, tf_min=15):
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

def main():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        return

    print("="*105)
    print("🚀 15-MINUTE TIMEFRAME DUMPRIDE BACKTEST (1-MINUTE INTRABAR PRECISION)")
    print("   • Base Dataset: datasets/june_2026_1m.db (15.3M 1-minute bars / 357 coins)")
    print("   • Signal Timeframe: 15m (15-Minute Candles)")
    print("   • Stepping Resolution: 1-Minute Intrabar Walk")
    print("   • Stop Loss: 1.0x ATR(14) on 15m candles")
    print("="*105)

    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    symbols = [r[0] for r in cursor.execute("SELECT DISTINCT symbol FROM klines_1m").fetchall()]
    
    raw_m1 = {}
    total_1m = 0
    for sym in symbols:
        rows = cursor.execute(
            "SELECT timestamp, open, high, low, close, volume FROM klines_1m WHERE symbol=? ORDER BY timestamp ASC",
            (sym,)
        ).fetchall()
        if len(rows) >= 500:
            raw_m1[sym] = rows
            total_1m += len(rows)
    conn.close()
    print(f"Loaded {len(raw_m1)} coins ({total_1m:,} 1-minute bars) in {time.time()-t0:.2f}s.\n")

    # Resample all coins to 15m
    t_res = time.time()
    resampled_15m = {}
    for sym, m1_rows in raw_m1.items():
        bars_15m = resample_bars(m1_rows, tf_min=15)
        if len(bars_15m) >= 25:
            resampled_15m[sym] = bars_15m
    print(f"Resampled to 15m in {time.time()-t_res:.2f}s.\n")

    test_configs = [
        # (Spike Mult, RR)
        (10.0, 2.0),
        (10.0, 1.5),
        (10.0, 3.0),
        (5.0, 2.0),
        (8.0, 2.0),
        (15.0, 2.0),
        (20.0, 2.0),
    ]

    all_comparison = []

    for spike_mult, rr_target in test_configs:
        trades = []
        
        for sym, bars_15m in resampled_15m.items():
            m1_rows = raw_m1[sym]
            m1_ts_list = np.array([r[0] for r in m1_rows])
            
            opens = np.array([b['open'] for b in bars_15m])
            highs = np.array([b['high'] for b in bars_15m])
            lows = np.array([b['low'] for b in bars_15m])
            closes = np.array([b['close'] for b in bars_15m])
            volumes = np.array([b['volume'] for b in bars_15m])
            timestamps = np.array([b['ts'] for b in bars_15m])
            
            i = 20
            while i < len(bars_15m):
                base_vol = np.mean(volumes[i-20:i])
                if base_vol <= 0:
                    i += 1
                    continue
                    
                vol_mult = volumes[i] / base_vol
                is_green = closes[i] > opens[i]
                
                if vol_mult >= spike_mult and is_green:
                    entry_ts = timestamps[i] + 15 * 60 * 1000 # Close of 15m bar
                    entry_px = closes[i]
                    
                    # ATR(14) on 15m bars
                    trs = []
                    for k in range(max(1, i-13), i+1):
                        tr = max(highs[k] - lows[k], abs(highs[k] - closes[k-1]), abs(lows[k] - closes[k-1]))
                        trs.append(tr)
                    atr14 = float(np.mean(trs)) if trs else entry_px * 0.01
                    
                    risk_dist = 1.0 * atr14
                    sl_px = entry_px + risk_dist
                    tp_px = entry_px - (rr_target * risk_dist)
                    
                    sub_idx = np.searchsorted(m1_ts_list, entry_ts)
                    if sub_idx >= len(m1_rows):
                        i += 1
                        continue
                        
                    exit_px = None
                    exit_ts = None
                    r_earned = None
                    outcome = None
                    
                    # Walk up to 48 hours (2880 1m bars)
                    for m_idx in range(sub_idx, min(len(m1_rows), sub_idx + 2880)):
                        m_ts, m_o, m_h, m_l, m_c, m_v = m1_rows[m_idx]
                        
                        # Stop Loss
                        if m_h >= sl_px:
                            exit_px = sl_px
                            exit_ts = m_ts
                            r_earned = -1.0
                            outcome = "STOP LOSS"
                            break
                            
                        # Take Profit
                        if m_l <= tp_px:
                            exit_px = tp_px
                            exit_ts = m_ts
                            r_earned = rr_target
                            outcome = "TAKE PROFIT"
                            break
                            
                    if exit_px is None:
                        last_i = min(len(m1_rows)-1, sub_idx + 2879)
                        exit_px = m1_rows[last_i][4]
                        exit_ts = m1_rows[last_i][0]
                        r_earned = (entry_px - exit_px) / risk_dist
                        outcome = "TIMEOUT"
                        
                    dur_min = (exit_ts - entry_ts) / 60000.0
                    trades.append({
                        "sym": sym,
                        "entry_ts": entry_ts,
                        "exit_ts": exit_ts,
                        "r": r_earned,
                        "is_win": r_earned > 0,
                        "outcome": outcome,
                        "duration_min": dur_min
                    })
                    
                    i += 1
                else:
                    i += 1
                    
        # Concurrency & Performance Metrics
        trades.sort(key=lambda x: x["entry_ts"])
        
        # 10 Concurrent Position Cap
        executed = []
        active = {}
        for t in trades:
            e_ts = t["entry_ts"]
            for a_sym in list(active.keys()):
                if active[a_sym] <= e_ts:
                    del active[a_sym]
            if t["sym"] in active:
                continue
            if len(active) >= 10:
                continue
            executed.append(t)
            active[t["sym"]] = t["exit_ts"]
            
        n = len(executed)
        if n == 0:
            continue
            
        wins = [t for t in executed if t["is_win"]]
        losses = [t for t in executed if not t["is_win"]]
        tp_hits = [t for t in executed if t["outcome"] == "TAKE PROFIT"]
        
        win_rate = (len(wins) / n) * 100.0
        gross_p = sum(t["r"] for t in wins)
        gross_l = abs(sum(t["r"] for t in losses)) if losses else 0.001
        pf = gross_p / gross_l
        net_r = sum(t["r"] for t in executed)
        exp_r = net_r / n
        avg_dur_hrs = np.mean([t["duration_min"] for t in executed]) / 60.0
        
        cum_r = np.cumsum([t["r"] for t in executed])
        peak_r = np.maximum.accumulate(cum_r)
        max_dd = np.max(peak_r - cum_r) if len(cum_r) > 0 else 0.0
        
        all_comparison.append({
            "spike": f"{spike_mult:.0f}x",
            "rr": f"1:{rr_target:.1f}",
            "trades": n,
            "win_rate": f"{win_rate:.1f}%",
            "pf": f"{pf:.2f}",
            "net_r": f"{net_r:+.2f}R",
            "max_dd": f"{max_dd:.2f}R",
            "exp_r": f"{exp_r:+.3f}R",
            "duration": f"{avg_dur_hrs:.1f}h"
        })

    print("="*95)
    print("📊 15-MINUTE TIMEFRAME BACKTEST SUMMARY TABLE (1-MIN RESOLUTION)")
    print("="*95)
    print(f"{'SPIKE':<8} | {'RR RATIO':<10} | {'TRADES':<8} | {'WIN RATE':<10} | {'PF':<6} | {'NET RETURN':<12} | {'MAX DD':<10} | {'EXPECTANCY':<12} | {'AVG DURATION'}")
    print("-" * 95)
    for row in all_comparison:
        print(f"{row['spike']:<8} | {row['rr']:<10} | {row['trades']:<8} | {row['win_rate']:<10} | {row['pf']:<6} | {row['net_r']:<12} | {row['max_dd']:<10} | {row['exp_r']:<12} | {row['duration']}")
    print("="*95)

if __name__ == "__main__":
    main()
